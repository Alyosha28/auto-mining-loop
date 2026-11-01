#!/usr/bin/env python3
"""Tests for blinded live analysis and frozen formal execution."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_analysis import (
    EVIDENCE,
    RAW,
    analyze_rows,
    analyze_run,
    paired_inference,
    sanitize_loop,
    unblind_rows,
    validate_blinded,
)
from agents.live_formal import LoopFailure, execute_run, prepare_run
from agents.live_manifest import read_json
from agents.live_qd_runner import mock_model_config


def loop_result(arm, domain, confirmed, slots=24):
    operators = ("rank", "zscore", "delta4", "delta13", "neg", "tanh")
    trajectory = []
    for index in range(slots):
        formula = "rank_minus(f00,f01)" if index == 0 else f"{operators[index % 6]}(f{index % 5:02d})"
        valid = index < confirmed
        width = 4 if slots == 24 else 2
        trajectory.append({
            "proposal_id": f"P-{index:02d}", "generation": index // width, "slot": index % width,
            "parent_id": "INITIAL-000", "parent_formula": "f00", "formula": formula,
            "status": "ARCHIVED_EVIDENCE" if valid else "SEARCH_INVALID",
            "search_valid": valid, "schema_valid": True, "coverage": 1.0,
            "signed_mean_ic": 0.02 if valid else 0.0, "icir": 0.1 if valid else 0.0,
            "search_fitness": 0.1, "direction": 1,
        })
    return {
        "arm": arm, "trusted_archive": arm == EVIDENCE,
        "status": "COMPLETED", "not_run_slots": 0, "attempted_slots": slots,
        "proposal_slots": slots, "archive_cells": 1, "nominated": confirmed,
        "audit_confirmed_unique_discoveries": confirmed,
        "audit_confirmed_per_slot": confirmed / slots,
        "audit_confirmed_unique_per_24_slots": confirmed * 24 / slots,
        "trajectory": trajectory,
        "audit": [{"audit_confirmed": True, "formula": "rank_minus(f00,f01)",
                   "signed_mean_ic": 0.03, "icir": 0.2, "coverage": 1.0} for _ in range(confirmed)],
        "independent_recompute": {"consistent": True, "n_checks": confirmed, "n_consistent": confirmed},
        "cost": {
            "wall_seconds": 1.0, "cpu_seconds": 0.1,
            "llm": {"logical_calls": slots, "actual_calls": slots, "non_cached_calls": slots,
                    "cached_calls": 0, "cache_hits": 0, "infrastructure_retries": 0,
                    "prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            "evaluator_calls": {"search_initial": 1, "search_proposals": slots,
                                "audit": confirmed, "recompute": confirmed, "total": 1 + slots + 2 * confirmed},
        },
        "random_fallback_used": False, "audit_feedback_applied": False,
        "real_data_read": domain == "real_dev", "score_window_read": False,
        "oos_window_read": False, "embargo_read": False, "independent_oos": False,
        "proposal_status_counts": ({"ARCHIVED_EVIDENCE": confirmed, "SEARCH_INVALID": slots - confirmed}
                                   if confirmed else {"SEARCH_INVALID": slots}),
    }


def block(domain, task, block_id, cluster, evidence=1, raw=0, swap=False):
    key = f"{domain}/{task}/{block_id}"
    arms = {"A": {"result": sanitize_loop(loop_result(EVIDENCE, domain, evidence))},
            "B": {"result": sanitize_loop(loop_result(RAW, domain, raw))}}
    mapping = {"A": EVIDENCE, "B": RAW}
    if swap:
        arms = {"A": arms["B"], "B": arms["A"]}
        mapping = {"A": RAW, "B": EVIDENCE}
    payload = {"domain": domain, "task": task, "block_id": block_id,
               "cluster_id": cluster, "arms": arms,
               "initial_library": [f"f{index:02d}" for index in range(5)],
               "feature_names": [f"f{index:02d}" for index in range(5)]}
    return key, payload, mapping


class PairedAnalysisTests(unittest.TestCase):
    def test_stratified_statistics_are_deterministic_and_weight_strata(self):
        first = paired_inference([1, 1, 2], ["a", "a", "b"], 17, 19, n_resamples=1000)
        second = paired_inference([1, 1, 2], ["a", "a", "b"], 17, 19, n_resamples=1000)
        self.assertEqual(first, second)
        self.assertEqual(first["delta_per_slot"], 1.5)
        self.assertEqual(first["analysis_units"], 3)
        self.assertEqual(first["n_resamples"], 1000)

    def test_blinded_full_design_primary_real_and_null(self):
        blocks = []
        mapping = {}
        for task_index, task in enumerate(("planted", "weak", "nonlinear", "regime_state")):
            for index in range(8):
                key, row, arms = block("synthetic", task, f"block_{index:03d}", task, swap=(index % 2 == 1))
                blocks.append(row)
                mapping[key] = arms
        for index in range(8):
            key, row, arms = block("synthetic", "null", f"block_{index:03d}", "null",
                                   evidence=int(index < 2), raw=int(index < 3), swap=index % 2 == 1)
            blocks.append(row)
            mapping[key] = arms
        for fold in range(8):
            for replicate in range(4):
                key, row, arms = block("real_dev", "real_dev", f"fold_{fold:02d}_pair_{replicate:02d}",
                                       f"fold_{fold:02d}", swap=replicate % 2 == 1)
                blocks.append(row)
                mapping[key] = arms
        payload = {"blocks": blocks}
        validation = validate_blinded(payload, set(mapping))
        self.assertEqual(validation["n_blocks"], 72)
        self.assertEqual(validation["n_loops"], 144)
        result = analyze_rows(unblind_rows(payload, mapping))
        primary = result["synthetic_primary"]
        self.assertEqual(primary["n_matched_pairs"], 32)
        self.assertEqual(primary["delta_per_24_slots"], 1.0)
        self.assertEqual(primary["ci95_per_24_slots"], [1.0, 1.0])
        self.assertLess(primary["p_value_two_sided"], 0.001)
        self.assertEqual(result["real_dev"]["temporal_clusters"], 8)
        self.assertEqual(result["real_dev"]["n_matched_pairs"], 32)
        self.assertEqual(result["real_dev"]["independent_market_samples"], 1)
        self.assertEqual(result["real_dev"]["delta_per_24_slots"], 1.0)
        exploratory = result["exploratory"]["synthetic"]["planted"][EVIDENCE]
        self.assertEqual(exploratory["mechanism_novel_discoveries"], 8)
        self.assertEqual(exploratory["mean_budget_to_first_valid"], 1.0)
        self.assertEqual(exploratory["mean_cumulative_search_valid_by_slot"], [1.0] * 24)
        self.assertEqual(exploratory["mean_search_curve_auc"], 1.0 / 24)
        self.assertEqual(exploratory["mean_max_parent_reuse"], 24.0)
        self.assertEqual(exploratory["minimum_coverage"], 1.0)
        real_secondary = result["exploratory"]["real_dev"]["real_dev"][EVIDENCE]
        self.assertEqual(real_secondary["mechanism_novel_discoveries"], 32)
        self.assertEqual(real_secondary["mean_budget_to_first_audit_confirmed"], 1.0)
        self.assertEqual([row["false_declaration_loops"] for row in result["null_negative_control"]], [2, 3])
        self.assertFalse(result["independent_real_data_oos"])

    def test_blinding_removes_arm_specific_fields_and_rejects_incomplete(self):
        _, row, _ = block("synthetic", "planted", "block_000", "planted")
        self.assertNotIn("arm", row["arms"]["A"]["result"])
        self.assertNotIn("trusted_archive", row["arms"]["A"]["result"])
        self.assertEqual(row["arms"]["A"]["result"]["proposal_status_counts"], {"EVALUATED": 24})
        self.assertTrue(all(item["status"] == "EVALUATED"
                            for item in row["arms"]["A"]["result"]["trajectory"]))
        row["arms"]["A"]["result"]["status"] = "COVERAGE_STOP"
        with self.assertRaisesRegex(ValueError, "incomplete loop"):
            validate_blinded({"blocks": [row]})


class FormalExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_live_formal_")
        self.root = Path(self.tmp.name)
        self.model_patch = mock.patch("agents.live_formal.live_model_config", return_value=mock_model_config())
        self.check_patch = mock.patch("agents.live_formal._check_model", return_value=None)
        self.model_patch.start()
        self.check_patch.start()

    def tearDown(self):
        self.check_patch.stop()
        self.model_patch.stop()
        self.tmp.cleanup()

    def test_mock_subprocess_two_stage_blinded_execution(self):
        run_id = "TEST_formal_mock"
        prepare_run(run_id, self.root, test_smoke=True, workers=2)
        run_dir = self.root / run_id
        receipt = execute_run(run_dir)
        self.assertEqual(receipt["status"], "COMPLETED")
        self.assertEqual(receipt["n_loops_completed"], 4)
        result = analyze_run(run_dir)
        self.assertEqual(result["validation"]["n_loops"], 4)
        self.assertFalse(result["primary_analysis_performed"])
        self.assertTrue(result["effect_estimation_from_smoke_forbidden"])
        self.assertTrue(result["blinding"]["provenance_verified"])
        self.assertEqual(result["cost"]["llm"]["logical_calls"], 16)
        self.assertEqual(result["cost"]["llm"]["cache_hits"], 0)
        self.assertEqual(result["cost"]["evaluated_proposals"], 16)
        self.assertEqual(result["cost"]["schema_ast_valid_evaluated_proposals"], 16)
        self.assertEqual(result["cost"]["schema_invalid_evaluated_rate"], 0.0)
        for block in read_json(run_dir / "blinded_results.json")["blocks"]:
            for record in block["arms"].values():
                self.assertNotIn("arm", record["result"])
                self.assertNotIn("trusted_archive", record["result"])

    def test_initial_libraries_are_mechanism_declared_not_outcome_selected(self):
        from agents.live_formal import _synthetic_blocks
        run_dir = self.root / "initial_library"
        run_dir.mkdir()
        blocks = _synthetic_blocks("TEST_initial", mock_model_config(), ("regime_state",), 1,
                                   2, 2, 40, True, run_dir)
        spec = read_json(blocks[0]["spec_paths"][EVIDENCE])
        self.assertEqual(spec["initial_library"],
                         [f"product(f{index:02d},f_state)" for index in range(5)])
        prepare_run("TEST_initial_planted", self.root, test_smoke=True, workers=1)
        design = read_json(self.root / "TEST_initial_planted/design.json")
        path = next(iter(design["synthetic_spec_hashes"]))
        self.assertEqual(read_json(path)["initial_library"], [f"f{index:02d}" for index in range(5)])

    def test_run_spec_tampering_stops_before_model_calls(self):
        run_id = "TEST_formal_tamper"
        prepare_run(run_id, self.root, test_smoke=True, workers=1)
        run_dir = self.root / run_id
        design = read_json(run_dir / "design.json")
        path = next(iter(design["synthetic_spec_hashes"]))
        payload = read_json(path)
        payload["generations"] = 1
        Path(path).write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "run spec hash mismatch"):
            execute_run(run_dir)
        self.assertTrue((run_dir / "execution_incomplete.json").is_file())
        self.assertFalse(list(run_dir.glob("synthetic/**/completion.json")))
        self.assertFalse(read_json(run_dir / "execution_incomplete.json")["primary_analysis_performed"])

    def test_coverage_stop_latch_prevents_new_work_on_resume(self):
        run_id = "TEST_formal_coverage_latch"
        prepare_run(run_id, self.root, test_smoke=True, workers=2)
        run_dir = self.root / run_id
        design = read_json(run_dir / "design.json")
        spec_path = next(iter(design["synthetic_spec_hashes"]))
        completion = Path(spec_path).parent / "completion.json"
        completion.write_text(json.dumps({"status": "COVERAGE_STOP", "coverage_stop": {"coverage": 0.5}}),
                              encoding="utf-8")
        with mock.patch("agents.live_formal._run_block") as runner:
            with self.assertRaisesRegex(LoopFailure, "coverage stop latched"):
                execute_run(run_dir)
            runner.assert_not_called()
        receipt = read_json(run_dir / "execution_incomplete.json")
        self.assertEqual(receipt["status"], "COVERAGE_STOP")
        self.assertFalse(receipt["primary_analysis_performed"])

        run_id = "TEST_formal_coverage_operation_latch"
        prepare_run(run_id, self.root, test_smoke=True, workers=2)
        run_dir = self.root / run_id
        design = read_json(run_dir / "design.json")
        spec_path = next(iter(design["synthetic_spec_hashes"]))
        operation = Path(spec_path).parent / "operations.jsonl"
        operation.write_text(json.dumps({"kind": "search_proposals", "operation_id": "P-G00-S00",
                                        "event": "finished", "error_type": "CoverageStop"}) + "\n",
                             encoding="utf-8")
        with mock.patch("agents.live_formal._run_block") as runner:
            with self.assertRaisesRegex(LoopFailure, "coverage stop latched"):
                execute_run(run_dir)
            runner.assert_not_called()
        self.assertEqual(read_json(run_dir / "execution_incomplete.json")["status"], "COVERAGE_STOP")


if __name__ == "__main__":
    unittest.main(verbosity=2)
