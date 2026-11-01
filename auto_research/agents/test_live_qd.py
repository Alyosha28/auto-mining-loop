#!/usr/bin/env python3
"""Fast synthetic/mock tests for the live QD smoke infrastructure."""
from __future__ import annotations

import json
import random
import shutil
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
from agents.live_evaluator import CoverageStop, LiveEvaluator
from agents.live_llm import LiveLLMClient, TransientLLMError
from agents.live_manifest import ResumeError, read_json, read_jsonl
from agents.live_qd_runner import (
    ARMS,
    EVIDENCE,
    RAW,
    LiveLoopSpec,
    _LiveLoop,
    bundle_from_spec,
    default_test_script,
    live_model_config,
    make_test_client_factory,
    resume_live_loop,
    run_live_loop,
    run_matched_smoke,
)


def make_spec(arm=EVIDENCE, run_id="TEST_unit", agent_seed=19, generations=2, slots=2,
              audit_seeds=(201, 301), weeks=24):
    return LiveLoopSpec(
        run_id=run_id,
        task="planted",
        block_id="block_000",
        arm=arm,
        agent_seed=agent_seed,
        search_seed=101,
        audit_seeds=audit_seeds,
        generations=generations,
        slots_per_generation=slots,
        n_symbols=36,
        n_weeks=weeks,
        n_features=8,
        n_audit_folds=2,
        nomination_cap=4,
    )


def signed_panel(beta: float, seed: int, weeks: int = 24, symbols: int = 36) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    frames = []
    for date in pd.date_range("2017-01-06", periods=weeks, freq="W-FRI"):
        features = rng.standard_normal((symbols, 2))
        frame = pd.DataFrame(features, columns=["f00", "f01"])
        frame.insert(0, "forward_return", beta * features[:, 0] + rng.standard_normal(symbols) * 0.05)
        frame.insert(0, "research_mask", True)
        frame.insert(0, "log_market_cap", rng.standard_normal(symbols))
        frame.insert(0, "symbol", [f"S{index:03d}" for index in range(symbols)])
        frame.insert(0, "date", date)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


class LiveLLMTests(unittest.TestCase):
    def test_loopback_retry_costs_and_instance_cache(self):
        calls = []

        def transport(url, payload, headers, timeout):
            calls.append((url, payload, headers))
            if len(calls) == 1:
                raise TransientLLMError("TEST transient")
            return {
                "id": "TEST", "model": payload["model"],
                "choices": [{"message": {"content": "{\"expression\":\"f00\"}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            }

        client = LiveLLMClient(transport=transport, max_retries=1, retry_backoff=0)
        messages = [{"role": "user", "content": "TEST"}]
        response = client.chat(messages)
        self.assertEqual(response.actual_calls, 2)
        self.assertEqual(response.infrastructure_retries, 1)
        self.assertEqual(response.tokens["total_tokens"], 5)
        self.assertEqual(len(response.raw_hash), 64)
        self.assertNotIn("Authorization", calls[0][2])
        self.assertTrue(all(call[0].startswith("http://127.0.0.1:1234/v1/") for call in calls))
        again = client.chat(messages)
        self.assertEqual(again.cache_hits, 0)
        self.assertEqual(client.get_stats()["actual_calls"], 3)
        self.assertEqual(client.get_stats()["cache_hits"], 0)

        cached_client = LiveLLMClient(transport=transport, max_retries=0, retry_backoff=0,
                                      cache_enabled=True, cache_scope="TEST_run/block/arm")
        cached_client.chat(messages)
        cached = cached_client.chat(messages)
        self.assertTrue(cached.cached)
        self.assertEqual(cached.actual_calls, 0)
        self.assertEqual(cached.tokens["total_tokens"], 0)
        self.assertEqual(cached_client.get_stats()["cache_hits"], 1)
        separate = LiveLLMClient(transport=transport, max_retries=0, retry_backoff=0,
                                 cache_enabled=True, cache_scope="TEST_run/block/arm")
        separate.chat(messages)
        self.assertEqual(separate.get_stats()["cache_hits"], 0)
        for endpoint in ("https://127.0.0.1:1234/v1", "http://localhost:1234/v1",
                         "http://example.com/v1", "http://user@127.0.0.1:1234/v1"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                LiveLLMClient(base_url=endpoint, transport=transport)

    def test_reasoning_only_retry_preserves_raw_attempt_and_decoding_config(self):
        calls = []

        def transport(url, payload, headers, timeout):
            calls.append(payload)
            if len(calls) == 1:
                return {
                    "id": "TEST-empty", "model": payload["model"],
                    "choices": [{"message": {"content": "", "reasoning_content": "TEST raw reasoning"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
                }
            return {
                "id": "TEST-success", "model": payload["model"],
                "choices": [{"message": {"content": "{\"expression\":\"f00\"}"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
            }

        client = LiveLLMClient(transport=transport, max_retries=1, retry_backoff=0,
                               reasoning_effort="low")
        response = client.chat([{"role": "user", "content": "TEST"}])
        self.assertEqual(calls[0]["reasoning_effort"], "low")
        self.assertEqual(response.actual_calls, 2)
        failed = response.attempts[0]
        self.assertEqual(failed["raw_response"]["choices"][0]["message"]["reasoning_content"],
                         "TEST raw reasoning")
        self.assertEqual(failed["usage"]["total_tokens"], 5)
        self.assertEqual(len(failed["raw_hash"]), 64)
        self.assertEqual(response.tokens["total_tokens"], 9)
        self.assertEqual(client.seed_capabilities()["reasoning_effort"], "low")


class EvaluatorTests(unittest.TestCase):
    def test_raw_direction_is_frozen_and_audit_cannot_reflip(self):
        search_panel = signed_panel(-0.5, seed=1)
        audit_panel = signed_panel(0.5, seed=2)
        dates = pd.DatetimeIndex(sorted(search_panel.date.unique()))
        node = parse("f00", {"f00", "f01"})
        evaluator = LiveEvaluator()
        search = evaluator.search(node, search_panel, dates)
        self.assertLess(search["raw_mean_ic"], 0)
        self.assertEqual(search["direction"], -1)
        self.assertGreater(search["signed_mean_ic"], 0)
        audit = evaluator.audit(node, audit_panel, dates, search["direction"])
        self.assertGreater(audit["raw_mean_ic"], 0)
        self.assertEqual(audit["direction"], -1)
        self.assertEqual(audit["direction_source"], "frozen_search_direction")
        self.assertLess(audit["signed_mean_ic"], 0)
        self.assertLess(audit["icir"], 0)
        self.assertAlmostEqual(audit["signed_mean_ic"], -audit["raw_mean_ic"])
        with self.assertRaises(Exception):
            evaluator.audit(node, audit_panel, dates, None)

    def test_low_coverage_raises_and_has_separate_pit_schema(self):
        panel = signed_panel(0.5, seed=3)
        dates = pd.DatetimeIndex(sorted(panel.date.unique()))
        blocked_dates = dates[:2]
        panel.loc[panel.date.isin(blocked_dates), "forward_return"] = np.nan
        with self.assertRaises(CoverageStop) as caught:
            LiveEvaluator().search(parse("f00", {"f00", "f01"}), panel, dates)
        exc = caught.exception
        self.assertLess(exc.coverage, 0.96)
        self.assertEqual(exc.planned_dates, len(dates))
        self.assertIn("pit_safe", exc.detail)
        self.assertIn("assumption_layer", exc.detail)
        self.assertEqual(exc.detail["pit_safe"]["planned_weeks"], 0)
        self.assertEqual(exc.detail["assumption_layer"]["planned_weeks"], len(dates))

    def test_frozen_history_covers_worst_temporal_chain_in_search_and_audit(self):
        spec = make_spec()
        bundle = bundle_from_spec(spec)
        self.assertEqual(spec.warmup_weeks, 312)
        self.assertEqual(bundle.manifest["history_dates"][1], "2016-12-30")
        self.assertEqual(bundle.manifest["search_dates"][0], "2017-01-06")
        node = parse("delta13(" * 24 + "f00" + ")" * 24, set(spec.feature_names))
        evaluator = LiveEvaluator()
        search = evaluator.search(node, bundle.search_panel, bundle.search_dates)
        self.assertEqual(search["coverage"], 1.0)
        for seed, folds in bundle.audit_folds.items():
            for dates in folds.values():
                audit = evaluator.audit(node, bundle.audit_panels[seed], dates, search["direction"])
                self.assertEqual(audit["coverage"], 1.0)


class LiveLoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TEST_live_qd_unit_")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_invalid_and_duplicate_consume_slots_without_fallback(self):
        script = [
            "not json",
            default_test_script()[2] | {"expression": "f00"},
            default_test_script()[0] | {"expression": "f01"},
            default_test_script()[1],
        ]
        spec = make_spec()
        bundle = bundle_from_spec(spec)
        factory = make_test_client_factory(script)
        result = run_live_loop(spec, bundle, factory(), self.root / "arm")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["attempted_slots"], 4)
        self.assertEqual(result["not_run_slots"], 0)
        self.assertEqual(result["cost"]["llm"]["actual_calls"], 4)
        self.assertEqual(result["cost"]["evaluator_calls"]["search_proposals"], 2)
        statuses = result["proposal_status_counts"]
        self.assertEqual(statuses["REJECTED_PARSE"], 1)
        self.assertEqual(statuses["DUPLICATE"], 1)
        proposals = read_jsonl(self.root / "arm/proposals.jsonl")
        self.assertEqual([row["proposal_id"] for row in proposals],
                         ["P-G00-S00", "P-G00-S01", "P-G01-S00", "P-G01-S01"])
        self.assertEqual(proposals[0]["raw_response"], "not json")
        self.assertEqual(proposals[1]["duplicate_of"], "initial_library")
        self.assertTrue(all(row["agent_proposed"] and not row["random_fallback"] for row in proposals))
        self.assertTrue(all("attempts" in row for row in proposals))
        self.assertFalse(result["random_fallback_used"])

    def test_coverage_stop_is_immediate_and_preserves_unspent_slots(self):
        script = [default_test_script()[0] | {"expression": "lag5(f00)"}]
        spec = make_spec()
        spec.warmup_weeks = 0
        bundle = bundle_from_spec(spec)
        factory = make_test_client_factory(script)
        client = factory()
        result = run_live_loop(spec, bundle, client, self.root / "arm", stop_before_audit=True)
        self.assertEqual(result["status"], "COVERAGE_STOP")
        self.assertEqual(result["attempted_slots"], 1)
        self.assertEqual(result["not_run_slots"], 3)
        self.assertEqual(result["nominated"], 0)
        self.assertEqual(client.get_stats()["actual_calls"], 1)
        self.assertEqual(result["coverage_stop"]["action"], "STOP_NO_SEED_REPLACEMENT_NO_RULE_CHANGE")
        self.assertEqual(result["proposal_status_counts"], {"COVERAGE_STOP": 1})
        self.assertEqual(len(read_jsonl(self.root / "arm/checkpoints.jsonl")), 2)
        resumed_client = factory()
        resumed = resume_live_loop(self.root / "arm", bundle=bundle, llm=resumed_client,
                                   stop_before_audit=True)
        self.assertEqual(resumed["status"], "COVERAGE_STOP")
        self.assertEqual(resumed_client.get_stats()["actual_calls"], 0)

        def interrupt(checkpoint):
            if checkpoint["state"]["status"] == "COVERAGE_STOP":
                raise RuntimeError("TEST coverage checkpoint interruption")

        interrupted = self.root / "interrupted_arm"
        with self.assertRaisesRegex(RuntimeError, "TEST coverage checkpoint interruption"):
            run_live_loop(spec, bundle, factory(), interrupted, on_checkpoint=interrupt,
                          stop_before_audit=True)
        resumed_client = factory()
        with self.assertRaisesRegex(ResumeError, "coverage stop latched"):
            resume_live_loop(interrupted, bundle=bundle, llm=resumed_client, stop_before_audit=True)
        self.assertEqual(resumed_client.get_stats()["actual_calls"], 0)

    def test_raw_and_evidence_archives_change_real_parent_trajectory(self):
        invalid_schema = {
            "expression": "neg(f00)",
            "rationale": "Negation preserves the magnitude of the same mechanism.",
            "expected_ic": 0.02,
        }
        script = [invalid_schema, default_test_script()[2]]
        agent_seed = next(seed for seed in range(100)
                          if random.Random(f"{seed}:1:0").choice([0, 1]) == 1)
        results = {}
        for arm in ARMS:
            spec = make_spec(arm=arm, agent_seed=agent_seed, generations=2, slots=1,
                             audit_seeds=(201,))
            bundle = bundle_from_spec(spec)
            results[arm] = run_live_loop(spec, bundle, make_test_client_factory(script)(),
                                         self.root / arm)
        raw, evidence = results[RAW], results[EVIDENCE]
        self.assertEqual(raw["proposal_status_counts"]["ARCHIVED_RAW"], 2)
        self.assertEqual(evidence["proposal_status_counts"]["REJECTED_SCHEMA"], 1)
        self.assertEqual(raw["trajectory"][1]["parent_id"], "P-G00-S00")
        self.assertEqual(evidence["trajectory"][1]["parent_id"], "INITIAL-000")
        self.assertNotEqual(raw["trajectory"][1]["parent_formula"], evidence["trajectory"][1]["parent_formula"])
        self.assertIn("neg(f00)", [row["formula"] for row in raw["audit"]])
        self.assertNotIn("neg(f00)", [row["formula"] for row in evidence["audit"]])
        for result in results.values():
            self.assertTrue(result["independent_recompute"]["consistent"])
            for proposal in read_jsonl(self.root / result["arm"] / "proposals.jsonl"):
                if result["arm"] == EVIDENCE:
                    self.assertTrue(proposal["parent_evidence_verified"])
                    self.assertTrue(proposal["parent_search_valid"])

    def test_parent_selection_prefers_least_filled_cells(self):
        loop = object.__new__(_LiveLoop)
        loop.spec = make_spec(agent_seed=19)
        loop.cell_counts = {}
        loop.archive = {}
        for index, visits in enumerate((9, 4, 3, 2, 1, 0)):
            key = ("rank", "mid", f"cell_{index}")
            loop.cell_counts[key] = visits
            loop.archive[key] = {"cell_key": list(key), "search_fitness": float(6 - index),
                                 "formula": f"rank(f{index:02d})"}

        class Chooser:
            offered = None

            def choice(self, elites):
                self.offered = list(elites)
                return elites[-1]

        chooser = Chooser()
        with mock.patch("agents.live_qd_runner.random.Random", return_value=chooser) as seeded:
            selected = loop._select_parent(1, 1)
        seeded.assert_called_once_with("19:1:1")
        expected = [loop.archive[("rank", "mid", f"cell_{index}")] for index in (5, 4, 3, 2, 1)]
        self.assertEqual(chooser.offered, expected)
        self.assertEqual(selected, expected[-1])
        with self.assertRaises(ValueError):
            live_model_config("unregistered-model")

    def test_cell_counts_survive_checkpoint_resume_without_changing_lineage(self):
        spec = make_spec(generations=3, slots=1, audit_seeds=(201,))
        bundle = bundle_from_spec(spec)
        script = default_test_script()
        reference = run_live_loop(spec, bundle, make_test_client_factory(script)(), self.root / "reference")

        class InjectedInterruption(RuntimeError):
            pass

        def interrupt(checkpoint):
            if len(checkpoint["state"]["proposal_ids"]) == 1:
                raise InjectedInterruption("TEST checkpoint interruption")

        with self.assertRaises(InjectedInterruption):
            run_live_loop(spec, bundle, make_test_client_factory(script)(), self.root / "resumed",
                          on_checkpoint=interrupt)
        result = resume_live_loop(self.root / "resumed", bundle=bundle,
                                  llm=make_test_client_factory(script[1:])())
        self.assertEqual(result["trajectory"], reference["trajectory"])
        self.assertEqual(result["audit"], reference["audit"])
        for directory in (self.root / "reference", self.root / "resumed"):
            checkpoints = read_jsonl(directory / "checkpoints.jsonl")
            self.assertIsNotNone(checkpoints[1]["state"]["cell_counts"])
            counts = {tuple(key): value for key, value in checkpoints[-1]["state"]["cell_counts"]}
            rows = [checkpoints[-1]["state"]["initial_rows"], read_jsonl(directory / "proposals.jsonl")]
            expected = {}
            for row_list in rows:
                for row in row_list:
                    if row.get("cell_key"):
                        key = tuple(row["cell_key"])
                        expected[key] = expected.get(key, 0) + 1
            self.assertEqual(counts, expected)

    def test_resume_skips_completed_slots_and_rejects_tampering(self):
        spec = make_spec(audit_seeds=(201,))
        bundle = bundle_from_spec(spec)
        arm_dir = self.root / "arm"
        fired = []

        class InjectedInterruption(RuntimeError):
            pass

        def interrupt(checkpoint):
            if len(checkpoint["state"]["proposal_ids"]) == 1 and not fired:
                fired.append(True)
                raise InjectedInterruption("TEST checkpoint interruption")

        with self.assertRaises(InjectedInterruption):
            run_live_loop(spec, bundle, make_test_client_factory()(), arm_dir, on_checkpoint=interrupt)
        self.assertFalse((arm_dir / "completion.json").exists())
        first = read_jsonl(arm_dir / "proposals.jsonl")
        self.assertEqual(len(first), 1)
        first_hash = first[0]["raw_response_hash"]
        client = make_test_client_factory()()
        result = resume_live_loop(arm_dir, llm=client, bundle=bundle)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["attempted_slots"], 4)
        proposals = read_jsonl(arm_dir / "proposals.jsonl")
        self.assertEqual(len(proposals), 4)
        self.assertEqual(len({row["proposal_id"] for row in proposals}), 4)
        self.assertEqual(proposals[0]["raw_response_hash"], first_hash)
        self.assertEqual(len(read_jsonl(arm_dir / "llm_responses.jsonl")), 4)
        completion_bytes = (arm_dir / "completion.json").read_bytes()
        second_client = make_test_client_factory()()
        resumed = resume_live_loop(arm_dir, llm=second_client, bundle=bundle)
        self.assertEqual(resumed["attempted_slots"], 4)
        self.assertEqual(second_client.get_stats()["logical_calls"], 0)
        self.assertEqual((arm_dir / "completion.json").read_bytes(), completion_bytes)

        tampered = self.root / "tampered"
        shutil.copytree(arm_dir, tampered)
        manifest_path = tampered / "manifest.json"
        manifest = read_json(manifest_path)
        manifest["frozen_hashes"]["protocol"]["value"]["coverage_min"] = 0.5
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ResumeError):
            resume_live_loop(tampered, llm=make_test_client_factory()(), bundle=bundle)

    def test_resume_rejects_uncommitted_llm_and_evaluation_work(self):
        spec = make_spec(audit_seeds=(201,))
        bundle = bundle_from_spec(spec)
        arm_dir = self.root / "arm"
        run_live_loop(spec, bundle, make_test_client_factory()(), arm_dir, stop_before_audit=True)
        for case in ("orphan_llm_response", "unfinished_operation"):
            with self.subTest(case=case):
                directory = self.root / case
                shutil.copytree(arm_dir, directory)
                name = "llm_responses.jsonl" if case == "orphan_llm_response" else "operations.jsonl"
                event = ({"proposal_id": "P-G02-S00"} if case == "orphan_llm_response" else
                         {"kind": "search_proposals", "operation_id": "P-G02-S00", "event": "started"})
                with (directory / name).open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(event) + "\n")
                client = make_test_client_factory()()
                with self.assertRaisesRegex(ResumeError, "uncheckpointed|unfinished"):
                    resume_live_loop(directory, llm=client, bundle=bundle)
                self.assertEqual(client.get_stats()["logical_calls"], 0)

    def test_recompute_receipt_recovers_counter_without_replaying(self):
        spec = make_spec(audit_seeds=(201,))
        bundle = bundle_from_spec(spec)
        arm_dir = self.root / "recompute_recovery"
        run_live_loop(spec, bundle, make_test_client_factory()(), arm_dir, stop_before_audit=True)
        original_checkpoint = _LiveLoop._checkpoint
        interrupted = []

        def interrupt_before_complete_checkpoint(loop):
            if loop.phase == "complete" and loop.recompute is not None and not interrupted:
                interrupted.append(True)
                raise RuntimeError("TEST recompute receipt interruption")
            return original_checkpoint(loop)

        with mock.patch.object(_LiveLoop, "_checkpoint", interrupt_before_complete_checkpoint):
            with self.assertRaisesRegex(RuntimeError, "TEST recompute receipt interruption"):
                resume_live_loop(arm_dir, bundle=bundle, llm=make_test_client_factory()(), audit_only=True)
        self.assertTrue((arm_dir / "recompute.json").is_file())
        before = read_jsonl(arm_dir / "operations.jsonl")
        client = make_test_client_factory()()
        result = resume_live_loop(arm_dir, bundle=bundle, llm=client, audit_only=True)
        self.assertEqual(read_jsonl(arm_dir / "operations.jsonl"), before)
        self.assertEqual(client.get_stats()["logical_calls"], 0)
        recompute = read_json(arm_dir / "recompute.json")
        calls = result["cost"]["evaluator_calls"]
        self.assertEqual(calls["recompute"], len(recompute["fold_checks"]))
        self.assertEqual(calls["total"], sum(calls[key] for key in
                                             ("search_initial", "search_proposals", "audit", "recompute")))
        self.assertEqual(len(before), 2 * (result["attempted_slots"] + calls["total"]))

    def test_two_stage_search_freezes_before_audit_only_resume(self):
        spec = make_spec(audit_seeds=(201,))
        bundle = bundle_from_spec(spec)
        arm_dir = self.root / "two_stage"
        client = make_test_client_factory()()
        frozen = run_live_loop(spec, bundle, client, arm_dir, stop_before_audit=True)
        self.assertEqual(frozen["status"], "NOMINATIONS_FROZEN")
        self.assertTrue(frozen["search_stage_complete"])
        self.assertGreater(frozen["nominated"], 0)
        self.assertEqual(frozen["audit"], [])
        self.assertIsNone(frozen["independent_recompute"])
        self.assertFalse((arm_dir / "audit.jsonl").exists())
        self.assertFalse((arm_dir / "completion.json").exists())
        self.assertTrue((arm_dir / "search_completion.json").is_file())
        self.assertEqual(client.get_stats()["logical_calls"], 4)
        audit_client = make_test_client_factory()()
        completed = resume_live_loop(arm_dir, bundle=bundle, llm=audit_client, audit_only=True)
        self.assertEqual(completed["status"], "COMPLETED")
        self.assertEqual(completed["attempted_slots"], 4)
        self.assertTrue(completed["independent_recompute"]["consistent"])
        self.assertEqual(audit_client.get_stats()["logical_calls"], 0)
        with self.assertRaisesRegex(RuntimeError, "cannot run search"):
            run_live_loop(spec, bundle, make_test_client_factory()(), self.root / "invalid_audit_only",
                          audit_only=True)

    def test_four_matched_loops_streams_audit_and_manifests(self):
        factory = make_test_client_factory()
        result = run_matched_smoke(
            self.root, "TEST_matched", tasks=("planted", "weak"), n_blocks=1,
            generations=2, slots_per_generation=2, client_factory=factory,
            audit_seed_count=2, n_audit_folds=2, n_symbols=36, n_weeks=24,
        )
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["n_loops_completed"], 4)
        self.assertEqual(result["n_loops_planned"], 4)
        self.assertEqual(len(factory.clients), 4)
        self.assertEqual(len({id(client) for client in factory.clients}), 4)
        run_dir = self.root / "TEST_matched"
        self.assertFalse(result["primary_analysis_performed"])
        for loop in result["loops"]:
            with self.subTest(task=loop["task"], arm=loop["arm"]):
                self.assertEqual(loop["status"], "COMPLETED")
                self.assertEqual(loop["proposal_slots"], 4)
                self.assertEqual(loop["attempted_slots"], 4)
                self.assertEqual(loop["not_run_slots"], 0)
                self.assertEqual(loop["cost"]["llm"]["logical_calls"], 4)
                self.assertEqual(loop["cost"]["llm"]["actual_calls"], 4)
                self.assertEqual(loop["cost"]["llm"]["cache_hits"], 0)
                self.assertEqual(loop["cost"]["evaluator_calls"]["search_initial"], 1)
                self.assertGreater(loop["nominated"], 0)
                self.assertEqual(len(loop["audit"]), loop["nominated"])
                self.assertTrue(loop["independent_recompute"]["consistent"])
                self.assertEqual(loop["independent_recompute"]["consistency_rate"], 1.0)
                self.assertGreater(loop["independent_recompute"]["n_checks"], 0)
                self.assertFalse(loop["audit_feedback_applied"])
                self.assertFalse(loop["random_fallback_used"])
                self.assertEqual(loop["audit_confirmed_unique_discoveries"],
                                 sum(row["audit_confirmed"] for row in loop["audit"]))
                self.assertAlmostEqual(loop["audit_confirmed_per_slot"],
                                       loop["audit_confirmed_unique_discoveries"] / 4)
                for audit in loop["audit"]:
                    self.assertEqual(len(audit["folds"]), 4)
                    self.assertTrue(audit["checks"]["fixed_audit_seeds_complete"])
                    self.assertTrue(audit["checks"]["not_in_initial_library"])
                    self.assertTrue(audit["checks"]["canonical_unique"])
                    self.assertEqual(audit["audit_confirmed"], all(audit["checks"].values()))
                    self.assertTrue(all(fold["direction"] == audit["direction"] for fold in audit["folds"]))
                arm_dir = run_dir / "blocks" / loop["task"] / loop["block_id"] / "arms" / loop["arm"]
                proposals = read_jsonl(arm_dir / "proposals.jsonl")
                raw = read_jsonl(arm_dir / "llm_responses.jsonl")
                self.assertEqual(len(proposals), 4)
                self.assertEqual(len(raw), 4)
                self.assertTrue(all(row["raw_response"] for row in proposals))
                self.assertTrue(all(row["raw_response_hash"] for row in proposals))
                self.assertTrue(all(row["attempts"] for row in proposals))
                known = {"INITIAL-000"}
                for proposal in proposals:
                    self.assertIn(proposal["parent_id"], known)
                    known.add(proposal["proposal_id"])
                    self.assertIn("rationale", proposal)
                    self.assertIn("falsification", proposal)
                    if proposal.get("search"):
                        self.assertIsNotNone(proposal["ast"])
                        self.assertIn(proposal["direction"], (-1, 1))
                manifest = read_json(arm_dir / "manifest.json")
                self.assertFalse(manifest["llm_seed"]["explicit_seed_supported"])
                self.assertFalse(manifest["llm_seed"]["deterministic_sampling_claimed"])
                self.assertFalse(manifest["llm_seed"]["prompt_seed_is_determinism"])
                self.assertIn("resume_command", manifest)
                self.assertIn("code", manifest["frozen_hashes"])
                self.assertIn("data", manifest["frozen_hashes"])
                self.assertFalse(manifest["historical_claims_read"])
                other_arm = RAW if loop["arm"] == EVIDENCE else EVIDENCE
                other = read_json(arm_dir.parent / other_arm / "manifest.json")
                for key in ("protocol", "model", "prompt", "data"):
                    self.assertEqual(manifest["frozen_hashes"][key], other["frozen_hashes"][key])
                block = read_json(arm_dir.parents[1] / "manifest.json")
                self.assertEqual(set(block["arm_assignment"]["execution_order"]), set(ARMS))
        with self.assertRaises(FileExistsError):
            run_matched_smoke(self.root, "TEST_matched", tasks=("planted",), n_blocks=1,
                              client_factory=make_test_client_factory(), n_symbols=36, n_weeks=24)


if __name__ == "__main__":
    unittest.main(verbosity=2)
