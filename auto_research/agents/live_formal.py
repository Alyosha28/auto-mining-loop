#!/usr/bin/env python3
"""Preregistered, resumable two-stage live experiments with bounded concurrency."""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))

from agents.live_analysis import analyze_run, sanitize_loop
from agents.live_data import DEFAULT_WARMUP_WEEKS
from agents.live_manifest import (
    append_jsonl,
    canonical_json,
    code_hashes,
    create_run_directory,
    file_hashes,
    frozen_hashes,
    initialize_manifest,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_json,
    utc_now,
    write_immutable_json,
)
from agents.live_qd_runner import (
    ARMS,
    EVIDENCE,
    RAW,
    LiveLoopSpec,
    live_model_config,
    prompt_contract,
    protocol_identity,
)
from agents.live_realdev import materialize_real_fold
from agents.live_warmup import DEFAULT_FEATURES

RUNNER = ROOT / "auto_research/agents/live_qd_runner.py"
PYTHON = ROOT / ".venv/bin/python"
DEFAULT_RUN_ROOT = ROOT / "auto_research/agents/runs"
DEFAULT_CALENDAR = ROOT / "auto_research/agents/cache/REALDEV_FOLD_CALENDAR_20261024.json"
DEFAULT_WARMUP = ROOT / "auto_research/agents/cache/REALDEV_WARMUP_2010_2016_312_20261024_R2"
TASKS = ("planted", "weak", "nonlinear", "regime_state", "null")


class LoopFailure(RuntimeError):
    def __init__(self, message: str, status: str = "INCOMPLETE"):
        super().__init__(message)
        self.status = status


def _key(block: dict) -> str:
    return f"{block['domain']}/{block['task']}/{block['block_id']}"


def _seed(run_id: str, domain: str, *parts) -> int:
    return 1000000 + int(sha256_json([run_id, domain, *parts])[:12], 16) % 2000000000


def _arms(run_id: str, key: str, scope: str) -> list[str]:
    order = list(ARMS)
    random.Random(f"{run_id}:{scope}:{key}").shuffle(order)
    return order


def _write_spec(path: Path, spec: LiveLoopSpec) -> str:
    if path.exists():
        restored = LiveLoopSpec.from_dict(read_json(path))
        if canonical_json(asdict(restored)) != canonical_json(asdict(spec)):
            raise ValueError(f"existing run spec differs: {path}")
    else:
        write_immutable_json(path, asdict(spec))
    return str(path)


def _block(domain: str, task: str, block_id: str, cluster_id: str, run_id: str) -> dict:
    key = f"{domain}/{task}/{block_id}"
    blind = _arms(run_id, key, "blind")
    return {
        "domain": domain, "task": task, "block_id": block_id, "cluster_id": cluster_id,
        "order": _arms(run_id, key, "execution"),
        "blind_arms": {"A": blind[0], "B": blind[1]},
        "spec_paths": {},
    }


def _synthetic_blocks(run_id: str, model: dict, tasks: tuple[str, ...], n_blocks: int,
                      generations: int, slots: int, n_symbols: int, test_only: bool,
                      run_dir: Path) -> list[dict]:
    blocks = []
    for task in tasks:
        for index in range(n_blocks):
            block = _block("synthetic", task, f"block_{index:03d}", task, run_id)
            block["agent_seed"] = _seed(run_id, "agent", task, index)
            search_seed = _seed(run_id, "synthetic-search", task, index)
            audit_seeds = (_seed(run_id, "synthetic-audit-0", task, index),
                           _seed(run_id, "synthetic-audit-1", task, index))
            if search_seed in audit_seeds or len(set(audit_seeds)) != 2:
                raise ValueError("synthetic search/audit seed collision; refusing silent replacement")
            block["search_seed"] = search_seed
            block["audit_seeds"] = list(audit_seeds)
            initial = (tuple(f"product(f{index:02d},f_state)" for index in range(5))
                       if task == "regime_state" else tuple(f"f{index:02d}" for index in range(5)))
            block["initial_library"] = list(initial)
            for arm in ARMS:
                spec = LiveLoopSpec(
                    run_id=run_id, task=task, block_id=block["block_id"], arm=arm,
                    agent_seed=block["agent_seed"], search_seed=search_seed, audit_seeds=audit_seeds,
                    generations=generations, slots_per_generation=slots, initial_library=initial,
                    n_symbols=n_symbols, n_weeks=32, n_features=8, warmup_weeks=DEFAULT_WARMUP_WEEKS,
                    search_fraction=0.75, n_audit_folds=2, nomination_cap=4, model=model,
                    data_config={"kind": "synthetic"}, execution_order=block["order"].index(arm),
                    test_mode=test_only,
                )
                block["feature_names"] = list(spec.feature_names)
                path = run_dir / "synthetic" / task / block["block_id"] / "arms" / arm / "run_spec.json"
                block["spec_paths"][arm] = _write_spec(path, spec)
            blocks.append(block)
    return blocks


def _real_skeletons(run_id: str, run_dir: Path) -> list[dict]:
    blocks = []
    for fold in range(8):
        for replicate in range(4):
            block = _block("real_dev", "real_dev", f"fold_{fold:02d}_pair_{replicate:02d}",
                           f"fold_{fold:02d}", run_id)
            block["agent_seed"] = _seed(run_id, "real-agent", fold, replicate)
            block["initial_library"] = list(DEFAULT_FEATURES)
            block["feature_names"] = list(DEFAULT_FEATURES)
            block["spec_paths"] = {
                arm: str(run_dir / "real_dev" / block["block_id"] / "arms" / arm / "run_spec.json")
                for arm in ARMS
            }
            blocks.append(block)
    return blocks


def _check_model(model: dict) -> None:
    if sha256_file(Path(model["weights_file"])) != model["weights_sha256"]:
        raise ValueError("local model weights differ from the registered SHA-256")
    from agents.live_llm import validate_loopback_base
    base = validate_loopback_base(model["base_url"])
    with urlopen(f"{base}/models", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    identifiers = {row.get("id") for row in payload.get("data", [])}
    if model["name"] not in identifiers:
        raise ValueError(f"local model is not loaded: {model['name']}")


def prepare_run(run_id: str, output_root: Path = DEFAULT_RUN_ROOT, reasoning_effort: str | None = None,
                test_smoke: bool = False, workers: int = 4, smoke_receipt: Path | None = None,
                calendar_path: Path = DEFAULT_CALENDAR, warmup_dir: Path = DEFAULT_WARMUP) -> dict:
    if not test_smoke and (workers != 4 or smoke_receipt is None):
        raise ValueError("formal execution requires four workers and a frozen smoke resource receipt")
    run_dir = create_run_directory(Path(output_root), run_id, test_mode=test_smoke)
    model = live_model_config(reasoning_effort=reasoning_effort)
    _check_model(model)
    tasks = ("planted", "weak") if test_smoke else TASKS
    blocks = _synthetic_blocks(run_id, model, tasks, 1 if test_smoke else 8,
                               2 if test_smoke else 6, 2 if test_smoke else 4,
                               40 if test_smoke else 250, test_smoke, run_dir)
    if not test_smoke:
        real_blocks = _real_skeletons(run_id, run_dir)
        blocks = [real_blocks[0], *blocks, *real_blocks[1:]]
    blind_map = {_key(block): block["blind_arms"] for block in blocks}
    write_immutable_json(run_dir / "blind_map.json", blind_map)
    design = {
        "schema": "AUTO-RESEARCH-LIVE-FORMAL-DESIGN-1",
        "run_id": run_id, "test_only": test_smoke, "blocks": blocks, "model": model,
        "workers": workers, "real_enabled": not test_smoke,
        "generations": 2 if test_smoke else 6, "slots_per_generation": 2 if test_smoke else 4,
        "proposal_slots_per_loop": 4 if test_smoke else 24,
        "synthetic_symbols": 40 if test_smoke else 250,
        "synthetic_evaluation_weeks": 32,
        "synthetic_spec_hashes": {path: sha256_file(Path(path)) for block in blocks
                                  for path in block["spec_paths"].values() if Path(path).is_file()},
        "llm_seed_policy": "no explicit backend seed; agent seeds only control QD choice and arm ordering",
        "independent_clients_per_arm": True, "cross_arm_or_run_cache": False,
    }
    write_immutable_json(run_dir / "design.json", design)
    design_hash = sha256_file(run_dir / "design.json")
    calendar = None
    warmup = None
    if not test_smoke:
        calendar_path = Path(calendar_path).resolve()
        warmup_dir = Path(warmup_dir).resolve()
        calendar = read_json(calendar_path)
        warmup_manifest = read_json(warmup_dir / "manifest.json")
        if (calendar.get("outcome_rows_read") is not False or warmup_manifest.get("status") != "COMPLETED"
                or warmup_manifest.get("warmup_weeks") != 312
                or not warmup_manifest["availability"]["all_required_predictor_weeks_pass"]
                or warmup_manifest.get("labels_computed") is not False
                or warmup_manifest.get("raw_label_columns_read") != []):
            raise ValueError("real-dev calendar/warmup fails its frozen access contract")
        warmup = {"path": str(warmup_dir), "manifest_sha256": sha256_file(warmup_dir / "manifest.json")}
    prereg = {
        "schema": "AUTO-RESEARCH-LIVE-QD-PREREGISTRATION-1",
        "run_id": run_id, "created_at": utc_now(), "test_only": test_smoke,
        "protocol_id": protocol_identity(), "prepared_design_sha256": design_hash,
        "code_hashes": code_hashes(), "model": model, "prompt": prompt_contract(),
        "arms": {EVIDENCE: "trusted archive requires schema/AST/coverage/search gate and frozen direction",
                 RAW: "raw-search archive and nominations; identical live proposal/evaluation budget"},
        "synthetic": {
            "tasks": list(tasks), "blocks_per_task": 1 if test_smoke else 8,
            "primary_tasks": ["planted", "weak", "nonlinear", "regime_state"],
            "null_negative_control_blocks": 0 if test_smoke else 8,
            "symbols": 40 if test_smoke else 250, "evaluation_weeks": 32,
            "search_fraction": 0.75, "audit_seeds_per_block": 2, "audit_folds_per_seed": 2,
            "initial_library": {"regime_state": "product(fi,f_state) for i=0..4; declared state and planted candidate set",
                                "other_tasks": "f00..f04 fixed generator-declared candidates; not outcome-selected"},
        },
        "real_dev": None if test_smoke else {
            "matched_pairs": 32, "temporal_clusters": 8, "agent_pairs_per_cluster": 4,
            "initial_library": list(DEFAULT_FEATURES), "feature_names": list(DEFAULT_FEATURES),
            "initial_library_rationale": "the frozen nine primary-control fields, not the active factor library or score history",
            "calendar": {"path": str(calendar_path), "sha256": sha256_file(calendar_path)},
            "warmup": warmup,
            "initial_search_weeks": 23, "audit_weeks_per_block": 22,
            "unused_signal_dates": calendar["unused_dev_signal_dates"],
            "search_history_overlap": True, "agent_seed_pairs_are_not_independent_markets": True,
            "search_validation": calendar["search_validation"],
            "interpretation": "dev-internal temporal replication, never independent real-data OOS",
        },
        "real_dev_fold_calendar_sha256": sha256_file(calendar_path) if calendar is not None else None,
        "pre_2017_authorization": None if test_smoke else {
            "scope": "predictor-only warmup; no pre-2017 outcome/label columns or constructed forward returns",
            "authorized_raw_window": ["2010-01-01", "2016-12-31"],
            "source": "explicit user authorization on 2026-10-24",
            "not_an_oos_window": True,
        },
        "budget": {"generations": 2 if test_smoke else 6,
                   "proposal_slots_per_loop": 4 if test_smoke else 24,
                   "max_proposal_slots_per_loop": 24, "nomination_cap": 4,
                   "invalid_duplicate_parse_failures_consume_slots": True, "random_fallback": False},
        "coverage": {"minimum": 0.96, "pit_and_assumption_layers_reported": True,
                     "on_below_minimum": "stop all new work, retain scene and unspent slots; no seed replacement or rule change"},
        "primary_endpoint": "audit-confirmed unique discoveries / 24 proposal slots; evidence minus raw within matched block",
        "analysis": {
            "synthetic_units": "32 task x fresh-panel-seed matched blocks; null excluded from primary effect",
            "synthetic_ci": "task-stratified paired block bootstrap, 10000 draws, seed 2026102401, percentile 95%",
            "synthetic_p": "paired sign-flip randomization, 10000 draws, seed 2026102402, two-sided +1 correction",
            "real_units": "8 temporal clusters; four agent pairs averaged within each cluster",
            "real_ci": "cluster bootstrap, 10000 draws, seed 2026102403, percentile 95%",
            "real_p": "cluster-level sign flips, 10000 draws, seed 2026102404, descriptive only",
            "secondary": "exploratory only: cumulative search-valid curves and AUC=mean(cumulative valid/24 across slots), first search-valid/audit-confirmed slot, audit-confirmed/nominated, signed IC/ICIR, mechanism novelty by distinct AST leaf sets, parent reuse/stagnation, null declarations, recompute consistency and cost per confirmed discovery",
            "multiple_testing": "one confirmatory synthetic endpoint; no secondary confirmatory claims; new families require a new preregistration and Holm",
            "blinding": "arm-neutral blinded artifacts validated before reading arm map/provenance",
            "power": "32 pairs under design dz=0.5 is approximately 80%; no smaller detectable effect is promised",
        },
        "execution": {
            "workers": workers, "per_block_arms_sequential": True,
            "search_then_frozen_nominations_then_independent_audit_process": True,
            "audit_feedback_to_search": False, "optional_stopping": False,
            "recovery": "resume immutable per-arm checkpoints; do not replay completed slots",
            "real_data_must_be_materialized_before_any_formal_synthetic_loop": True,
            "first_real_pair_infrastructure_gate": "fold_00/pair_00 both arms and audits complete before other blocks; status only, no effect review or rerun; it remains in the formal analysis",
        },
        "resource_basis": None if test_smoke else {
            "path": str(Path(smoke_receipt).resolve()), "sha256": sha256_file(Path(smoke_receipt)),
            "planned_loops": 144, "planned_proposal_slots": 3456,
            "note": "resource estimate is not permission to reduce the frozen design",
        },
        "score_window_read": False, "oos_window_read": False, "embargo_read": False,
        "independent_real_data_oos": False, "historical_claims_read": False, "kg_read": False,
    }
    prereg_path = (run_dir / "preregistration.json" if test_smoke else
                   ROOT / "auto_research/preregistrations" / f"PREREG-{run_id}.json")
    write_immutable_json(prereg_path, prereg)
    data = {"design_sha256": design_hash, "calendar": calendar, "warmup": warmup}
    manifest = initialize_manifest(
        run_dir / "manifest.json", "run", frozen_hashes(prereg, model, prompt_contract(), data),
        run_id=run_id, test_only=test_smoke, real_data_read=not test_smoke,
        score_window_read=False, oos_window_read=False, embargo_read=False,
        independent_real_data_oos=False, planned_blocks=[_key(block) for block in blocks],
        blind_map_sha256=sha256_file(run_dir / "blind_map.json"),
        preregistration={"path": str(prereg_path), "sha256": sha256_file(prereg_path)},
        design_sha256=design_hash, workers=workers, primary_analysis_performed=False,
        stage="PREREGISTERED",
    )
    if test_smoke:
        _write_execution_plan(run_dir, blocks, workers, real_materialized=False)
    return read_json(manifest)


def _write_execution_plan(run_dir: Path, blocks: list[dict], workers: int, real_materialized: bool) -> dict:
    path = run_dir / "execution_plan.json"
    plan = {
        "schema": "AUTO-RESEARCH-LIVE-EXECUTION-PLAN-1", "created_at": utc_now(),
        "blocks": blocks, "workers": workers, "real_data_materialized": real_materialized,
        "spec_hashes": {path: sha256_file(Path(path)) for block in blocks for path in block["spec_paths"].values()},
        "n_loops": sum(len(block["spec_paths"]) for block in blocks),
        "primary_analysis_performed": False,
    }
    if path.exists():
        old = read_json(path)
        if old["blocks"] != plan["blocks"] or old["workers"] != workers:
            raise ValueError("execution plan drift")
        return old
    write_immutable_json(path, plan)
    return plan


def materialize_real(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    if manifest.get("test_only") or manifest["frozen_hashes"]["code"]["files"] != code_hashes():
        raise ValueError("formal code or TEST contract mismatch")
    prereg_path = Path(manifest["preregistration"]["path"])
    if sha256_file(prereg_path) != manifest["preregistration"]["sha256"]:
        raise ValueError("preregistration hash mismatch")
    prereg = read_json(prereg_path)
    design = read_json(run_dir / "design.json")
    calendar_path = Path(prereg["real_dev"]["calendar"]["path"])
    calendar = read_json(calendar_path)
    configs = {}
    for fold in calendar["folds"]:
        fold_id = fold["fold_id"]
        input_dir = run_dir / "real_dev/folds" / fold_id / "input"
        if (input_dir / "data_config.json").is_file():
            config = read_json(input_dir / "data_config.json")
            if config["fold_calendar"]["sha256"] != sha256_file(calendar_path):
                raise ValueError("existing real-dev input calendar mismatch")
        else:
            config = materialize_real_fold(calendar_path, Path(prereg["real_dev"]["warmup"]["path"]),
                                           input_dir, DEFAULT_FEATURES, preregistration=prereg_path,
                                           fold_id=fold_id)
        configs[fold_id] = config
    for block in design["blocks"]:
        if block["domain"] != "real_dev":
            continue
        config = configs[block["cluster_id"]]
        for arm in ARMS:
            spec = LiveLoopSpec(
                run_id=manifest["run_id"], task="real_dev", block_id=block["block_id"], arm=arm,
                agent_seed=block["agent_seed"], search_seed=0, audit_seeds=(0,), generations=6,
                slots_per_generation=4, initial_library=DEFAULT_FEATURES, n_symbols=None,
                n_weeks=22, n_features=len(DEFAULT_FEATURES), warmup_weeks=312,
                feature_names=DEFAULT_FEATURES, n_audit_folds=2, nomination_cap=4,
                model=design["model"], data_config=config,
                execution_order=block["order"].index(arm), test_mode=False,
            )
            _write_spec(Path(block["spec_paths"][arm]), spec)
    return _write_execution_plan(run_dir, design["blocks"], design["workers"], real_materialized=True)


def _process(command: list[str], arm_dir: Path, stage: str, stop_event: threading.Event) -> None:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""),
           "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    terminated = False
    while process.poll() is None:
        if stop_event.wait(0.2):
            terminated = True
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
            break
    output, _ = process.communicate()
    log_dir = arm_dir / "process_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    index = len(list(log_dir.glob(f"{stage}_*.log")))
    log_path = log_dir / f"{stage}_{index:03d}.log"
    with log_path.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps({"command": command, "returncode": process.returncode,
                                 "terminated_by_global_stop": terminated}, ensure_ascii=False) + "\n")
        handle.write(output or "")
    if process.returncode or terminated:
        raise LoopFailure(f"subprocess failed: {log_path}; returncode={process.returncode}")


def _run_arm(run_dir: Path, spec_path: Path, stop_event: threading.Event) -> dict:
    arm_dir = spec_path.parent
    completion = arm_dir / "completion.json"
    if completion.is_file():
        return read_json(completion)
    if stop_event.is_set():
        raise LoopFailure("global stop before search")
    if not (arm_dir / "search_completion.json").is_file():
        if (arm_dir / "manifest.json").is_file():
            command = [str(PYTHON), str(RUNNER), "--resume-arm", str(arm_dir), "--stop-before-audit"]
        else:
            command = [str(PYTHON), str(RUNNER), "--run-spec", str(spec_path), "--stop-before-audit"]
        _process(command, arm_dir, "search", stop_event)
    if not completion.is_file():
        if not (arm_dir / "search_completion.json").is_file():
            raise LoopFailure(f"search stage did not freeze nominations: {arm_dir}")
        search = read_json(arm_dir / "search_completion.json")
        if search["status"] != "NOMINATIONS_FROZEN":
            raise LoopFailure(f"search stopped before frozen nominations: {arm_dir}: {search['status']}",
                              status=search["status"])
        if stop_event.is_set():
            raise LoopFailure("global stop before audit")
        _process([str(PYTHON), str(RUNNER), "--resume-arm", str(arm_dir), "--audit-only"],
                 arm_dir, "audit", stop_event)
    if not completion.is_file():
        raise LoopFailure(f"audit did not complete: {arm_dir}")
    return read_json(completion)


def _run_block(run_dir: Path, block: dict, stop_event: threading.Event) -> tuple[dict, dict]:
    key = _key(block)
    inverse = {arm: blind for blind, arm in block["blind_arms"].items()}
    arms = {}
    provenance = {}
    for arm in block["order"]:
        spec = LiveLoopSpec.from_dict(read_json(block["spec_paths"][arm]))
        if list(spec.initial_library) != block["initial_library"] or list(spec.feature_names) != block["feature_names"]:
            raise ValueError(f"initial-library or feature contract mismatch: {key}/{arm}")
        summary = _run_arm(run_dir, Path(block["spec_paths"][arm]), stop_event)
        if summary["status"] != "COMPLETED":
            stop_event.set()
            raise LoopFailure(f"{key}/{arm}: {summary['status']}", status=summary["status"])
        completion = (Path(block["spec_paths"][arm]).parent / "completion.json").resolve()
        arms[inverse[arm]] = {"result": sanitize_loop(summary)}
        provenance[arm] = {"path": str(completion.relative_to(run_dir)),
                           "completion_sha256": sha256_file(completion)}
    result = {key: value for key, value in block.items() if key in {
        "domain", "task", "block_id", "cluster_id", "initial_library", "feature_names"}}
    result["arms"] = arms
    return result, provenance


def _next_path(run_dir: Path, stem: str) -> Path:
    index = 0
    while True:
        path = run_dir / (f"{stem}.json" if index == 0 else f"{stem}_{index:03d}.json")
        if not path.exists():
            return path
        index += 1


def _assert_no_coverage_latch(run_dir: Path, plan: dict) -> None:
    for receipt in sorted(run_dir.glob("execution_incomplete*.json")):
        if read_json(receipt).get("status") == "COVERAGE_STOP":
            raise LoopFailure(f"coverage stop latched by {receipt}; only read-only diagnosis is allowed",
                              status="COVERAGE_STOP")
    for failure in read_jsonl(run_dir / "failures.jsonl"):
        if failure.get("status") == "COVERAGE_STOP":
            raise LoopFailure("coverage stop latched in the failure ledger; no new execution is allowed",
                              status="COVERAGE_STOP")
    for block in plan["blocks"]:
        for spec_path in block["spec_paths"].values():
            arm_dir = Path(spec_path).parent
            for name in ("completion.json", "search_completion.json"):
                path = arm_dir / name
                if path.is_file():
                    result = read_json(path)
                    if result.get("status") == "COVERAGE_STOP" or result.get("coverage_stop") is not None:
                        raise LoopFailure(f"coverage stop latched in {path}; no new execution is allowed",
                                          status="COVERAGE_STOP")
            operations = read_jsonl(arm_dir / "operations.jsonl")
            if any(row.get("event") == "finished" and row.get("error_type") == "CoverageStop"
                   for row in operations):
                raise LoopFailure(f"coverage stop latched in {arm_dir}/operations.jsonl; no new execution is allowed",
                                  status="COVERAGE_STOP")
            checkpoints = read_jsonl(arm_dir / "checkpoints.jsonl")
            if checkpoints:
                state = checkpoints[-1]["state"]
                if state.get("status") == "COVERAGE_STOP" or state.get("coverage_stop") is not None:
                    raise LoopFailure(f"coverage stop latched in {arm_dir}; no new execution is allowed",
                                      status="COVERAGE_STOP")


def _execute_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    if manifest["frozen_hashes"]["code"]["files"] != code_hashes():
        raise ValueError("execution code differs from frozen code; create a new run instead")
    if (run_dir / "execution_receipt.json").is_file():
        return read_json(run_dir / "execution_receipt.json")
    if sha256_file(run_dir / "blind_map.json") != manifest["blind_map_sha256"]:
        raise ValueError("blind map drift")
    plan = read_json(run_dir / "execution_plan.json")
    prereg_path = Path(manifest["preregistration"]["path"])
    if sha256_file(prereg_path) != manifest["preregistration"]["sha256"]:
        raise ValueError("preregistration drift")
    prereg = read_json(prereg_path)
    if prereg["prepared_design_sha256"] != manifest["design_sha256"]:
        raise ValueError("preregistered design identity mismatch")
    if sha256_file(run_dir / "design.json") != manifest["design_sha256"]:
        raise ValueError("design file drift")
    design = read_json(run_dir / "design.json")
    if plan["blocks"] != design["blocks"] or plan["workers"] != design["workers"]:
        raise ValueError("execution plan differs from the frozen design")
    expected_specs = {path for block in plan["blocks"] for path in block["spec_paths"].values()}
    if set(plan["spec_hashes"]) != expected_specs:
        raise ValueError("execution plan spec set mismatch")
    for path, expected_hash in plan["spec_hashes"].items():
        if sha256_file(Path(path)) != expected_hash:
            raise ValueError(f"run spec hash mismatch: {path}")
    for path, expected_hash in design["synthetic_spec_hashes"].items():
        if plan["spec_hashes"].get(path) != expected_hash:
            raise ValueError(f"synthetic run spec differs from preregistration: {path}")
    if not manifest.get("test_only") and not plan["real_data_materialized"]:
        raise PermissionError("real-dev materialization is required before any formal synthetic outcomes")
    if {_key(block) for block in plan["blocks"]} != set(manifest["planned_blocks"]):
        raise ValueError("planned blocks differ from preregistration")
    _assert_no_coverage_latch(run_dir, plan)
    started = time.perf_counter()
    stop_event = threading.Event()
    blinded = []
    provenance = {}
    failures = []
    running = {}
    index = 0
    if not manifest.get("test_only"):
        first = plan["blocks"][0]
        if first["domain"] != "real_dev" or first["block_id"] != "fold_00_pair_00":
            raise ValueError("formal execution must begin with the registered real-dev infrastructure block")
        try:
            result, sources = _run_block(run_dir, first, stop_event)
        except Exception as exc:
            failures.append({"block": _key(first), "status": getattr(exc, "status", "INCOMPLETE"),
                             "error": str(exc)[:4000], "stage": "real_dev_infrastructure_gate"})
            stop_event.set()
            append_jsonl(run_dir / "failures.jsonl", failures[-1])
        else:
            blinded.append(result)
            provenance[_key(first)] = sources
            append_jsonl(run_dir / "execution_ledger.jsonl", {
                "block": _key(first), "status": "COMPLETED", "created_at": utc_now(),
                "stage": "real_dev_infrastructure_gate"})
        index = 1
    with ThreadPoolExecutor(max_workers=plan["workers"]) as executor:
        try:
            while index < len(plan["blocks"]) or running:
                while index < len(plan["blocks"]) and len(running) < plan["workers"] and not stop_event.is_set():
                    block = plan["blocks"][index]
                    future = executor.submit(_run_block, run_dir, block, stop_event)
                    running[future] = block
                    index += 1
                if not running:
                    break
                done, _ = wait(running, return_when=FIRST_COMPLETED)
                for future in done:
                    block = running.pop(future)
                    key = _key(block)
                    try:
                        result, sources = future.result()
                    except Exception as exc:
                        failures.append({"block": key, "status": getattr(exc, "status", "INCOMPLETE"),
                                         "error": str(exc)[:4000]})
                        stop_event.set()
                        append_jsonl(run_dir / "failures.jsonl", failures[-1])
                    else:
                        blinded.append(result)
                        provenance[_key(block)] = sources
                        append_jsonl(run_dir / "execution_ledger.jsonl", {
                            "block": key, "status": "COMPLETED", "created_at": utc_now()})
        except BaseException:
            stop_event.set()
            raise
    order = {_key(block): position for position, block in enumerate(plan["blocks"])}
    blinded.sort(key=lambda block: order[_key(block)])
    if not failures and len(blinded) == len(plan["blocks"]):
        write_immutable_json(run_dir / "blinded_results.json", {
            "schema": "AUTO-RESEARCH-LIVE-BLINDED-RESULTS-1", "status": "COMPLETED",
            "test_only": manifest.get("test_only", False), "blocks": blinded})
        write_immutable_json(run_dir / "provenance.json", provenance)
        receipt = {
            "schema": "AUTO-RESEARCH-LIVE-EXECUTION-RECEIPT-1", "created_at": utc_now(),
            "status": "COMPLETED", "n_blocks": len(blinded),
            "n_loops_completed": len(blinded) * 2, "n_loops_planned": plan["n_loops"],
            "workers": plan["workers"], "wall_seconds": time.perf_counter() - started,
            "blinded_results_sha256": sha256_file(run_dir / "blinded_results.json"),
            "provenance_sha256": sha256_file(run_dir / "provenance.json"),
            "real_data_materialized": plan["real_data_materialized"],
            "score_window_read": False, "oos_window_read": False, "embargo_read": False,
            "primary_analysis_performed": False,
            "artifact_hashes": file_hashes(run_dir),
        }
        write_immutable_json(run_dir / "execution_receipt.json", receipt)
        return receipt
    partial = _next_path(run_dir, "blinded_results_partial")
    write_immutable_json(partial, {"schema": "AUTO-RESEARCH-LIVE-BLINDED-RESULTS-1",
                                   "status": "INCOMPLETE", "blocks": blinded})
    status = "COVERAGE_STOP" if any(row["status"] == "COVERAGE_STOP" for row in failures) else "INCOMPLETE"
    receipt = {
        "schema": "AUTO-RESEARCH-LIVE-EXECUTION-RECEIPT-1", "created_at": utc_now(),
        "status": status, "n_blocks": len(blinded), "n_loops_completed": len(blinded) * 2,
        "n_loops_planned": plan["n_loops"], "failures": failures,
        "not_run_blocks": [_key(block) for block in plan["blocks"] if _key(block) not in {_key(row) for row in blinded}],
        "partial_blinded_results": {"path": str(partial), "sha256": sha256_file(partial)},
        "partial_provenance": provenance, "wall_seconds": time.perf_counter() - started,
        "primary_analysis_performed": False,
    }
    write_immutable_json(_next_path(run_dir, "execution_incomplete"), receipt)
    return receipt


def execute_run(run_dir: Path) -> dict:
    try:
        return _execute_run(run_dir)
    except BaseException as exc:
        record = {
            "schema": "AUTO-RESEARCH-LIVE-EXECUTION-RECEIPT-1", "created_at": utc_now(),
            "status": getattr(exc, "status", "INCOMPLETE"), "error": str(exc)[:4000],
            "error_type": type(exc).__name__, "stage": "execute_or_validation",
            "primary_analysis_performed": False,
        }
        append_jsonl(Path(run_dir) / "failures.jsonl", record)
        write_immutable_json(_next_path(Path(run_dir), "execution_incomplete"), record)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--stage", choices=("prepare", "materialize", "execute", "analyze", "all"), default="all")
    parser.add_argument("--test-smoke", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--smoke-receipt", type=Path)
    args = parser.parse_args()
    run_dir = Path(args.output_root) / args.run_id
    if args.stage in {"prepare", "all"} and not (run_dir / "manifest.json").is_file():
        prepare_run(args.run_id, args.output_root, reasoning_effort=args.reasoning_effort,
                    test_smoke=args.test_smoke, workers=args.workers, smoke_receipt=args.smoke_receipt)
    if args.stage in {"materialize", "all"} and not args.test_smoke and not (run_dir / "execution_plan.json").is_file():
        materialize_real(run_dir)
    result = None
    if args.stage in {"execute", "all"}:
        result = execute_run(run_dir)
    if args.stage == "analyze" or (args.stage == "all" and result.get("status") == "COMPLETED"):
        result = analyze_run(run_dir)
    if args.stage == "prepare":
        result = read_json(run_dir / "manifest.json")
    elif args.stage == "materialize":
        result = read_json(run_dir / "execution_plan.json")
    print(json.dumps(result, ensure_ascii=False, indent=1, allow_nan=False))


if __name__ == "__main__":
    main()
