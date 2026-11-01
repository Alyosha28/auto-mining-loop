#!/usr/bin/env python3
"""Matched live LLM MAP-Elites loops with evidence-gated or raw-feedback archives."""
from __future__ import annotations

import argparse
import copy
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
if str(ROOT / "auto_research/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research/scripts"))
import expr_search as es
from agents.expr_grammar import parse, validate
from agents.live_data import DEFAULT_WARMUP_WEEKS, SyntheticBundle, make_synthetic_bundle
from agents.live_evaluator import CoverageStop, EvaluationError, LiveEvaluator
from agents.live_llm import LLMRequestError, LLMResponse, LiveLLMClient, extract_all_json
from agents.live_manifest import (
    append_checkpoint,
    append_jsonl,
    code_hashes,
    create_run_directory,
    file_hashes,
    frozen_hashes,
    initialize_manifest,
    load_checkpoints,
    read_json,
    read_jsonl,
    safe_component,
    sha256_file,
    sha256_json,
    utc_now,
    validate_checkpoint_streams,
    validate_resume,
    write_immutable_json,
)

EVIDENCE = "evidence-gated"
RAW = "raw-control"
ARMS = (EVIDENCE, RAW)
PROMPT_SYSTEM = """You are a quantitative factor researcher performing one mechanism-preserving mutation.
Output ONLY one JSON object with keys expression, rationale, expected_ic, falsification.
Use only the supplied feature names and this grammar: unary rank/zscore/winsorize/vol_scaled/tanh/sign/residual_size/delta4/delta13/delta_w/ema5/lag5/neg; binary ratio/diff/rank_minus/product.
Keep the parent's economic mechanism; change its operator/horizon or combine it with one related signal. Do not propose the parent itself."""
PROMPT_USER = """Parent expression: {parent_formula}
Parent raw search feedback (not audit evidence): {parent_feedback}
Feature pool: {features}
Propose exactly one new expression. expected_ic is a signed hypothesis, not permission to change the evaluator's frozen direction.
Return ONLY the JSON object."""
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "expression": {"type": "string"},
        "rationale": {"type": "string"},
        "expected_ic": {"type": "number"},
        "falsification": {"type": "string"},
    },
    "required": ["expression", "rationale", "expected_ic", "falsification"],
    "additionalProperties": False,
}
RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "live_qd_mutation", "strict": True, "schema": RESPONSE_SCHEMA,
}}
ARCHIVE_CONTRACT = {
    "cell_key": "root_family,depth_bucket,turnover_bucket",
    "depth_bucket": "shallow<=2;mid<=4;deep>4",
    "turnover_bucket": "unknown;low<0.5;mid<0.8;high>=0.8",
    "parent_selection": "least-visited cells first (all evaluated offers counted), then descending search fitness and formula; uniform slot-seeded choice among the first five elites",
    "random_fallback": False,
    "audit_feedback_to_search": False,
}


def prompt_contract() -> dict:
    return {
        "schema": "AUTO-RESEARCH-LIVE-QD-PROMPT-1",
        "system": PROMPT_SYSTEM,
        "user_template": PROMPT_USER,
        "response_format": RESPONSE_FORMAT,
        "parser": "exactly one extracted top-level object; expression parsed by agents.expr_grammar",
        "schema_required": sorted(RESPONSE_SCHEMA["required"]),
        "additional_properties": False,
    }


def protocol_identity() -> str:
    identity = {
        "prompt": prompt_contract(),
        "archive": ARCHIVE_CONTRACT,
        "runner_sha256": sha256_file(Path(__file__)),
        "grammar_sha256": sha256_file(ROOT / "auto_research/agents/expr_grammar.py"),
    }
    return "LIVE-QD-" + sha256_json(identity)[:16]


def depth(node: dict) -> int:
    if node["kind"] == "leaf":
        return 1
    if node["kind"] == "unary":
        return 1 + depth(node["arg"])
    return 1 + max(depth(node["left"]), depth(node["right"]))


def root_family(node: dict) -> str:
    return node["op"] if node["kind"] != "leaf" else "leaf"


def turnover_bucket(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.5:
        return "low"
    if value < 0.8:
        return "mid"
    return "high"


def cell_key(node: dict, result: dict) -> tuple:
    d = depth(node)
    depth_bucket = "shallow" if d <= 2 else ("mid" if d <= 4 else "deep")
    return root_family(node), depth_bucket, turnover_bucket(result.get("turnover_proxy"))


@dataclass
class LiveLoopSpec:
    run_id: str
    task: str
    block_id: str
    arm: str
    agent_seed: int
    search_seed: int
    audit_seeds: tuple[int, ...]
    generations: int = 2
    slots_per_generation: int = 2
    initial_library: tuple[str, ...] = ("f00",)
    n_symbols: int | None = 40
    n_weeks: int = 32
    n_features: int = 8
    warmup_weeks: int = DEFAULT_WARMUP_WEEKS
    feature_names: tuple[str, ...] = ()
    search_fraction: float = 0.75
    n_audit_folds: int = 2
    nomination_cap: int = 4
    min_coverage: float = 0.96
    search_min_ic: float = 0.005
    search_min_icir: float = 0.10
    audit_min_ic: float = 0.02
    model: dict | None = None
    data_config: dict | None = None
    execution_order: int = 0
    test_mode: bool = True

    def __post_init__(self) -> None:
        self.initial_library = tuple(self.initial_library)
        self.audit_seeds = tuple(int(seed) for seed in self.audit_seeds)
        self.model = dict(self.model or mock_model_config())
        self.data_config = dict(self.data_config or {"kind": "synthetic"})
        data_kind = self.data_config["kind"]
        if data_kind not in {"synthetic", "real_dev"}:
            raise ValueError(f"unknown data kind: {data_kind}")
        if not self.feature_names:
            if self.task not in {"planted", "weak", "nonlinear", "regime_state", "null"} or self.n_symbols is None:
                raise ValueError("non-synthetic loops require an explicit feature contract")
            self.feature_names = tuple(f"f{index:02d}" for index in range(self.n_features))
            if self.task == "regime_state":
                self.feature_names = (*self.feature_names, "f_state")
        self.feature_names = tuple(self.feature_names)
        if len(set(self.feature_names)) != len(self.feature_names):
            raise ValueError("feature contract must be unique")
        if data_kind == "real_dev":
            if (self.task != "real_dev" or self.audit_seeds != (0,) or self.n_symbols is not None
                    or bool(self.data_config.get("test_only")) != bool(self.test_mode)):
                raise ValueError("real-dev data, seed, universe and TEST contracts disagree")
            if set(self.feature_names) != set(self.data_config.get("feature_names", [])):
                raise ValueError("real-dev spec and data feature contracts differ")
            if self.n_audit_folds != int(self.data_config.get("n_audit_folds", 2)):
                raise ValueError("real-dev audit fold count mismatch")
            if tuple(self.data_config.get("initial_library", [])) != self.initial_library:
                raise ValueError("real-dev initial library differs from the frozen data input")
        elif self.n_symbols is None:
            raise ValueError("synthetic loops require n_symbols")
        if self.arm not in ARMS:
            raise ValueError(f"unknown arm: {self.arm}")
        if self.generations < 1 or self.slots_per_generation < 1:
            raise ValueError("generations and slots must be positive")
        if self.generations * self.slots_per_generation > 24:
            raise ValueError("this preregistered runner supports at most 24 proposal slots")
        if self.min_coverage != 0.96 or self.audit_min_ic != 0.02:
            raise ValueError("coverage and audit thresholds are fixed at 0.96 and 0.02")
        if self.warmup_weeks < 0 or (not self.test_mode and self.warmup_weeks != DEFAULT_WARMUP_WEEKS):
            raise ValueError("formal loops use the frozen 312-week history contract")
        if not self.initial_library:
            raise ValueError("a fixed initial library is required")
        if self.test_mode and not self.run_id.startswith("TEST_"):
            raise ValueError("TEST loops require a TEST_ run_id")
        safe_component(self.run_id)
        safe_component(self.block_id)

    @property
    def proposal_slots(self) -> int:
        return self.generations * self.slots_per_generation

    @property
    def cache_scope(self) -> str:
        return f"{self.run_id}/{self.block_id}/{self.arm}"

    def matched_protocol(self) -> dict:
        return {
            "schema": "AUTO-RESEARCH-LIVE-QD-PROTOCOL-1",
            "protocol_id": protocol_identity(),
            "task": self.task,
            "proposal_slots": self.proposal_slots,
            "generations": self.generations,
            "slots_per_generation": self.slots_per_generation,
            "initial_library": list(self.initial_library),
            "feature_contract": {"feature_names": list(self.feature_names),
                                 "source": "frozen panel columns; no historical claims or KG"},
            "n_symbols": self.n_symbols,
            "n_weeks": self.n_weeks,
            "data_config": self.data_config,
            "search_fraction": self.search_fraction if self.data_config["kind"] == "synthetic" else None,
            "search_validation": self.data_config.get(
                "search_validation", "IC/ICIR gate on the search window; independent synthetic audit panels"),
            "warmup_weeks": self.warmup_weeks,
            "audit_seeds": list(self.audit_seeds),
            "n_audit_folds": self.n_audit_folds,
            "nomination_cap": self.nomination_cap,
            "coverage_min": self.min_coverage,
            "search_min_signed_ic": self.search_min_ic,
            "search_min_signed_icir": self.search_min_icir,
            "audit_min_signed_ic": self.audit_min_ic,
            "audit_aggregation": "n_dates-weighted mean of fixed seed/fold signed mean IC",
            "direction": "freeze sign of raw search mean IC; never re-optimize during audit",
            "archive_contract": ARCHIVE_CONTRACT,
            "arms": {
                EVIDENCE: "only schema/AST/coverage/search-valid frozen-direction elites become parents/nominations",
                RAW: "all AST/coverage-evaluable raw-search elites become parents/nominations",
            },
            "budget_semantics": "proposal slots exclude matched initial-library evaluations; every evaluator call is reported",
        }

    def synthetic_config(self) -> dict:
        if self.data_config["kind"] != "synthetic":
            raise ValueError("real-dev specs cannot be used to generate synthetic panels")
        return {
            "task": self.task,
            "search_seed": self.search_seed,
            "audit_seeds": list(self.audit_seeds),
            "n_symbols": self.n_symbols,
            "n_weeks": self.n_weeks,
            "n_features": self.n_features,
            "search_fraction": self.search_fraction,
            "n_audit_folds": self.n_audit_folds,
            "warmup_weeks": self.warmup_weeks,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "LiveLoopSpec":
        return cls(**payload)


def mock_model_config() -> dict:
    return {
        "provider": "injected-mock",
        "name": "TEST-mock-v1",
        "base_url": "http://127.0.0.1:1234/v1",
        "temperature": 0.2,
        "max_tokens": 768,
        "supports_explicit_seed": False,
        "seed": None,
    }


def live_model_config(name: str = "sage-a1.5", base_url: str = "http://127.0.0.1:1234/v1",
                      reasoning_effort: str | None = None) -> dict:
    if name != "sage-a1.5":
        raise ValueError("live runs require an explicitly registered local model with frozen weights")
    weights_file = Path.home() / ".lmstudio/models/InternScience/Sage-A1.5-Q4_K_M-GGUF/Sage-A1.5-Q4_K_M.gguf"
    weights_size = 21713463520
    if not weights_file.is_file() or weights_file.stat().st_size != weights_size:
        raise ValueError(f"Sage weights missing or size drift: {weights_file}")
    return {
        "provider": "lmstudio-loopback",
        "name": name,
        "base_url": base_url,
        "temperature": 0.2,
        "max_tokens": 4096,
        "reasoning_effort": reasoning_effort,
        "supports_explicit_seed": False,
        "seed": None,
        "weights_file": str(weights_file),
        "weights_sha256": "a64fe0bbf936db3eb54f66f142c2b2eee569ade83673649f549a21daef8ca395",
        "weights_size_bytes": weights_size,
    }


def bundle_from_spec(spec: LiveLoopSpec) -> SyntheticBundle:
    if spec.data_config["kind"] == "real_dev":
        from agents.live_realdev import load_real_dev_bundle
        return load_real_dev_bundle(spec.data_config)
    return make_synthetic_bundle(**spec.synthetic_config())


def _zero_llm_totals() -> dict:
    return {
        "logical_calls": 0,
        "actual_calls": 0,
        "non_cached_calls": 0,
        "cached_calls": 0,
        "cache_hits": 0,
        "infrastructure_retries": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def _zero_evaluator_calls() -> dict:
    return {"search_initial": 0, "search_proposals": 0, "audit": 0, "recompute": 0, "total": 0}


def _mean(values: list[float | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return float(np.mean(finite)) if finite else None


def _weighted_mean(rows: list[dict], key: str) -> float | None:
    pairs = [(float(row[key]), int(row["n_dates"])) for row in rows
             if row.get(key) is not None and math.isfinite(float(row[key])) and int(row["n_dates"]) > 0]
    denominator = sum(weight for _, weight in pairs)
    return sum(value * weight for value, weight in pairs) / denominator if denominator else None


def _metric_equal(left, right, tolerance: float = 1e-10) -> bool:
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)
    return left == right


class _LiveLoop:
    def __init__(self, spec: LiveLoopSpec, bundle: SyntheticBundle, llm, arm_dir: Path,
                 resume: bool = False, on_checkpoint: Callable[[dict], None] | None = None):
        self.spec = spec
        self.bundle = bundle
        if bundle.manifest.get("kind") != spec.data_config["kind"]:
            raise ValueError("bundle data kind differs from the frozen spec")
        if any(bundle.manifest.get(key) for key in ("score_window_read", "oos_window_read", "embargo_read")):
            raise PermissionError("closed score/OOS/embargo data is forbidden in live loops")
        if spec.data_config["kind"] == "real_dev" and not bundle.manifest.get("real_data_read"):
            raise ValueError("real-dev spec lacks its authorized real-data manifest")
        self.llm = llm
        self.arm_dir = Path(arm_dir)
        self.on_checkpoint = on_checkpoint
        self.started_wall = time.perf_counter()
        self.started_cpu = time.process_time()
        self.evaluator = self._new_evaluator()
        self.archive: dict[tuple, dict] = {}
        self.cell_counts: dict[tuple, int] = {}
        self.seen_canonical: set[str] = set()
        self.initial_rows: list[dict] = []
        self.proposals: list[dict] = []
        self.nominations: list[dict] = []
        self.audit_records: list[dict] = []
        self.recompute: dict | None = None
        self.next_generation = 0
        self.next_slot = 0
        self.phase = "initialize"
        self.status = "RUNNING"
        self.coverage_stop: dict | None = None
        self.llm_totals = _zero_llm_totals()
        self.evaluator_calls = _zero_evaluator_calls()
        self.wall_before_resume = 0.0
        self.cpu_before_resume = 0.0
        if getattr(llm, "cache_enabled", False):
            raise ValueError("matched live loops require cache_enabled=False; no cross-arm/run cache is permitted")
        self.features = sorted(es.features(bundle.search_panel))
        if set(self.features) != set(spec.feature_names):
            raise ValueError("panel features differ from the frozen feature contract")
        self.initial_canonical = set()
        for formula in spec.initial_library:
            node = parse(formula, set(self.features))
            validate(node, set(self.features))
            canonical = es.canonical(node)
            if canonical in self.initial_canonical:
                raise ValueError("initial library must be canonical unique")
            self.initial_canonical.add(canonical)
        if resume:
            self._restore()
        else:
            self._create()

    def _new_evaluator(self) -> LiveEvaluator:
        return LiveEvaluator(min_coverage=self.spec.min_coverage, search_min_ic=self.spec.search_min_ic,
                             search_min_icir=self.spec.search_min_icir,
                             pit_safe_dates=self.bundle.manifest.get("pit_safe_dates", ()),
                             assumption_label=self.bundle.manifest.get("assumption_label",
                                                                       "synthetic_terminal_label"))

    @property
    def paths(self) -> dict[str, Path]:
        return {
            "manifest": self.arm_dir / "manifest.json",
            "operations": self.arm_dir / "operations.jsonl",
            "proposals": self.arm_dir / "proposals.jsonl",
            "llm": self.arm_dir / "llm_responses.jsonl",
            "search": self.arm_dir / "search_evaluations.jsonl",
            "negative": self.arm_dir / "negative_memory.jsonl",
            "checkpoints": self.arm_dir / "checkpoints.jsonl",
            "nominations": self.arm_dir / "nominations.json",
            "search_completion": self.arm_dir / "search_completion.json",
            "audit": self.arm_dir / "audit.jsonl",
            "recompute": self.arm_dir / "recompute.json",
            "completion": self.arm_dir / "completion.json",
        }

    def _frozen(self) -> dict:
        return frozen_hashes(self.spec.matched_protocol(), self.spec.model, prompt_contract(),
                             self.bundle.manifest)

    def _seed_record(self) -> dict:
        if hasattr(self.llm, "seed_capabilities"):
            record = self.llm.seed_capabilities()
        else:
            record = {
                "explicit_seed_supported": bool(self.spec.model.get("supports_explicit_seed")),
                "configured_seed": self.spec.model.get("seed"),
                "seed_will_be_sent": False,
                "deterministic_sampling_claimed": False,
                "prompt_seed_is_determinism": False,
            }
        record["agent_seed_scope"] = "QD parent choice and randomized arm order only"
        return record

    def _create(self) -> None:
        if self.arm_dir.exists():
            def allowed(path: Path) -> bool:
                if path.name == "run_spec.json":
                    return True
                if path.name != "._run_spec.json" or path.is_symlink() or not path.is_file():
                    return False
                with path.open("rb") as handle:
                    return handle.read(8) == b"\x00\x05\x16\x07\x00\x02\x00\x00"

            unexpected = [path.name for path in self.arm_dir.iterdir() if not allowed(path)]
            if unexpected:
                raise FileExistsError(f"refusing to initialize a nonempty arm directory: {unexpected}")
        else:
            self.arm_dir.mkdir(parents=True, exist_ok=False)
        command = (f"python {Path(__file__).resolve()} --resume-arm {self.arm_dir.resolve()}")
        audit_command = f"{command} --audit-only"
        initialize_manifest(
            self.paths["manifest"], "arm", self._frozen(),
            run_id=self.spec.run_id,
            task=self.spec.task,
            block_id=self.spec.block_id,
            arm=self.spec.arm,
            arm_assignment={"execution_order": self.spec.execution_order, "agent_seed": self.spec.agent_seed},
            loop_spec=asdict(self.spec),
            llm_seed=self._seed_record(),
            streams={key: path.name for key, path in self.paths.items() if key != "manifest"},
            resume_command=command,
            audit_resume_command=audit_command,
            cache_policy="disabled_per_arm_and_run",
            random_fallback=False,
            historical_claims_read=bool(self.bundle.manifest.get("historical_claims_read", False)),
            kg_read=bool(self.bundle.manifest.get("kg_read", False)),
            real_data_read=bool(self.bundle.manifest.get("real_data_read", False)),
            score_window_read=bool(self.bundle.manifest.get("score_window_read", False)),
            oos_window_read=bool(self.bundle.manifest.get("oos_window_read", False)),
            embargo_read=bool(self.bundle.manifest.get("embargo_read", False)),
            independent_oos=False,
            audit_feedback_applied=False,
            wall_seconds=None,
            cpu_seconds=None,
            tokens=None,
            evaluator_calls=None,
        )

    def _restore(self) -> None:
        manifest = read_json(self.paths["manifest"])
        validate_resume(manifest, self._frozen())
        checkpoints = load_checkpoints(self.paths["checkpoints"])
        state = checkpoints[-1]["state"]
        proposal_rows = read_jsonl(self.paths["proposals"])
        validate_checkpoint_streams(checkpoints[-1], [row["proposal_id"] for row in proposal_rows])
        audit_rows = read_jsonl(self.paths["audit"])
        if [row["proposal_id"] for row in audit_rows] != state.get("audit_completed_ids", []):
            from agents.live_manifest import ResumeError
            raise ResumeError("audit stream and checkpoint differ; refusing to replay audit")
        self.initial_rows = state.get("initial_rows", [])
        self.archive = {}
        for row in state.get("archive", []):
            self.archive[tuple(row["cell_key"])] = row
        self.cell_counts = {}
        if state.get("cell_counts") is not None:
            self.cell_counts = {tuple(key): int(value) for key, value in state["cell_counts"]}
        else:
            for row in [*self.initial_rows, *proposal_rows]:
                if row.get("cell_key"):
                    key = tuple(row["cell_key"])
                    self.cell_counts[key] = self.cell_counts.get(key, 0) + 1
        self.seen_canonical = set(state.get("seen_canonical", []))
        self.proposals = proposal_rows
        self.next_generation = state["next_generation"]
        self.next_slot = state["next_slot"]
        self.phase = state["phase"]
        self.status = state["status"]
        self.coverage_stop = state.get("coverage_stop")
        self.llm_totals = {**_zero_llm_totals(), **state.get("llm_totals", {})}
        self.evaluator_calls = {**_zero_evaluator_calls(), **state.get("evaluator_calls", {})}
        self.wall_before_resume = float(state.get("elapsed_wall", 0.0))
        self.cpu_before_resume = float(state.get("elapsed_cpu", 0.0))
        self.audit_records = state.get("audit_records", audit_rows)
        if self.paths["nominations"].is_file():
            expected_hash = state.get("nominations_sha256")
            if expected_hash and sha256_file(self.paths["nominations"]) != expected_hash:
                from agents.live_manifest import ResumeError
                raise ResumeError("frozen nominations hash mismatch")
            self.nominations = read_json(self.paths["nominations"])["nominations"]
        elif self.phase in {"audit", "recompute", "complete"}:
            from agents.live_manifest import ResumeError
            raise ResumeError("audit phase requires frozen nominations")
        if self.paths["recompute"].is_file():
            self.recompute = read_json(self.paths["recompute"])
        self._validate_recovery_streams()

    def _validate_recovery_streams(self) -> None:
        from agents.live_manifest import ResumeError
        expected = [("llm", row["proposal_id"]) for row in self.proposals]
        expected.extend(("search_initial", row["proposal_id"]) for row in self.initial_rows)
        expected.extend(("search_proposals", row["proposal_id"]) for row in self.proposals
                        if row.get("search") is not None or row["status"] in {"COVERAGE_STOP", "EVALUATION_ERROR"})
        if self.coverage_stop and self.coverage_stop["proposal_id"].startswith("INITIAL-"):
            expected.append(("search_initial", self.coverage_stop["proposal_id"]))
        for audit in self.audit_records:
            expected.extend(("audit", f"{audit['proposal_id']}:{fold['audit_seed']}:{fold['fold_id']}")
                            for fold in audit["folds"])
            stopped = audit.get("coverage_stop")
            if stopped:
                expected.append(("audit", f"{audit['proposal_id']}:{stopped['audit_seed']}:{stopped['fold_id']}"))
        if self.recompute:
            expected.extend(("recompute", f"{row['proposal_id']}:{row['audit_seed']}:{row['fold_id']}")
                            for row in self.recompute["fold_checks"])
        actual = []
        active = None
        for row in read_jsonl(self.paths["operations"]):
            operation = (row["kind"], row["operation_id"])
            if row["event"] == "started":
                if active is not None:
                    raise ResumeError("overlapping operation journal entries; recovery refused")
                active = operation
            elif row["event"] == "finished":
                if active != operation:
                    raise ResumeError("operation journal start/finish mismatch; recovery refused")
                actual.append(operation)
                active = None
            else:
                raise ResumeError("unknown operation journal event; recovery refused")
        if active is not None:
            raise ResumeError("unfinished model/evaluation operation: actual cost unknown; replay refused")
        if sorted(actual) != sorted(expected):
            raise ResumeError("operation journal and checkpoint differ; refusing to repeat uncheckpointed work")
        evaluator_calls = _zero_evaluator_calls()
        for kind, _ in actual:
            if kind == "llm":
                continue
            if kind not in evaluator_calls:
                raise ResumeError(f"unknown evaluator operation kind: {kind}")
            evaluator_calls[kind] += 1
            evaluator_calls["total"] += 1
        self.evaluator_calls = evaluator_calls
        llm_ids = [row["proposal_id"] for row in read_jsonl(self.paths["llm"])]
        if llm_ids != [row["proposal_id"] for row in self.proposals]:
            raise ResumeError("uncheckpointed LLM response: actual cost must not be replayed or undercounted")
        search_ids = [row["proposal_id"] for row in read_jsonl(self.paths["search"])]
        expected_search = [row["proposal_id"] for row in self.initial_rows]
        expected_search.extend(row["proposal_id"] for row in self.proposals
                               if row.get("search") is not None or row["status"] == "COVERAGE_STOP")
        if self.coverage_stop and self.coverage_stop["proposal_id"].startswith("INITIAL-"):
            expected_search.append(self.coverage_stop["proposal_id"])
        if search_ids != expected_search:
            raise ResumeError("search evaluation stream and checkpoint differ; replay refused")

    def _elapsed(self) -> tuple[float, float]:
        return (self.wall_before_resume + time.perf_counter() - self.started_wall,
                self.cpu_before_resume + time.process_time() - self.started_cpu)

    def _state(self) -> dict:
        wall, cpu = self._elapsed()
        return {
            "schema": "AUTO-RESEARCH-LIVE-QD-STATE-1",
            "next_generation": self.next_generation,
            "next_slot": self.next_slot,
            "phase": self.phase,
            "status": self.status,
            "coverage_stop": self.coverage_stop,
            "archive": list(self.archive.values()),
            "cell_counts": [(list(key), value) for key, value in self.cell_counts.items()],
            "seen_canonical": sorted(self.seen_canonical),
            "initial_rows": self.initial_rows,
            "proposal_ids": [row["proposal_id"] for row in self.proposals],
            "audit_completed_ids": [row["proposal_id"] for row in self.audit_records],
            "audit_records": self.audit_records,
            "nominations_sha256": sha256_file(self.paths["nominations"]) if self.paths["nominations"].is_file() else None,
            "llm_totals": self.llm_totals,
            "evaluator_calls": self.evaluator_calls,
            "elapsed_wall": wall,
            "elapsed_cpu": cpu,
        }

    def _checkpoint(self) -> None:
        record = append_checkpoint(self.paths["checkpoints"], self._state())
        if self.on_checkpoint is not None:
            self.on_checkpoint(record)

    def _eval_call(self, kind: str) -> None:
        self.evaluator_calls[kind] += 1
        self.evaluator_calls["total"] += 1

    def _start_operation(self, kind: str, operation_id: str, evaluator: bool = True) -> dict:
        operation = {"kind": kind, "operation_id": operation_id}
        append_jsonl(self.paths["operations"], {**operation, "event": "started"})
        if evaluator:
            self._eval_call(kind)
        return operation

    def _finish_operation(self, operation: dict, outcome: str, error_type: str | None = None) -> None:
        append_jsonl(self.paths["operations"], {
            **operation, "event": "finished", "outcome": outcome, "error_type": error_type,
        })

    def _eval_operation(self, kind: str, operation_id: str):
        loop = self

        class Operation:
            def __enter__(self):
                self.operation = loop._start_operation(kind, operation_id)

            def __exit__(self, exception_type, exception, traceback):
                loop._finish_operation(self.operation, "ERROR" if exception else "COMPLETED",
                                       exception_type.__name__ if exception_type else None)

        return Operation()

    def _row(self, proposal_id: str, node: dict, metrics: dict, source: str, parent_id: str | None,
             generation: int | None, slot: int | None, schema_valid: bool) -> dict:
        return {
            "proposal_id": proposal_id,
            "formula": metrics["formula"],
            "node": node,
            "source": source,
            "parent_id": parent_id,
            "generation": generation,
            "slot": slot,
            "schema_valid": bool(schema_valid),
            "search": metrics,
            "search_fitness": metrics["search_fitness"],
            "turnover_proxy": metrics["turnover_proxy"],
            "direction": metrics["direction"],
            "cell_key": list(cell_key(node, metrics)),
        }

    def _evidence_eligible(self, row: dict) -> bool:
        metrics = row["search"]
        return bool(row["schema_valid"] and metrics["search_valid"] and metrics["direction_frozen"]
                    and metrics["coverage"] >= self.spec.min_coverage)

    def _offer(self, row: dict) -> bool:
        key = tuple(row["cell_key"])
        self.cell_counts[key] = self.cell_counts.get(key, 0) + 1
        if self.spec.arm == EVIDENCE and not self._evidence_eligible(row):
            return False
        incumbent = self.archive.get(key)
        if incumbent is None or row["search_fitness"] > incumbent["search_fitness"]:
            self.archive[key] = row
            return True
        return False

    def _coverage_record(self, exc: CoverageStop, proposal_id: str | None) -> dict:
        return {
            "proposal_id": proposal_id,
            "phase": exc.phase,
            "formula": exc.formula,
            "coverage": exc.coverage,
            "n_dates": exc.n_dates,
            "planned_dates": exc.planned_dates,
            "coverage_detail": exc.detail,
            "action": "STOP_NO_SEED_REPLACEMENT_NO_RULE_CHANGE",
        }

    def _initialize(self) -> None:
        self.phase = "initialize"
        for index, formula in enumerate(self.spec.initial_library):
            node = parse(formula, set(self.features))
            validate(node, set(self.features))
            try:
                with self._eval_operation("search_initial", f"INITIAL-{index:03d}"):
                    metrics = self.evaluator.search(node, self.bundle.search_panel, self.bundle.search_dates)
            except CoverageStop as exc:
                self.status = "COVERAGE_STOP"
                self.phase = "stopped"
                self.coverage_stop = self._coverage_record(exc, f"INITIAL-{index:03d}")
                append_jsonl(self.paths["search"], self.coverage_stop)
                self._checkpoint()
                return
            row = self._row(f"INITIAL-{index:03d}", node, metrics, "initial_library", None,
                            None, None, True)
            self.initial_rows.append(row)
            self._offer(row)
            append_jsonl(self.paths["search"], {"event": "initial_evaluation", **row})
            if self.spec.arm == EVIDENCE and not self._evidence_eligible(row):
                append_jsonl(self.paths["negative"], {
                    "proposal_id": row["proposal_id"], "formula": row["formula"],
                    "reason": "initial_library_failed_search_gate", "search": metrics,
                })
        self.phase = "search"
        self.next_generation = 0
        self.next_slot = 0
        self._checkpoint()

    def _select_parent(self, generation: int, slot: int) -> dict:
        elites = sorted(self.archive.values(), key=lambda row: (
            self.cell_counts.get(tuple(row["cell_key"]), 0), -row["search_fitness"], row["formula"]))
        if not elites:
            raise EvaluationError("no trusted archive parent" if self.spec.arm == EVIDENCE else "no raw archive parent")
        rng = random.Random(f"{self.spec.agent_seed}:{generation}:{slot}")
        return rng.choice(elites[:max(1, min(5, len(elites)))])

    def _messages(self, parent: dict) -> list[dict]:
        feedback = {
            "raw_mean_ic": parent["search"]["raw_mean_ic"],
            "frozen_direction": parent["search"]["direction"],
            "signed_mean_ic": parent["search"]["signed_mean_ic"],
            "icir": parent["search"]["icir"],
            "turnover": parent["search"]["turnover"],
            "coverage": parent["search"]["coverage"],
            "search_valid": parent["search"]["search_valid"],
        }
        user = PROMPT_USER.format(parent_formula=parent["formula"],
                                  parent_feedback=json.dumps(feedback, sort_keys=True),
                                  features=", ".join(sorted(self.features)))
        return [{"role": "system", "content": PROMPT_SYSTEM}, {"role": "user", "content": user}]

    def _add_llm_response_cost(self, response: LLMResponse) -> None:
        self.llm_totals["logical_calls"] += 1
        self.llm_totals["actual_calls"] += response.actual_calls
        self.llm_totals["non_cached_calls"] += int(not response.cached)
        self.llm_totals["cached_calls"] += int(response.cached)
        self.llm_totals["cache_hits"] += response.cache_hits
        self.llm_totals["infrastructure_retries"] += response.infrastructure_retries
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.llm_totals[key] += int(response.tokens.get(key) or 0)

    def _add_llm_error_cost(self, exc: LLMRequestError) -> None:
        self.llm_totals["logical_calls"] += 1
        self.llm_totals["actual_calls"] += exc.actual_calls
        self.llm_totals["non_cached_calls"] += 1
        self.llm_totals["infrastructure_retries"] += max(0, exc.actual_calls - 1)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self.llm_totals[key] += int(exc.tokens.get(key) or 0)

    def _parse_item(self, content: str) -> tuple[dict | None, bool, str | None]:
        extracted = extract_all_json(content)
        if len(extracted) != 1 or not isinstance(extracted[0], dict):
            return None, False, "expected exactly one top-level JSON object"
        item = extracted[0]
        schema_valid = bool(
            set(item) == set(RESPONSE_SCHEMA["required"])
            and isinstance(item.get("expression"), str) and bool(item.get("expression", "").strip())
            and isinstance(item.get("rationale"), str) and bool(item.get("rationale", "").strip())
            and isinstance(item.get("falsification"), str) and bool(item.get("falsification", "").strip())
            and isinstance(item.get("expected_ic"), (int, float)) and not isinstance(item.get("expected_ic"), bool)
            and math.isfinite(float(item.get("expected_ic")))
        )
        return item, schema_valid, None if schema_valid else "proposal schema mismatch"

    def _advance(self) -> None:
        if self.next_slot + 1 < self.spec.slots_per_generation:
            self.next_slot += 1
        else:
            self.next_generation += 1
            self.next_slot = 0
        if self.next_generation >= self.spec.generations and self.phase == "search":
            self.phase = "nominations"

    def _one_slot(self) -> None:
        generation, slot = self.next_generation, self.next_slot
        proposal_id = f"P-G{generation:02d}-S{slot:02d}"
        parent = self._select_parent(generation, slot)
        messages = self._messages(parent)
        base = {
            "proposal_id": proposal_id,
            "generation": generation,
            "slot": slot,
            "parent_id": parent["proposal_id"],
            "parent_formula": parent["formula"],
            "parent_cell_key": parent["cell_key"],
            "parent_search_valid": parent["search"]["search_valid"],
            "parent_evidence_verified": self.spec.arm != EVIDENCE or self._evidence_eligible(parent),
            "agent_proposed": True,
            "random_fallback": False,
            "model": self.spec.model["name"],
            "prompt_hash": sha256_json(messages),
            "prompt_contract_hash": sha256_json(prompt_contract()),
        }
        llm_operation = self._start_operation("llm", proposal_id, evaluator=False)
        try:
            response = self.llm.chat(
                messages,
                model=self.spec.model["name"],
                temperature=float(self.spec.model.get("temperature", 0.2)),
                max_tokens=int(self.spec.model.get("max_tokens", 768)),
                response_format=RESPONSE_FORMAT,
                seed=self.spec.model.get("seed"),
            )
        except LLMRequestError as exc:
            self._add_llm_error_cost(exc)
            failure = {**base, "status": "LLM_ERROR", "error": str(exc), "attempts": exc.attempts,
                       "failed_attempt": True, "raw_response": None}
            self.proposals.append(failure)
            append_jsonl(self.paths["llm"], {**base, "request_messages": messages,
                                             "error": str(exc), "attempts": exc.attempts})
            append_jsonl(self.paths["proposals"], failure)
            self._finish_operation(llm_operation, "LLM_ERROR")
            self._advance()
            self._checkpoint()
            return
        except Exception as exc:
            self.llm_totals["logical_calls"] += 1
            failure = {**base, "status": "LLM_ERROR", "error": str(exc)[:1000],
                       "attempts": [{"attempt": 0, "status": "error", "error": str(exc)[:1000]}],
                       "failed_attempt": True, "raw_response": None}
            self.proposals.append(failure)
            append_jsonl(self.paths["llm"], {**base, "request_messages": messages, "error": str(exc)[:1000]})
            append_jsonl(self.paths["proposals"], failure)
            self._finish_operation(llm_operation, "LLM_ERROR")
            self._advance()
            self._checkpoint()
            return
        self._add_llm_response_cost(response)
        record = {
            **base,
            "raw_response": response.content,
            "raw_response_hash": response.raw_hash,
            "usage": response.usage,
            "actual_llm_calls": response.actual_calls,
            "cache_hits": response.cache_hits,
            "infrastructure_retries": response.infrastructure_retries,
            "attempts": response.attempts,
            "failed_attempt": False,
        }
        append_jsonl(self.paths["llm"], {**base, "request_messages": messages, "response": response.to_dict()})
        self._finish_operation(llm_operation, "RESPONSE_LOGGED")
        item, schema_valid, schema_error = self._parse_item(response.content)
        record["schema_valid"] = schema_valid
        if item is None:
            record.update(status="REJECTED_PARSE", error=schema_error, rationale=None,
                          expected_ic=None, falsification=None, ast=None)
            self.proposals.append(record)
            append_jsonl(self.paths["proposals"], record)
            self._advance()
            self._checkpoint()
            return
        expression = item.get("expression") if isinstance(item.get("expression"), str) else ""
        record.update(rationale=item.get("rationale"), expected_ic=item.get("expected_ic"),
                      falsification=item.get("falsification"), expression=expression,
                      schema_error=schema_error)
        try:
            node = parse(expression, set(self.features))
            validate(node, set(self.features))
        except Exception as exc:
            record.update(status="REJECTED_AST", error=str(exc), ast=None)
            self.proposals.append(record)
            append_jsonl(self.paths["proposals"], record)
            self._advance()
            self._checkpoint()
            return
        canonical = es.canonical(node)
        record.update(ast=node, formula=canonical)
        if canonical in self.initial_canonical or canonical in self.seen_canonical:
            duplicate_of = "initial_library" if canonical in self.initial_canonical else "prior_proposal"
            record.update(status="DUPLICATE", duplicate_of=duplicate_of)
            self.proposals.append(record)
            append_jsonl(self.paths["proposals"], record)
            self._advance()
            self._checkpoint()
            return
        self.seen_canonical.add(canonical)
        try:
            with self._eval_operation("search_proposals", proposal_id):
                metrics = self.evaluator.search(node, self.bundle.search_panel, self.bundle.search_dates)
        except CoverageStop as exc:
            record.update(status="COVERAGE_STOP", coverage=exc.coverage, n_dates=exc.n_dates,
                          planned_dates=exc.planned_dates, coverage_detail=exc.detail,
                          error=str(exc), failed_attempt=True)
            self.proposals.append(record)
            self.status = "COVERAGE_STOP"
            self.coverage_stop = self._coverage_record(exc, proposal_id)
            append_jsonl(self.paths["search"], {**self.coverage_stop, "ast": node})
            append_jsonl(self.paths["proposals"], record)
            self._advance()
            self.phase = "stopped"
            self._checkpoint()
            return
        except EvaluationError as exc:
            record.update(status="EVALUATION_ERROR", error=str(exc), failed_attempt=True)
            self.proposals.append(record)
            append_jsonl(self.paths["proposals"], record)
            self._advance()
            self._checkpoint()
            return
        row = self._row(proposal_id, node, metrics, "live_llm_mutation", parent["proposal_id"],
                        generation, slot, schema_valid)
        record.update(search=metrics, direction=metrics["direction"], search_fitness=metrics["search_fitness"],
                      cell_key=row["cell_key"], evidence_eligible=self._evidence_eligible(row))
        archived = self._offer(row)
        if self.spec.arm == EVIDENCE and not schema_valid:
            record["status"] = "REJECTED_SCHEMA"
        elif self.spec.arm == EVIDENCE and not metrics["search_valid"]:
            record["status"] = "SEARCH_INVALID"
        elif archived:
            record["status"] = "ARCHIVED_EVIDENCE" if self.spec.arm == EVIDENCE else "ARCHIVED_RAW"
        else:
            record["status"] = "SEARCH_VALID_NOT_ELITE" if self.spec.arm == EVIDENCE else "EVALUATED_NOT_ELITE"
        record["archive_inserted"] = archived
        self.proposals.append(record)
        append_jsonl(self.paths["search"], {"event": "proposal_evaluation", **row,
                                            "proposal_status": record["status"]})
        if self.spec.arm == EVIDENCE and not self._evidence_eligible(row):
            append_jsonl(self.paths["negative"], {
                "proposal_id": proposal_id, "formula": canonical,
                "reason": record["status"], "search": metrics,
                "not_success_evidence": True,
            })
        append_jsonl(self.paths["proposals"], record)
        self._advance()
        self._checkpoint()

    def _freeze_nominations(self) -> None:
        rows = [row for row in self.archive.values() if row["source"] != "initial_library"]
        rows.sort(key=lambda row: (-row["search_fitness"], row["formula"]))
        seen = set()
        nominations = []
        for row in rows:
            if row["formula"] in seen:
                continue
            seen.add(row["formula"])
            if self.spec.arm == EVIDENCE and not self._evidence_eligible(row):
                continue
            nominations.append({
                "proposal_id": row["proposal_id"],
                "formula": row["formula"],
                "ast": row["node"],
                "direction": row["direction"],
                "parent_id": row["parent_id"],
                "generation": row["generation"],
                "slot": row["slot"],
                "search": row["search"],
                "audit_input_sha256": sha256_json({
                    "formula": row["formula"], "direction": row["direction"],
                    "audit_seeds": list(self.spec.audit_seeds),
                    "folds": {str(seed): sorted(folds) for seed, folds in self.bundle.audit_folds.items()},
                    "data_hashes": self.bundle.data_hashes,
                }),
            })
            if len(nominations) >= self.spec.nomination_cap:
                break
        self.nominations = nominations
        write_immutable_json(self.paths["nominations"], {
            "schema": "AUTO-RESEARCH-LIVE-QD-NOMINATIONS-1",
            "created_at": utc_now(),
            "arm": self.spec.arm,
            "frozen_before_audit": True,
            "audit_feedback_applied": False,
            "nominations": nominations,
        })
        self.phase = "audit"
        self._checkpoint()

    def _audit_one(self, nomination: dict, formula_counts: dict, evaluator: LiveEvaluator) -> dict:
        expected = [(int(seed), fold_id) for seed in self.spec.audit_seeds
                    for fold_id in sorted(self.bundle.audit_folds[int(seed)])]
        folds = []
        coverage_failure = None
        for seed, fold_id in expected:
            try:
                with self._eval_operation("audit", f"{nomination['proposal_id']}:{seed}:{fold_id}"):
                    metrics = evaluator.audit(nomination["ast"], self.bundle.get_audit_panel(seed),
                                              self.bundle.audit_folds[seed][fold_id], nomination["direction"])
                folds.append({"audit_seed": seed, "fold_id": fold_id, **metrics})
            except CoverageStop as exc:
                coverage_failure = self._coverage_record(exc, nomination["proposal_id"])
                coverage_failure.update(audit_seed=seed, fold_id=fold_id)
                break
        actual = [(row["audit_seed"], row["fold_id"]) for row in folds]
        complete = actual == expected and coverage_failure is None
        coverage_ok = complete and all(row["coverage"] >= self.spec.min_coverage for row in folds)
        signed_mean = _weighted_mean(folds, "signed_mean_ic") if complete else None
        checks = {
            "not_in_initial_library": nomination["formula"] not in self.initial_canonical,
            "canonical_unique": formula_counts[nomination["formula"]] == 1,
            "fixed_audit_seeds_complete": complete,
            "coverage_ok": coverage_ok,
            "signed_mean_ic_ok": signed_mean is not None and signed_mean >= self.spec.audit_min_ic,
            "frozen_direction_preserved": bool(folds) and all(row["direction"] == nomination["direction"]
                                                                for row in folds),
        }
        confirmed = all(checks.values())
        return {
            "proposal_id": nomination["proposal_id"],
            "formula": nomination["formula"],
            "direction": nomination["direction"],
            "expected_audit_folds": [{"audit_seed": seed, "fold_id": fold} for seed, fold in expected],
            "actual_audit_folds": [{"audit_seed": seed, "fold_id": fold} for seed, fold in actual],
            "folds": folds,
            "signed_mean_ic": signed_mean,
            "icir": _weighted_mean(folds, "icir") if complete else None,
            "turnover": _mean([row["turnover"] for row in folds]) if complete else None,
            "coverage": (sum(row["n_dates"] for row in folds) / sum(row["planned_dates"] for row in folds)
                         if complete else None),
            "n_dates": sum(row["n_dates"] for row in folds),
            "checks": checks,
            "audit_confirmed": confirmed,
            "coverage_stop": coverage_failure,
            "status": "COVERAGE_STOP" if coverage_failure else "AUDITED",
            "audit_input_sha256": nomination["audit_input_sha256"],
        }

    def _audit_all(self) -> None:
        evaluator = self._new_evaluator()
        counts = {}
        for nomination in self.nominations:
            counts[nomination["formula"]] = counts.get(nomination["formula"], 0) + 1
        completed = {row["proposal_id"] for row in self.audit_records}
        by_id = {row["proposal_id"]: row for row in self.audit_records}
        for nomination in self.nominations:
            if nomination["proposal_id"] in completed:
                continue
            record = self._audit_one(nomination, counts, evaluator)
            self.audit_records.append(record)
            by_id[record["proposal_id"]] = record
            append_jsonl(self.paths["audit"], record)
            if record["status"] == "COVERAGE_STOP":
                self.status = "COVERAGE_STOP"
                self.phase = "stopped"
                self.coverage_stop = record["coverage_stop"]
                self._checkpoint()
                return
            self._checkpoint()
        self.audit_records = [by_id[row["proposal_id"]] for row in self.nominations]
        self.phase = "recompute"
        self._checkpoint()

    def _recompute_all(self) -> None:
        if self.recompute is not None:
            self.phase = "complete"
            self._checkpoint()
            return
        evaluator = self._new_evaluator()
        nominations = {row["proposal_id"]: row for row in self.nominations}
        checks = []
        for audit in self.audit_records:
            nomination = nominations[audit["proposal_id"]]
            for frozen_fold in audit["folds"]:
                seed, fold_id = frozen_fold["audit_seed"], frozen_fold["fold_id"]
                formula = nomination["formula"]
                node = parse(formula, set(es.features(self.bundle.get_audit_panel(seed))))
                validate(node, set(es.features(self.bundle.get_audit_panel(seed))))
                with self._eval_operation("recompute", f"{audit['proposal_id']}:{seed}:{fold_id}"):
                    recomputed = evaluator.audit(node, self.bundle.get_audit_panel(seed),
                                                 self.bundle.audit_folds[seed][fold_id], nomination["direction"])
                comparison = {key: _metric_equal(frozen_fold.get(key), recomputed.get(key))
                              for key in ("direction", "signed_mean_ic", "icir", "turnover", "coverage", "n_dates")}
                checks.append({"proposal_id": audit["proposal_id"], "audit_seed": seed, "fold_id": fold_id,
                               "checks": comparison, "consistent": all(comparison.values()),
                               "recomputed": {key: recomputed.get(key) for key in comparison}})
        aggregate_checks = []
        for audit in self.audit_records:
            if audit["status"] != "AUDITED":
                continue
            recomputed_signed = _weighted_mean(audit["folds"], "signed_mean_ic")
            consistent = _metric_equal(audit["signed_mean_ic"], recomputed_signed)
            aggregate_checks.append({"proposal_id": audit["proposal_id"], "consistent": consistent})
        n_checks = len(checks) + len(aggregate_checks)
        n_consistent = sum(row["consistent"] for row in checks + aggregate_checks)
        self.recompute = {
            "schema": "AUTO-RESEARCH-LIVE-QD-RECOMPUTE-1",
            "created_at": utc_now(),
            "source": "new LiveEvaluator; formulas parsed from frozen nominations; bundle data hashes resume-validated",
            "fold_checks": checks,
            "aggregate_checks": aggregate_checks,
            "n_checks": n_checks,
            "n_consistent": n_consistent,
            "consistency_rate": n_consistent / n_checks if n_checks else None,
            "consistent": n_consistent == n_checks,
            "claim_reviewer_used": False,
        }
        write_immutable_json(self.paths["recompute"], self.recompute)
        self.phase = "complete"
        self._checkpoint()

    def _summary(self) -> dict:
        wall, cpu = self._elapsed()
        confirmed = sum(bool(row.get("audit_confirmed")) for row in self.audit_records)
        status_counts = {}
        for row in self.proposals:
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1
        trajectory = [{
            "proposal_id": row["proposal_id"], "generation": row["generation"], "slot": row["slot"],
            "parent_id": row["parent_id"], "parent_formula": row["parent_formula"],
            "formula": row.get("formula"), "status": row["status"],
            "search_fitness": row.get("search_fitness"), "direction": row.get("direction"),
            "schema_valid": row.get("schema_valid"),
            "search_valid": row.get("search", {}).get("search_valid"),
            "coverage": row.get("search", {}).get("coverage"),
            "signed_mean_ic": row.get("search", {}).get("signed_mean_ic"),
            "icir": row.get("search", {}).get("icir"),
        } for row in self.proposals]
        return {
            "schema": "AUTO-RESEARCH-LIVE-QD-LOOP-RESULT-1",
            "created_at": utc_now(),
            "test_only": self.spec.test_mode,
            "run_id": self.spec.run_id,
            "task": self.spec.task,
            "block_id": self.spec.block_id,
            "arm": self.spec.arm,
            "execution_order": self.spec.execution_order,
            "status": self.status,
            "coverage_stop": self.coverage_stop,
            "proposal_slots": self.spec.proposal_slots,
            "attempted_slots": len(self.proposals),
            "not_run_slots": self.spec.proposal_slots - len(self.proposals),
            "proposal_status_counts": status_counts,
            "archive_cells": len(self.archive),
            "trusted_archive": self.spec.arm == EVIDENCE,
            "nominated": len(self.nominations),
            "audit_confirmed_unique_discoveries": confirmed,
            "audit_confirmed_per_slot": confirmed / self.spec.proposal_slots,
            "audit_confirmed_unique_per_24_slots": confirmed * 24 / self.spec.proposal_slots,
            "trajectory": trajectory,
            "audit": self.audit_records,
            "independent_recompute": self.recompute,
            "cost": {
                "wall_seconds": wall,
                "cpu_seconds": cpu,
                "llm": self.llm_totals,
                "evaluator_calls": self.evaluator_calls,
            },
            "random_fallback_used": False,
            "audit_feedback_applied": False,
            "historical_claims_read": bool(self.bundle.manifest.get("historical_claims_read", False)),
            "kg_read": bool(self.bundle.manifest.get("kg_read", False)),
            "real_data_read": bool(self.bundle.manifest.get("real_data_read", False)),
            "score_window_read": bool(self.bundle.manifest.get("score_window_read", False)),
            "oos_window_read": bool(self.bundle.manifest.get("oos_window_read", False)),
            "embargo_read": bool(self.bundle.manifest.get("embargo_read", False)),
            "independent_oos": False,
            "primary_analysis_performed": False,
        }

    def _finish(self) -> dict:
        if self.paths["completion"].is_file():
            return read_json(self.paths["completion"])
        summary = self._summary()
        summary["artifact_hashes_before_completion"] = file_hashes(self.arm_dir)
        write_immutable_json(self.paths["completion"], summary)
        return summary

    def _finish_search(self) -> dict:
        if self.paths["search_completion"].is_file():
            return read_json(self.paths["search_completion"])
        summary = self._summary()
        summary["status"] = "NOMINATIONS_FROZEN" if self.status == "RUNNING" else self.status
        summary["search_stage_complete"] = self.status == "RUNNING"
        summary["audit_deferred_to_independent_process"] = True
        summary["artifact_hashes_before_search_completion"] = file_hashes(self.arm_dir)
        write_immutable_json(self.paths["search_completion"], summary)
        return summary

    def run(self, stop_before_audit: bool = False, audit_only: bool = False) -> dict:
        if stop_before_audit and audit_only:
            raise ValueError("stop_before_audit and audit_only are mutually exclusive")
        if self.paths["completion"].is_file():
            return read_json(self.paths["completion"])
        if self.status == "COVERAGE_STOP" or self.coverage_stop is not None:
            from agents.live_manifest import ResumeError
            raise ResumeError("coverage stop latched; no further model or evaluation calls are allowed")
        if audit_only and self.phase not in {"audit", "recompute", "complete"}:
            raise RuntimeError("audit-only recovery requires frozen nominations and cannot run search")
        if not audit_only:
            if self.phase == "initialize":
                self._initialize()
            while self.phase == "search":
                if not self.archive:
                    self.status = "NO_TRUSTED_PARENT" if self.spec.arm == EVIDENCE else "NO_RAW_PARENT"
                    self.phase = "stopped"
                    self._checkpoint()
                    break
                self._one_slot()
            if self.phase == "nominations":
                self._freeze_nominations()
            if stop_before_audit and self.phase == "audit":
                return self._finish_search()
        if self.phase == "audit":
            self._audit_all()
        if self.phase == "recompute":
            self._recompute_all()
        if self.phase == "complete" and self.status == "RUNNING":
            self.status = "COMPLETED"
        return self._finish()


def run_live_loop(spec: LiveLoopSpec, bundle: SyntheticBundle, llm, arm_dir: Path,
                  resume: bool = False, on_checkpoint: Callable[[dict], None] | None = None,
                  stop_before_audit: bool = False, audit_only: bool = False) -> dict:
    return _LiveLoop(spec, bundle, llm, arm_dir, resume=resume, on_checkpoint=on_checkpoint).run(
        stop_before_audit=stop_before_audit, audit_only=audit_only)


def default_test_script() -> list[dict]:
    return [
        {"expression": "rank(f00)", "rationale": "Cross-sectional ranking preserves the planted mechanism.",
         "expected_ic": 0.02, "falsification": "The signed audit IC is below 0.02."},
        {"expression": "rank_minus(f00,f01)", "rationale": "A related planted-signal spread preserves mechanism.",
         "expected_ic": 0.02, "falsification": "The signed audit IC is below 0.02."},
        {"expression": "zscore(f00)", "rationale": "Standardization preserves the same cross-sectional signal.",
         "expected_ic": 0.02, "falsification": "The signed audit IC is below 0.02."},
        {"expression": "product(f00,f02)", "rationale": "Interaction with a related signal tests mechanism robustness.",
         "expected_ic": 0.02, "falsification": "The signed audit IC is below 0.02."},
    ]


def make_test_client_factory(script: Sequence[dict] | None = None):
    scripted = copy.deepcopy(list(script or default_test_script()))
    clients = []

    def factory() -> LiveLLMClient:
        index = {"value": 0}

        def transport(url: str, payload: dict, headers: dict, timeout: float) -> dict:
            item = scripted[min(index["value"], len(scripted) - 1)]
            call = index["value"]
            index["value"] += 1
            content = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)
            prompt_tokens = max(1, len(json.dumps(payload["messages"])) // 4)
            completion_tokens = max(1, len(content) // 4)
            return {
                "id": f"TEST-mock-{call:03d}", "model": payload["model"],
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                          "total_tokens": prompt_tokens + completion_tokens},
            }

        client = LiveLLMClient(model="TEST-mock-v1", transport=transport, cache_enabled=False,
                               retry_backoff=0.0)
        clients.append(client)
        return client

    factory.clients = clients
    return factory


def make_live_client_factory(model: dict):
    def factory() -> LiveLLMClient:
        return LiveLLMClient(model=model["name"], base_url=model.get("base_url", "http://127.0.0.1:1234/v1"),
                             supports_explicit_seed=bool(model.get("supports_explicit_seed")),
                             seed=model.get("seed"), reasoning_effort=model.get("reasoning_effort"),
                             cache_enabled=False)
    return factory


def _recovery_command(arm_dir: Path) -> str:
    return f"python {Path(__file__).resolve()} --resume-arm {arm_dir.resolve()}"


def run_matched_smoke(output_root: Path, run_id: str, tasks: Sequence[str] = ("planted", "weak"),
                      n_blocks: int = 1, generations: int = 2, slots_per_generation: int = 2,
                      client_factory: Callable | None = None, model: dict | None = None,
                      run_seed: int = 7717, agent_seed: int = 8819, search_seed: int = 81001,
                      audit_seed_count: int = 2, n_audit_folds: int = 2, n_symbols: int = 40,
                      n_weeks: int = 32, n_features: int = 8, nomination_cap: int = 4) -> dict:
    if not tasks or n_blocks < 1 or audit_seed_count < 1:
        raise ValueError("tasks, blocks and audit seeds must be non-empty")
    model = dict(model or mock_model_config())
    client_factory = client_factory or make_test_client_factory()
    started_wall, started_cpu = time.perf_counter(), time.process_time()
    designs = []
    for task_index, task in enumerate(tasks):
        for block_index in range(n_blocks):
            block_id = f"block_{block_index:03d}"
            seed = search_seed + task_index * 1000 + block_index
            audit_seeds = tuple(seed + 10000 * (offset + 1) for offset in range(audit_seed_count))
            common = dict(run_id=run_id, task=task, block_id=block_id, agent_seed=agent_seed,
                          search_seed=seed, audit_seeds=audit_seeds, generations=generations,
                          slots_per_generation=slots_per_generation, n_symbols=n_symbols, n_weeks=n_weeks,
                          n_features=n_features, n_audit_folds=n_audit_folds,
                          nomination_cap=nomination_cap, model=model, test_mode=True)
            bundle = make_synthetic_bundle(**LiveLoopSpec(arm=EVIDENCE, **common).synthetic_config())
            order = list(ARMS)
            random.Random(f"{run_seed}:{task}:{block_index}").shuffle(order)
            specs = {arm: LiveLoopSpec(arm=arm, execution_order=order.index(arm), **common) for arm in ARMS}
            designs.append({"task": task, "block_id": block_id, "bundle": bundle,
                            "specs": specs, "order": order})
    run_dir = create_run_directory(Path(output_root), run_id, test_mode=True)
    run_data = {f"{design['task']}/{design['block_id']}": design["bundle"].manifest for design in designs}
    run_protocol = {
        "protocol_id": protocol_identity(),
        "design": "tasks x matched blocks x two live arms",
        "tasks": list(tasks), "n_blocks": n_blocks,
        "generations": generations, "slots_per_generation": slots_per_generation,
        "proposal_slots_per_loop": generations * slots_per_generation,
        "run_seed": run_seed, "agent_seed": agent_seed,
    }
    assignments = {f"{design['task']}/{design['block_id']}": {
        "execution_order": design["order"],
        "assignment": {str(index): arm for index, arm in enumerate(design["order"])},
        "matched_agent_seed": agent_seed,
    } for design in designs}
    initialize_manifest(
        run_dir / "manifest.json", "run", frozen_hashes(run_protocol, model, prompt_contract(), run_data),
        run_id=run_id, test_only=True, arm_assignment=assignments,
        streams={f"{design['task']}/{design['block_id']}/{arm}":
                 f"blocks/{design['task']}/{design['block_id']}/arms/{arm}/"
                 for design in designs for arm in ARMS},
        recovery="Each arm manifest contains its immutable --resume-arm command.",
        random_fallback=False, primary_analysis_performed=False,
    )
    summaries = []
    clients = []
    run_status = "COMPLETED"
    for design in designs:
        block_dir = run_dir / "blocks" / design["task"] / design["block_id"]
        first_spec = design["specs"][EVIDENCE]
        initialize_manifest(
            block_dir / "manifest.json", "block",
            frozen_hashes(first_spec.matched_protocol(), model, prompt_contract(), design["bundle"].manifest),
            run_id=run_id, task=design["task"], block_id=design["block_id"],
            arm_assignment=assignments[f"{design['task']}/{design['block_id']}"],
            streams={arm: f"arms/{arm}/" for arm in ARMS},
            recovery_commands={arm: _recovery_command(block_dir / "arms" / arm) for arm in ARMS},
        )
        for arm in design["order"]:
            client = client_factory()
            if any(client is previous for previous in clients):
                raise ValueError("each arm requires an independent LLM client; cross-arm cache/state is forbidden")
            clients.append(client)
            summary = run_live_loop(design["specs"][arm], design["bundle"], client,
                                    block_dir / "arms" / arm)
            summaries.append(summary)
            if summary["status"] == "COVERAGE_STOP":
                run_status = "COVERAGE_STOP"
                break
        if run_status == "COVERAGE_STOP":
            break
    result = {
        "schema": "AUTO-RESEARCH-LIVE-QD-TEST-RESULT-1",
        "created_at": utc_now(),
        "test_only": True,
        "run_id": run_id,
        "status": run_status,
        "loops": summaries,
        "n_loops_completed": len(summaries),
        "n_loops_planned": len(designs) * 2,
        "wall_seconds": time.perf_counter() - started_wall,
        "cpu_seconds": time.process_time() - started_cpu,
        "primary_analysis_performed": False,
        "effect_estimation_from_smoke_forbidden": True,
        "artifact_hashes_before_run_completion": file_hashes(run_dir),
    }
    write_immutable_json(run_dir / "completion.json", result)
    return result


def resume_live_loop(arm_dir: Path, llm=None, bundle: SyntheticBundle | None = None,
                     on_checkpoint: Callable[[dict], None] | None = None,
                     audit_only: bool = False, stop_before_audit: bool = False) -> dict:
    arm_dir = Path(arm_dir)
    manifest = read_json(arm_dir / "manifest.json")
    spec = LiveLoopSpec.from_dict(manifest["loop_spec"])
    bundle = bundle or bundle_from_spec(spec)
    if llm is None:
        if spec.model.get("provider") == "injected-mock":
            llm = make_test_client_factory()()
        else:
            llm = make_live_client_factory(spec.model)()
    return run_live_loop(spec, bundle, llm, arm_dir, resume=True, on_checkpoint=on_checkpoint,
                         audit_only=audit_only, stop_before_audit=stop_before_audit)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--test-mode", action="store_true", help="synthetic panels plus injected mock; never calls a model server")
    mode.add_argument("--live-model", help="local LM Studio model for a synthetic TEST smoke; loopback only")
    parser.add_argument("--run-spec", type=Path)
    parser.add_argument("--resume-arm", type=Path)
    parser.add_argument("--stop-before-audit", action="store_true")
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--tasks", default="planted,weak")
    parser.add_argument("--blocks", type=int, default=1)
    parser.add_argument("--generations", type=int, default=2)
    parser.add_argument("--slots", type=int, default=2)
    parser.add_argument("--symbols", type=int, default=40)
    parser.add_argument("--weeks", type=int, default=32)
    parser.add_argument("--audit-seeds", type=int, default=2)
    parser.add_argument("--audit-folds", type=int, default=2)
    args = parser.parse_args()
    if args.run_spec:
        if args.resume_arm or args.audit_only or args.test_mode or args.live_model:
            parser.error("--run-spec is a standalone search-stage command")
        spec = LiveLoopSpec.from_dict(read_json(args.run_spec))
        factory = (make_test_client_factory() if spec.model.get("provider") == "injected-mock"
                   else make_live_client_factory(spec.model))
        summary = run_live_loop(spec, bundle_from_spec(spec), factory(), args.run_spec.parent,
                                stop_before_audit=args.stop_before_audit)
        print(json.dumps(summary, ensure_ascii=False, indent=1, allow_nan=False))
        return
    if args.resume_arm:
        if args.stop_before_audit and args.audit_only:
            parser.error("--stop-before-audit and --audit-only are mutually exclusive")
        summary = resume_live_loop(args.resume_arm, audit_only=args.audit_only,
                                   stop_before_audit=args.stop_before_audit)
        print(json.dumps(summary, ensure_ascii=False, indent=1, allow_nan=False))
        return
    if args.audit_only or args.stop_before_audit:
        parser.error("two-stage execution requires --run-spec or --resume-arm")
    if not args.test_mode and not args.live_model:
        parser.error("explicit --test-mode or --live-model is required")
    if not args.output_root or not args.run_id:
        parser.error("new smoke runs require --output-root and --run-id")
    if args.reasoning_effort and not args.live_model:
        parser.error("--reasoning-effort requires --live-model")
    if args.live_model:
        model = live_model_config(args.live_model, reasoning_effort=args.reasoning_effort)
        factory = make_live_client_factory(model)
    else:
        model = mock_model_config()
        factory = make_test_client_factory()
    result = run_matched_smoke(
        args.output_root, args.run_id, tasks=tuple(value.strip() for value in args.tasks.split(",") if value.strip()),
        n_blocks=args.blocks, generations=args.generations, slots_per_generation=args.slots,
        client_factory=factory, model=model, n_symbols=args.symbols, n_weeks=args.weeks,
        audit_seed_count=args.audit_seeds, n_audit_folds=args.audit_folds,
    )
    print(json.dumps({
        "run_id": result["run_id"], "status": result["status"],
        "n_loops_completed": result["n_loops_completed"],
        "loops": [{"task": row["task"], "arm": row["arm"], "status": row["status"],
                   "nominated": row["nominated"],
                   "audit_confirmed_unique_discoveries": row["audit_confirmed_unique_discoveries"],
                   "attempted_slots": row["attempted_slots"],
                   "wall_seconds": row["cost"]["wall_seconds"]} for row in result["loops"]],
    }, ensure_ascii=False, indent=1, allow_nan=False))


if __name__ == "__main__":
    main()
