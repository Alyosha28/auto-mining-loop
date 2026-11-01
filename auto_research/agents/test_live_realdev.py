#!/usr/bin/env python3
"""Synthetic-fixture integration tests for dev-only real-data loop inputs."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_research"))
from agents.expr_grammar import parse
from agents.live_evaluator import LiveEvaluator
from agents.live_manifest import read_json, sha256_file, write_immutable_json
from agents.live_qd_runner import RAW, LiveLoopSpec, bundle_from_spec, resume_live_loop, run_live_loop
from agents.live_realdev import load_real_dev_bundle, make_fold_calendar, materialize_real_fold
from agents.live_data import load_real_dev_snapshot
from agents.live_qd_runner import make_test_client_factory


class RealDevInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_live_realdev_")
        self.root = Path(self.tmp.name)
        self.features = ("momentum_20", "volatility_20", "turnover_mean_20", "volume_ratio_5_20")
        self.symbols = [f"S{index:03d}" for index in range(31)]
        self._make_warmup()
        self._make_source()
        self.calendar = make_fold_calendar(self.panel_dir, trading_dates=self.trading_dates, test_only=True)
        self.calendar_path = self.root / "calendar.json"
        write_immutable_json(self.calendar_path, self.calendar)
        self.config = materialize_real_fold(
            self.calendar_path, self.warmup_dir, self.root / "fold_00", self.features,
            panel_dir=self.panel_dir, authorized_roots=[self.root / "Data"], fold_id="fold_00",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _make_warmup(self):
        self.warmup_dir = self.root / "warmup"
        self.warmup_dir.mkdir(parents=True)
        dates = pd.date_range(end="2016-12-30", periods=312, freq="W-FRI")
        rng = np.random.default_rng(17)
        frames = []
        for date in dates:
            values = rng.normal(size=(31, 4))
            frame = pd.DataFrame(values, columns=self.features)
            frame.insert(0, "symbol", self.symbols)
            frame.insert(0, "date", date)
            frame["research_mask"] = True
            frame["log_market_cap"] = rng.normal(size=31)
            frame["universe_reason"] = "eligible"
            frame["status_source_date"] = str(date.date())
            frame["observed_listing_age"] = 100
            frame["forward_return"] = np.nan
            frame["label_reason"] = "NOT_READ_PREDICTOR_ONLY_WARMUP"
            frame["label_entry_date"] = None
            frame["label_exit_date"] = pd.NaT
            frames.append(frame)
        panel = pd.concat(frames, ignore_index=True)
        panel.to_parquet(self.warmup_dir / "part-0000.parquet", index=False)
        write_immutable_json(self.warmup_dir / "manifest.json", {
            "kind": "real_dev_warmup", "status": "COMPLETED", "test_only": True,
            "feature_names": list(self.features),
            "parts": [{"path": "part-0000.parquet",
                       "sha256": sha256_file(self.warmup_dir / "part-0000.parquet")}],
            "raw_label_columns_read": [], "labels_computed": False,
            "forward_return_all_missing": True,
            "score_window_read": False, "oos_window_read": False, "embargo_read": False,
        })

    def _make_source(self):
        self.signal_dates = pd.date_range("2017-01-06", periods=200, freq="W-FRI")
        self.trading_dates = sorted({str(date.date()) for date in self.signal_dates}
                                    | {str((date + pd.Timedelta(days=1)).date()) for date in self.signal_dates}
                                    | {"2020-11-30"})
        rng = np.random.default_rng(29)
        frames = []
        for index, date in enumerate(self.signal_dates):
            values = rng.normal(size=(31, 4))
            frame = pd.DataFrame(values, columns=self.features)
            frame.insert(0, "symbol", self.symbols)
            frame.insert(0, "date", str(date.date()))
            frame["research_mask"] = True
            frame["log_market_cap"] = rng.normal(size=31)
            frame["universe_reason"] = "eligible"
            frame["status_source_date"] = str((date - pd.Timedelta(days=1)).date())
            frame["observed_listing_age"] = 100
            frame["forward_return"] = 0.01 * values[:, 0] + rng.normal(0, 0.02, 31)
            frame["label_reason"] = "valid_price_diagnostic"
            next_signal = self.signal_dates[index + 1] if index + 1 < len(self.signal_dates) else date + pd.Timedelta(days=7)
            frame["label_entry_date"] = str((date + pd.Timedelta(days=1)).date())
            frame["label_exit_date"] = str((next_signal + pd.Timedelta(days=1)).date())
            frames.append(frame)
        for date in ("2021-01-08", "2024-01-05"):
            future = frames[0].copy()
            future["date"] = date
            future["label_entry_date"] = date
            future["label_exit_date"] = date
            frames.append(future)
        self.panel_dir = self.root / "Data/panel"
        self.panel_dir.mkdir(parents=True)
        pd.concat(frames, ignore_index=True).to_parquet(self.panel_dir / "part-0000.parquet", index=False)
        write_immutable_json(self.panel_dir / "manifest.json", {
            "signal_dates": [str(date.date()) for date in self.signal_dates],
            "parts": [{"path": "part-0000.parquet",
                       "sha256": sha256_file(self.panel_dir / "part-0000.parquet")}],
        })

    def test_calendar_lazy_audit_and_full_real_spec_two_stage(self):
        self.assertEqual(len(self.calendar["folds"][0]["search_dates"]), 23)
        self.assertTrue(all(len(fold["audit_dates"]) == 22 for fold in self.calendar["folds"]))
        self.assertEqual(self.calendar["unused_dev_signal_dates"], [str(self.signal_dates[-1].date())])
        self.assertLessEqual(self.calendar["folds"][-1]["audit_label_end"], "2020-11-30")
        bundle = load_real_dev_bundle(self.config)
        self.assertEqual(bundle.audit_panels, {})
        self.assertEqual(len(bundle.search_dates), 23)
        self.assertLess(bundle.search_panel.date.min(), pd.Timestamp("2017-01-01"))
        self.assertTrue(bundle.manifest["real_data_read"])
        self.assertFalse(bundle.manifest["independent_evaluation_eligible"])
        with mock.patch("agents.live_realdev._load_snapshot", wraps=__import__(
                "agents.live_realdev", fromlist=["_load_snapshot"])._load_snapshot) as loader:
            audit_panel = bundle.get_audit_panel(0)
            self.assertEqual(loader.call_count, 1)
            bundle.get_audit_panel(0)
            self.assertEqual(loader.call_count, 1)
        history = audit_panel.loc[audit_panel.date.isin(bundle.search_dates)]
        self.assertTrue(history.forward_return.isna().all())
        evaluator = LiveEvaluator(assumption_label="real_terminal_label")
        node = parse("delta13(momentum_20)", set(self.features))
        search = evaluator.search(node, bundle.search_panel, bundle.search_dates)
        self.assertEqual(search["coverage"], 1.0)
        for dates in bundle.audit_folds[0].values():
            audit = evaluator.audit(node, audit_panel, dates, search["direction"])
            self.assertEqual(audit["coverage"], 1.0)

        spec = LiveLoopSpec(
            run_id="TEST_realdev", task="real_dev", block_id="fold_00_pair_00", arm=RAW,
            agent_seed=17, search_seed=0, audit_seeds=(0,), generations=1, slots_per_generation=1,
            initial_library=self.features, n_symbols=None, n_weeks=22,
            n_features=len(self.features), feature_names=self.features,
            data_config=self.config, test_mode=True,
        )
        script = [{"expression": "rank(momentum_20)", "rationale": "Rank the same price mechanism.",
                   "expected_ic": 0.02, "falsification": "Signed audit IC is below 0.02."}]
        arm_dir = self.root / "arm"
        frozen = run_live_loop(spec, bundle_from_spec(spec), make_test_client_factory(script)(),
                               arm_dir, stop_before_audit=True)
        self.assertEqual(frozen["status"], "NOMINATIONS_FROZEN")
        self.assertGreater(frozen["nominated"], 0)
        self.assertFalse(frozen["real_data_read"] is False)
        result = resume_live_loop(arm_dir, llm=make_test_client_factory(script)(), audit_only=True)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["attempted_slots"], 1)
        self.assertTrue(result["independent_recompute"]["consistent"])
        self.assertTrue(result["real_data_read"])
        self.assertFalse(result["score_window_read"])
        self.assertFalse(result["oos_window_read"])
        self.assertFalse(result["embargo_read"])
        self.assertFalse(result["independent_oos"])
        manifest = read_json(arm_dir / "manifest.json")
        self.assertTrue(manifest["real_data_read"])
        self.assertFalse(manifest["score_window_read"])

    def test_second_fold_and_tampering_are_not_silent(self):
        config = materialize_real_fold(
            self.calendar_path, self.warmup_dir, self.root / "fold_01", self.features,
            panel_dir=self.panel_dir, authorized_roots=[self.root / "Data"], fold_id="fold_01",
        )
        self.assertEqual(config["fold_id"], "fold_01")
        bundle = load_real_dev_bundle(config)
        self.assertEqual(len(bundle.search_dates), 45)
        panel_path = self.root / "fold_01/search/panel.parquet"
        frame = pd.read_parquet(panel_path)
        frame.loc[0, "momentum_20"] = 999
        frame.to_parquet(panel_path, index=False)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            load_real_dev_bundle(config)

    def test_closed_windows_and_unregistered_formal_materialization_are_refused(self):
        with self.assertRaisesRegex(ValueError, "restricted"):
            load_real_dev_snapshot(self.panel_dir, self.features, start="2017-01-01", end="2017-06-09",
                                   label_end="2021-01-01", authorized_roots=[self.root / "Data"])
        calendar = dict(self.calendar)
        calendar["test_only"] = False
        path = self.root / "formal_calendar.json"
        write_immutable_json(path, calendar)
        with self.assertRaisesRegex(PermissionError, "frozen preregistration"):
            materialize_real_fold(path, self.warmup_dir, self.root / "unregistered", self.features,
                                  panel_dir=self.panel_dir, authorized_roots=[self.root / "Data"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
