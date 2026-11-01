#!/usr/bin/env python3
"""Boundary tests for predictor-only pre-development warmup construction."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_warmup import build_warmup
from research.mentor_clean import FIELDS


class WarmupDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_live_warmup_")
        self.root = Path(self.tmp.name)
        self.data = self.root / "Data"
        self.quotes = self.data / "a股指标行情"
        self.actions = self.data / "权息数据"
        self.actions.mkdir(parents=True)
        self.quotes.mkdir(parents=True)
        self.symbols = [f"{index:06d}.SZ" for index in range(1, 32)]
        self.dates = pd.bdate_range("2015-01-05", "2017-01-10").append(
            pd.DatetimeIndex(["2021-01-04"]))
        status = pd.DataFrame({"日期": self.dates.strftime("%Y-%m-%d")})
        for symbol in self.symbols:
            status[symbol] = "False"
        status.to_csv(self.data / "ST信息.csv", index=False, encoding="gb18030")
        for index, symbol in enumerate(self.symbols):
            price = 10 + index + np.arange(len(self.dates)) * 0.01
            raw = pd.DataFrame({
                "证券代码": symbol,
                "交易时间": self.dates.strftime("%Y%m%d"),
                "开盘价": price,
                "最高价": price + 0.2,
                "最低价": price - 0.2,
                "收盘价": price,
                "前收盘价": np.r_[price[0], price[:-1]],
                "成交量(手)": 10000,
                "成交额(千元)": 10000,
                "换手率（%）": 2,
                "总股本 （万股）": 10000,
                "流通股本 （万股）": 10000,
                "总市值 （万元）": 1000000,
            })
            raw.to_csv(self.quotes / f"{symbol}.csv", index=False, encoding="gb18030")
            pd.DataFrame({"日期": []}).to_csv(self.actions / f"{symbol}.csv", index=False, encoding="gb18030")

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, name="TEST_warmup_ok", weeks=8):
        return build_warmup(
            self.root / name, start="2015-01-01", end="2016-12-31", warmup_weeks=weeks,
            symbols=self.symbols, test_mode=True, data_root=self.data,
        )

    def test_bounded_arrow_history_has_no_labels_or_future_rows(self):
        manifest = self.build()
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertEqual(manifest["rows"], 31 * 8)
        self.assertEqual(manifest["warmup_weeks"], 8)
        self.assertFalse(manifest["labels_computed"])
        self.assertEqual(manifest["raw_label_columns_read"], [])
        self.assertFalse(manifest["score_window_read"])
        self.assertFalse(manifest["oos_window_read"])
        self.assertFalse(manifest["embargo_read"])
        for source in manifest["sources"].values():
            if "a股指标行情" in source["path"] or source["path"].endswith("ST信息.csv"):
                self.assertEqual(source["first_excluded_date"], "2017-01-02")
            self.assertFalse(source["full_source_table_materialized"])
            self.assertFalse(source["post_boundary_quote_values_retained"])
            self.assertFalse(source["outcome_columns_requested"])
        panel = pd.concat([pd.read_parquet(self.root / "TEST_warmup_ok" / part["path"])
                           for part in manifest["parts"]], ignore_index=True)
        self.assertLessEqual(panel.date.max(), pd.Timestamp("2016-12-31"))
        self.assertTrue(panel.forward_return.isna().all())
        self.assertTrue(panel.label_exit_date.isna().all())
        self.assertTrue(panel.label_reason.eq("NOT_READ_PREDICTOR_ONLY_WARMUP").all())
        self.assertEqual(set(panel.columns).intersection({"2021", "future_return"}), set())
        availability = manifest["availability"]
        self.assertTrue(availability["all_required_predictor_weeks_pass"])
        self.assertEqual(availability["features"]["momentum_20"]["coverage"], 1.0)
        self.assertTrue(panel.research_mask.all())

    def test_missing_st_column_is_unknown_not_false(self):
        path = self.data / "ST信息.csv"
        status = pd.read_csv(path, encoding="gb18030")
        status = status.drop(columns=[self.symbols[0]])
        status.to_csv(path, index=False, encoding="gb18030")
        manifest = self.build("TEST_warmup_missing_st")
        panel = pd.concat([pd.read_parquet(self.root / "TEST_warmup_missing_st" / part["path"])
                           for part in manifest["parts"]], ignore_index=True)
        missing = panel.symbol.eq(self.symbols[0])
        self.assertFalse(panel.loc[missing, "research_mask"].any())
        self.assertTrue(panel.loc[missing, "universe_reason"].eq("unknown_ST").all())
        self.assertTrue(panel.loc[~missing, "research_mask"].all())
        status_record = next(row for row in manifest["sources"].values()
                             if row["path"].endswith("ST信息.csv"))
        self.assertEqual(status_record["missing_symbol_columns"], [self.symbols[0]])
        self.assertEqual(status_record["missing_status_policy"], "unknown_not_false")

    def test_label_column_is_rejected_before_rows_and_failure_is_retained(self):
        path = self.quotes / "000001.SZ.csv"
        frame = pd.read_csv(path, encoding="gb18030")
        frame["forward_return"] = 0.0
        frame.to_csv(path, index=False, encoding="gb18030")
        with self.assertRaisesRegex(ValueError, "label columns are forbidden"):
            self.build("TEST_warmup_forbidden_label")
        failure = json.loads((self.root / "TEST_warmup_forbidden_label/failure.json").read_text())
        self.assertEqual(failure["raw_label_columns_read"], [])
        self.assertFalse(failure["labels_computed"])
        self.assertFalse((self.root / "TEST_warmup_forbidden_label/manifest.json").exists())

    def test_predictor_coverage_stop_does_not_zero_fill(self):
        for symbol in self.symbols:
            path = self.quotes / f"{symbol}.csv"
            frame = pd.read_csv(path, encoding="gb18030")
            frame["收盘价"] = -1
            frame.to_csv(path, index=False, encoding="gb18030")
        with self.assertRaisesRegex(RuntimeError, "coverage below 96%"):
            self.build("TEST_warmup_low_coverage")
        manifest = json.loads((self.root / "TEST_warmup_low_coverage/manifest.json").read_text())
        self.assertEqual(manifest["status"], "COVERAGE_STOP")
        self.assertFalse(manifest["availability"]["all_required_predictor_weeks_pass"])
        panel = pd.concat([pd.read_parquet(self.root / "TEST_warmup_low_coverage" / part["path"])
                           for part in manifest["parts"]], ignore_index=True)
        self.assertTrue(panel.momentum_20.isna().all())
        self.assertFalse(panel.momentum_20.eq(0).any())

    def test_nonchronological_source_fails_closed(self):
        path = self.quotes / "000001.SZ.csv"
        frame = pd.read_csv(path, encoding="gb18030")
        frame.loc[1, "交易时间"] = 19910101
        frame.to_csv(path, index=False, encoding="gb18030")
        with self.assertRaisesRegex(ValueError, "nonchronological"):
            self.build("TEST_warmup_nonchronological")
        self.assertTrue((self.root / "TEST_warmup_nonchronological/failure.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
