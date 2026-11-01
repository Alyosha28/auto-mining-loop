from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from research.research_governance import (
    GovernanceLedger,
    adjust_pvalues,
    audit_information_times,
    block_bootstrap_mean_pvalue,
    final_holdout_region,
    validate_input_manifest,
    validate_submission,
)


def digest(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def input_manifest():
    return {
        "schema_version": "RESEARCH-INPUT-MANIFEST-1",
        "dataset_version": "synthetic-v1",
        "universe_version": "pit-v1",
        "factor_code_hash": digest("code"),
        "preprocessing_hash": digest("preprocess"),
        "label_definition": "next-week open-to-open after signal",
        "execution_lag": "next trading day open",
        "calendar_version": "calendar-v1",
        "environment_version": "python-test",
        "availability_policy": "publication and arrival no later than signal",
        "random_seed": 7,
        "dependency_versions": {"numpy": "test"},
        "source_files_sha256": {"synthetic.csv": digest("source")},
    }


def submission(manifest_hash: str):
    return {
        "schema_version": "RESEARCH-SUBMISSION-1",
        "submission_id": "submit-001",
        "claim_family_id": "gap-repair",
        "claim": "The frozen score has positive incremental weekly RankIC.",
        "intended_use": "incremental",
        "factor_ids": ["factor_a", "factor_b"],
        "input_manifest_hash": manifest_hash,
        "definition_bundle_hash": digest("definitions"),
        "validation_region_id": "final-2027",
        "expected_sign": 1,
        "primary_metric": "rank_ic",
        "secondary_metrics": ["coverage"],
        "baselines": ["core_cluster_score"],
        "decision_rule": {
            "adjusted_p_max": 0.10,
            "effect_direction": 1,
            "all_frozen_claims_must_pass": True,
        },
        "evidence_rule": {
            "method": "bh",
            "alpha": 0.10,
            "test_family_id": "family-001",
            "family_members": ["factor_a", "factor_b"],
            "null_construction": "zero mean paired weekly RankIC gain",
            "resampling_scheme": "predeclared four-week blocks",
            "pvalue_source": "block_bootstrap_mean",
            "block_length": 4,
            "repetitions": 400,
            "seed": 20260921,
        },
        "response_package": {
            "metrics": ["rank_ic", "coverage"],
            "granularities": ["aggregate"],
            "max_feedback_rounds": 1,
            "replacement_policy": "none",
            "evidence_fields": ["adjusted_pvalues", "decisions"],
        },
    }


def fresh_state():
    return {
        "schema_version": "RESEARCH-EXPOSURE-3",
        "intervals": [{
            "region_id": "final-2027", "start": "2027-01-01", "end": "2027-12-31",
            "status": "fresh_holdout", "selection_access": False,
            "outcome_access": False,
        }],
    }


class ManifestAndTimeTests(unittest.TestCase):
    def test_manifest_is_strict_and_hashes_identity(self):
        value = validate_input_manifest(input_manifest())
        self.assertRegex(value["manifest_hash"], r"^[0-9a-f]{64}$")
        broken = input_manifest(); broken["random_seed"] = True
        with self.assertRaisesRegex(ValueError, "random_seed"):
            validate_input_manifest(broken)

    def test_information_time_audit_rejects_late_and_naive_times(self):
        record = {
            "event_time": "2026-04-01T00:00:00+08:00",
            "publication_time": "2026-04-30T14:00:00+08:00",
            "vendor_arrival_time": "2026-04-30T14:03:00+08:00",
            "research_available_time": "2026-04-30T14:05:00+08:00",
            "signal_time": "2026-04-30T15:00:00+08:00",
            "execution_time": "2026-05-06T09:30:00+08:00",
        }
        self.assertEqual(audit_information_times(record)["status"], "eligible_at_signal_time")
        late = dict(record, research_available_time="2026-04-30T15:01:00+08:00")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            audit_information_times(late)
        naive = dict(record, publication_time="2026-04-30T14:00:00")
        with self.assertRaisesRegex(ValueError, "timezone"):
            audit_information_times(naive)


class SubmissionAndExposureTests(unittest.TestCase):
    def setUp(self):
        self.manifest = validate_input_manifest(input_manifest())
        self.contract = submission(self.manifest["manifest_hash"])

    def test_submission_freezes_metrics_and_evidence_rule(self):
        value = validate_submission(self.contract)
        self.assertRegex(value["submission_hash"], r"^[0-9a-f]{64}$")
        broken = submission(self.manifest["manifest_hash"])
        broken["response_package"]["metrics"].append("monthly_pnl")
        with self.assertRaisesRegex(ValueError, "response metrics"):
            validate_submission(broken)

    def test_only_explicit_unexposed_region_is_final(self):
        self.assertEqual(final_holdout_region(fresh_state(), "final-2027")["status"],
                         "fresh_holdout")
        exposed = fresh_state(); exposed["intervals"][0]["status"] = "already_viewed"
        with self.assertRaisesRegex(ValueError, "exposed"):
            final_holdout_region(exposed, "final-2027")
        accessed = fresh_state(); accessed["intervals"][0]["outcome_access"] = True
        with self.assertRaisesRegex(ValueError, "deny prior"):
            final_holdout_region(accessed, "final-2027")

    def test_current_project_state_cannot_be_final_holdout(self):
        import json
        root = Path(__file__).resolve().parents[1]
        state = json.loads((root / "config/research_exposure_state_current.json").read_text())
        for region in ("development", "historical_review", "released_2023_plus"):
            with self.assertRaises(ValueError):
                final_holdout_region(state, region)


class AdjustmentTests(unittest.TestCase):
    def test_bh_and_by_are_deterministic_and_monotone(self):
        p = {"a": 0.01, "b": 0.04, "c": 0.03}
        self.assertEqual(adjust_pvalues(p, "none"), p)
        bh = adjust_pvalues(p, "bh")
        by = adjust_pvalues(p, "by")
        self.assertAlmostEqual(bh["a"], 0.03)
        self.assertAlmostEqual(bh["b"], 0.04)
        self.assertTrue(all(by[k] >= bh[k] for k in p))

    def test_invalid_pvalues_fail(self):
        for value in (-0.1, 1.1, float("nan"), True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    adjust_pvalues({"a": value}, "bh")

    def test_block_bootstrap_is_deterministic_and_detects_large_mean(self):
        values = [0.08 + (i % 4) * 0.001 for i in range(80)]
        first = block_bootstrap_mean_pvalue(
            values, block_length=4, repetitions=400, seed=17)
        second = block_bootstrap_mean_pvalue(
            values, block_length=4, repetitions=400, seed=17)
        self.assertEqual(first, second)
        self.assertLess(first["pvalue"], 0.02)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.ledger = GovernanceLedger(Path(self.temporary.name) / "governance.sqlite3")
        manifest = validate_input_manifest(input_manifest())
        self.contract = validate_submission(submission(manifest["manifest_hash"]))
        self.frozen = self.ledger.freeze(self.contract)

    def tearDown(self):
        self.ledger.close(); self.temporary.cleanup()

    def test_feedback_is_bounded_and_extra_metrics_are_logged_denied(self):
        allowed = self.ledger.authorize_feedback(
            self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        response = self.ledger.record_feedback(allowed["feedback_id"], {"rank_ic": 0.02})
        self.assertEqual(response["decision"], "returned")
        with self.assertRaises(PermissionError):
            self.ledger.authorize_feedback(
                self.frozen["submission_hash"], fresh_state(), ["monthly_pnl"], "aggregate")
        with self.assertRaises(PermissionError):
            self.ledger.authorize_feedback(
                self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        rows = self.ledger.connection.execute(
            "SELECT decision,reason FROM feedback ORDER BY feedback_id").fetchall()
        self.assertEqual([x["decision"] for x in rows], ["returned", "denied", "denied"])

    def test_response_cannot_exceed_authorization(self):
        allowed = self.ledger.authorize_feedback(
            self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.ledger.record_feedback(
                allowed["feedback_id"], {"rank_ic": 0.02, "coverage": 0.9})

    def test_calibration_requires_returned_feedback_and_exact_family(self):
        with self.assertRaisesRegex(ValueError, "returned feedback"):
            self.ledger.calibrate(
                self.frozen["submission_hash"], {"factor_a": 0.01, "factor_b": 0.02})
        access = self.ledger.authorize_feedback(
            self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        self.ledger.record_feedback(access["feedback_id"], {"rank_ic": 0.02})
        with self.assertRaisesRegex(ValueError, "exactly"):
            self.ledger.calibrate(self.frozen["submission_hash"], {"factor_a": 0.01})

    def test_calibration_and_scoped_release(self):
        access = self.ledger.authorize_feedback(
            self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        self.ledger.record_feedback(access["feedback_id"], {"rank_ic": 0.08})
        evidence = self.ledger.calibrate(
            self.frozen["submission_hash"], {"factor_a": 0.01, "factor_b": 0.02})
        self.assertTrue(all(evidence["decisions"].values()))
        released = self.ledger.release(self.frozen["submission_hash"], ["incremental"])
        self.assertEqual(released["allowed_uses"], ["incremental"])
        self.assertFalse(released["production_ready"])
        self.assertEqual(self.ledger.audit()["status"], "PASS")

    def test_release_refuses_failed_primary_claim(self):
        access = self.ledger.authorize_feedback(
            self.frozen["submission_hash"], fresh_state(), ["rank_ic"], "aggregate")
        self.ledger.record_feedback(access["feedback_id"], {"rank_ic": 0.01})
        self.ledger.calibrate(
            self.frozen["submission_hash"], {"factor_a": 0.01, "factor_b": 0.5})
        with self.assertRaisesRegex(ValueError, "did not pass"):
            self.ledger.release(self.frozen["submission_hash"], ["incremental"])

    def test_multi_parent_lineage_and_missing_parent_rejection(self):
        root = self.ledger.register_lineage(
            "root_a", digest("a"), "hypothesis-a", "formula-a", [], "seed")
        self.assertEqual(root["parents"], [])
        self.ledger.register_lineage(
            "root_b", digest("b"), "hypothesis-b", "formula-b", [], "seed")
        child = self.ledger.register_lineage(
            "child", digest("c"), "hypothesis-c", "formula-c",
            ["root_a", "root_b"], "interaction")
        self.assertEqual(child["parents"], ["root_a", "root_b"])
        with self.assertRaisesRegex(ValueError, "registered first"):
            self.ledger.register_lineage(
                "orphan", digest("d"), "hypothesis-d", "formula-d",
                ["missing"], "mutation")


if __name__ == "__main__":
    unittest.main()
