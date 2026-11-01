"""Evidence-governance contracts for factor research.

This module governs *claims and information access*.  It does not calculate
market factors, labels, portfolios or trading performance.  Its job is to make
the v2.1 boundaries executable:

* deterministic research-input manifests;
* frozen validation submissions;
* explicit final-holdout eligibility and bounded feedback;
* predeclared multiple-testing calibration;
* immutable decisions, scoped releases and multi-parent lineage.

The ledger improves enforcement and auditability.  It does not by itself make
adaptive inference valid or turn an exposed interval into a fresh holdout.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Mapping, Sequence

import numpy as np


_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")
_FINAL_REGION_STATUS = "fresh_holdout"
_EXPOSED_REGION_STATUSES = frozenset({
    "already_used_for_selection", "already_viewed", "authorized_for_research",
})
_ALLOWED_USES = frozenset({"incremental", "substitute", "state", "diagnostic"})
_CALIBRATION_METHODS = frozenset({"none", "bh", "by"})


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    result = json.loads(_canonical_bytes(value))
    return result


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _safe_id(value: Any, name: str) -> str:
    value = _text(value, name)
    if _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"{name} must be a safe identifier")
    return value


def _sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _iso_date(value: Any, name: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{name} must be an ISO date")
    return parsed


def _timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed


def validate_input_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one deterministic research-input identity manifest."""
    value = _object(manifest, "input manifest")
    if value.get("schema_version") != "RESEARCH-INPUT-MANIFEST-1":
        raise ValueError("unsupported input manifest schema")
    allowed = {
        "schema_version", "dataset_version", "universe_version", "factor_code_hash",
        "preprocessing_hash", "label_definition", "execution_lag", "calendar_version",
        "environment_version", "availability_policy", "random_seed",
        "dependency_versions", "source_files_sha256", "manifest_hash",
    }
    if set(value) - allowed:
        raise ValueError("unknown input manifest fields: " + ",".join(sorted(set(value)-allowed)))
    required_text = (
        "dataset_version", "universe_version", "label_definition",
        "execution_lag", "calendar_version", "environment_version",
        "availability_policy",
    )
    for name in required_text:
        _text(value.get(name), name)
    for name in ("factor_code_hash", "preprocessing_hash"):
        _sha(value.get(name), name)
    seed = value.get("random_seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("random_seed must be an integer")
    dependencies = value.get("dependency_versions")
    if not isinstance(dependencies, Mapping) or not dependencies:
        raise ValueError("dependency_versions must be a nonempty object")
    for name, version in dependencies.items():
        _text(name, "dependency name")
        _text(version, f"dependency version for {name}")
    sources = value.get("source_files_sha256")
    if not isinstance(sources, Mapping) or not sources:
        raise ValueError("source_files_sha256 must be a nonempty object")
    for path, digest in sources.items():
        _text(path, "source path")
        _sha(digest, f"source hash for {path}")
    value["manifest_hash"] = canonical_hash({k: v for k, v in value.items()
                                               if k != "manifest_hash"})
    return value


def audit_information_times(record: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed on one confirmatory signal-time information record.

    ``event_time`` is provenance only.  Publication, vendor arrival and the
    research availability decision must all be known and no later than the
    signal time.  Execution must not precede the signal.
    """
    value = _object(record, "information record")
    required = (
        "event_time", "publication_time", "vendor_arrival_time",
        "research_available_time", "signal_time", "execution_time",
    )
    parsed = {name: _timestamp(value.get(name), name) for name in required}
    available_floor = max(parsed["publication_time"], parsed["vendor_arrival_time"])
    if parsed["research_available_time"] < available_floor:
        raise ValueError("research_available_time predates publication or vendor arrival")
    if parsed["research_available_time"] > parsed["signal_time"]:
        raise ValueError("information was unavailable at signal time")
    if parsed["execution_time"] < parsed["signal_time"]:
        raise ValueError("execution_time precedes signal_time")
    return {
        "status": "eligible_at_signal_time",
        "available_floor": available_floor.isoformat(),
        "signal_time": parsed["signal_time"].isoformat(),
        "execution_time": parsed["execution_time"].isoformat(),
    }


def validate_submission(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a frozen claim contract without reading outcomes."""
    value = _object(contract, "submission")
    if value.get("schema_version") != "RESEARCH-SUBMISSION-1":
        raise ValueError("unsupported submission schema")
    allowed = {
        "schema_version", "submission_id", "claim_family_id", "claim",
        "intended_use", "factor_ids", "input_manifest_hash", "definition_bundle_hash",
        "validation_region_id", "expected_sign", "primary_metric", "secondary_metrics",
        "baselines", "decision_rule", "evidence_rule", "response_package",
        "submission_hash",
    }
    if set(value) - allowed:
        raise ValueError("unknown submission fields: " + ",".join(sorted(set(value)-allowed)))
    for name in ("submission_id", "claim_family_id", "validation_region_id"):
        _safe_id(value.get(name), name)
    _text(value.get("claim"), "claim")
    intended_use = value.get("intended_use")
    if intended_use not in _ALLOWED_USES:
        raise ValueError("unrecognized intended_use")
    factor_ids = value.get("factor_ids")
    if (not isinstance(factor_ids, list) or not factor_ids
            or any(_SAFE_ID.fullmatch(x) is None for x in factor_ids if isinstance(x, str))
            or any(not isinstance(x, str) for x in factor_ids)
            or len(factor_ids) != len(set(factor_ids))):
        raise ValueError("factor_ids must be unique safe identifiers")
    for name in ("input_manifest_hash", "definition_bundle_hash"):
        _sha(value.get(name), name)
    expected_sign = value.get("expected_sign")
    if expected_sign not in (-1, 1):
        raise ValueError("expected_sign must be +1 or -1")
    primary = _safe_id(value.get("primary_metric"), "primary_metric")
    secondary = value.get("secondary_metrics", [])
    if (not isinstance(secondary, list)
            or any(not isinstance(x, str) or _SAFE_ID.fullmatch(x) is None for x in secondary)
            or len(secondary) != len(set(secondary)) or primary in secondary):
        raise ValueError("secondary_metrics must be unique and exclude the primary metric")
    baselines = value.get("baselines")
    if (not isinstance(baselines, list) or not baselines
            or any(not isinstance(x, str) or not x.strip() for x in baselines)):
        raise ValueError("at least one baseline is required")
    evidence = value.get("evidence_rule")
    if not isinstance(evidence, Mapping):
        raise ValueError("evidence_rule is required")
    evidence_allowed = {
        "method", "alpha", "test_family_id", "family_members", "null_construction",
        "resampling_scheme", "pvalue_source", "block_length", "repetitions", "seed",
    }
    if set(evidence) - evidence_allowed:
        raise ValueError("unknown evidence_rule fields")
    method = evidence.get("method")
    if method not in _CALIBRATION_METHODS:
        raise ValueError("unsupported evidence calibration method")
    alpha = evidence.get("alpha")
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)) or not 0 < alpha < 1:
        raise ValueError("evidence_rule.alpha must be between zero and one")
    _safe_id(evidence.get("test_family_id"), "test_family_id")
    family_members = evidence.get("family_members")
    if (not isinstance(family_members, list) or not family_members
            or any(not isinstance(x, str) or _SAFE_ID.fullmatch(x) is None for x in family_members)
            or len(family_members) != len(set(family_members))
            or any(x not in family_members for x in factor_ids)):
        raise ValueError("evidence family_members must be unique and contain all frozen claims")
    _text(evidence.get("null_construction"), "null_construction")
    _text(evidence.get("resampling_scheme"), "resampling_scheme")
    decision = value.get("decision_rule")
    if not isinstance(decision, Mapping):
        raise ValueError("decision_rule is required")
    if set(decision) != {"adjusted_p_max", "effect_direction", "all_frozen_claims_must_pass"}:
        raise ValueError("decision_rule fields must be exact")
    if decision.get("adjusted_p_max") != alpha:
        raise ValueError("decision_rule.adjusted_p_max must equal frozen evidence alpha")
    if decision.get("effect_direction") != expected_sign:
        raise ValueError("decision_rule.effect_direction must equal expected_sign")
    if decision.get("all_frozen_claims_must_pass") is not True:
        raise ValueError("all_frozen_claims_must_pass must be true")
    pvalue_source = evidence.get("pvalue_source")
    if pvalue_source not in {"precomputed", "block_bootstrap_mean"}:
        raise ValueError("unsupported evidence pvalue_source")
    if pvalue_source == "block_bootstrap_mean":
        block_length = evidence.get("block_length")
        repetitions = evidence.get("repetitions")
        seed = evidence.get("seed")
        if (isinstance(block_length, bool) or not isinstance(block_length, int)
                or block_length < 1):
            raise ValueError("block_length must be a positive integer")
        if (isinstance(repetitions, bool) or not isinstance(repetitions, int)
                or repetitions < 200):
            raise ValueError("repetitions must be an integer >= 200")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("evidence seed must be an integer")
    response = value.get("response_package")
    allowed_metrics = [primary, *secondary]
    if not isinstance(response, Mapping):
        raise ValueError("response_package is required")
    if set(response) != {"metrics", "granularities", "max_feedback_rounds",
                         "replacement_policy", "evidence_fields"}:
        raise ValueError("response_package fields must be exact")
    returned_metrics = response.get("metrics")
    if (not isinstance(returned_metrics, list) or primary not in returned_metrics
            or any(x not in allowed_metrics for x in returned_metrics)
            or len(returned_metrics) != len(set(returned_metrics))):
        raise ValueError("response metrics must be a frozen subset containing primary_metric")
    granularities = response.get("granularities")
    if (not isinstance(granularities, list) or not granularities
            or any(not isinstance(x, str) or not x.strip() for x in granularities)
            or len(granularities) != len(set(granularities))):
        raise ValueError("response granularities must be unique nonempty strings")
    max_rounds = response.get("max_feedback_rounds")
    if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
        raise ValueError("max_feedback_rounds must be a positive integer")
    if response.get("replacement_policy") != "none":
        raise ValueError("replacement_policy must be none for a frozen submission")
    evidence_fields = response.get("evidence_fields")
    allowed_evidence_fields = {"adjusted_pvalues", "decisions"}
    if (not isinstance(evidence_fields, list) or "decisions" not in evidence_fields
            or len(evidence_fields) != len(set(evidence_fields))
            or any(x not in allowed_evidence_fields for x in evidence_fields)):
        raise ValueError("evidence_fields must be a frozen subset containing decisions")
    value["submission_hash"] = canonical_hash({k: v for k, v in value.items()
                                                if k != "submission_hash"})
    return value


def final_holdout_region(exposure_state: Mapping[str, Any], region_id: str) -> dict[str, Any]:
    """Return a fresh region or reject exposed/sealed/unknown intervals."""
    state = _object(exposure_state, "exposure state")
    intervals = state.get("intervals")
    if not isinstance(intervals, list):
        raise ValueError("exposure intervals required")
    matches = [x for x in intervals if isinstance(x, Mapping)
               and (x.get("region_id") == region_id or x.get("name") == region_id)]
    if len(matches) != 1:
        raise ValueError("validation region must resolve exactly once")
    region = dict(matches[0])
    start = _iso_date(region.get("start"), "region.start")
    end = _iso_date(region.get("end"), "region.end")
    if start > end:
        raise ValueError("validation region has invalid dates")
    status = region.get("status")
    if status in _EXPOSED_REGION_STATUSES:
        raise ValueError("validation region has already been exposed")
    if status == "sealed":
        raise ValueError("validation region is sealed and not released")
    if status != _FINAL_REGION_STATUS:
        raise ValueError("validation region is not an eligible fresh holdout")
    if region.get("selection_access") is not False or region.get("outcome_access") is not False:
        raise ValueError("fresh holdout must explicitly deny prior selection and outcome access")
    return region


def adjust_pvalues(pvalues: Mapping[str, float], method: str) -> dict[str, float]:
    """Apply a frozen none/BH/BY rule to one explicit test family."""
    if method not in _CALIBRATION_METHODS:
        raise ValueError("unsupported evidence calibration method")
    if not isinstance(pvalues, Mapping) or not pvalues:
        raise ValueError("pvalues must be a nonempty mapping")
    pairs = []
    for claim, pvalue in pvalues.items():
        _safe_id(claim, "claim id")
        if (isinstance(pvalue, bool) or not isinstance(pvalue, (int, float))
                or not math.isfinite(pvalue) or not 0 <= pvalue <= 1):
            raise ValueError("pvalues must be finite numbers in [0,1]")
        pairs.append((claim, float(pvalue)))
    if method == "none":
        return dict(pairs)
    ordered = sorted(pairs, key=lambda item: (item[1], item[0]))
    m = len(ordered)
    dependency = sum(1.0 / i for i in range(1, m + 1)) if method == "by" else 1.0
    adjusted = [0.0] * m
    running = 1.0
    for index in range(m - 1, -1, -1):
        rank = index + 1
        candidate = ordered[index][1] * m * dependency / rank
        running = min(running, candidate)
        adjusted[index] = min(1.0, running)
    return {ordered[i][0]: adjusted[i] for i in range(m)}


def block_bootstrap_mean_pvalue(values: Sequence[float], *, block_length: int,
                                repetitions: int, seed: int,
                                alternative: str = "greater") -> dict[str, Any]:
    """Deterministic moving-block null test for a time-series mean.

    The observed series is centered to construct the zero-mean null.  Circular
    contiguous blocks preserve local ordering.  This is an executable method,
    not a universal validity claim: the frozen submission must justify the
    block length, dependence assumptions and one/two-sided alternative.
    """
    if (isinstance(block_length, bool) or not isinstance(block_length, int)
            or block_length < 1):
        raise ValueError("block_length must be a positive integer")
    if (isinstance(repetitions, bool) or not isinstance(repetitions, int)
            or repetitions < 200):
        raise ValueError("repetitions must be an integer >= 200")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    if alternative not in {"greater", "less", "two-sided"}:
        raise ValueError("unsupported alternative")
    array = np.asarray(values, dtype=float)
    array = array[np.isfinite(array)]
    if len(array) < max(3, block_length):
        raise ValueError("insufficient finite observations for block bootstrap")
    observed = float(array.mean())
    centered = array - observed
    rng = np.random.default_rng(seed)
    n = len(array)
    block_count = math.ceil(n / block_length)
    means = np.empty(repetitions, dtype=float)
    offsets = np.arange(block_length)
    for repetition in range(repetitions):
        starts = rng.integers(0, n, size=block_count)
        sample_index = ((starts[:, None] + offsets) % n).reshape(-1)[:n]
        means[repetition] = centered[sample_index].mean()
    if alternative == "greater":
        exceed = int(np.count_nonzero(means >= observed))
    elif alternative == "less":
        exceed = int(np.count_nonzero(means <= observed))
    else:
        exceed = int(np.count_nonzero(np.abs(means) >= abs(observed)))
    return {
        "observed_mean": observed,
        "pvalue": (exceed + 1.0) / (repetitions + 1.0),
        "n_obs": int(n), "block_length": block_length,
        "repetitions": repetitions, "seed": seed,
        "alternative": alternative,
        "null_construction": "observed series centered to zero mean; circular moving blocks",
    }


class GovernanceLedger:
    """SQLite enforcement ledger for submissions, feedback, evidence and release."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS submissions (
              submission_hash TEXT PRIMARY KEY,
              submission_id TEXT NOT NULL UNIQUE,
              contract_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('frozen','validated','released')),
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback (
              feedback_id INTEGER PRIMARY KEY AUTOINCREMENT,
              submission_hash TEXT NOT NULL,
              region_id TEXT NOT NULL,
              requested_metrics_json TEXT NOT NULL,
              granularity TEXT NOT NULL,
              feedback_round INTEGER NOT NULL,
              decision TEXT NOT NULL CHECK(decision IN ('allowed','denied','returned')),
              response_hash TEXT,
              reason TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(submission_hash) REFERENCES submissions(submission_hash)
            );
            CREATE TABLE IF NOT EXISTS evidence (
              submission_hash TEXT PRIMARY KEY,
              evidence_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(submission_hash) REFERENCES submissions(submission_hash)
            );
            CREATE TABLE IF NOT EXISTS releases (
              submission_hash TEXT PRIMARY KEY,
              release_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(submission_hash) REFERENCES submissions(submission_hash)
            );
            CREATE TABLE IF NOT EXISTS lineage (
              candidate_id TEXT PRIMARY KEY,
              definition_hash TEXT NOT NULL,
              hypothesis_family_id TEXT NOT NULL,
              formula_family_id TEXT NOT NULL,
              parents_json TEXT NOT NULL,
              transformation TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "GovernanceLedger":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def freeze(self, contract: Mapping[str, Any]) -> dict[str, Any]:
        value = validate_submission(contract)
        raw = _canonical_bytes(value)
        now = _now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = self.connection.execute(
                "SELECT contract_json,status FROM submissions WHERE submission_hash=?",
                (value["submission_hash"],),
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO submissions VALUES(?,?,?,?,?,?)",
                    (value["submission_hash"], value["submission_id"], raw.decode(),
                     "frozen", now, now),
                )
                created = True
                status = "frozen"
            else:
                if existing["contract_json"] != raw.decode():
                    raise ValueError("submission hash collision")
                created = False
                status = existing["status"]
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return {"submission_hash": value["submission_hash"], "created": created,
                "status": status}

    def get_submission(self, submission_hash: str) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM submissions WHERE submission_hash=?", (submission_hash,)
        ).fetchone()
        if row is None:
            raise KeyError(submission_hash)
        return {"submission_hash": row["submission_hash"], "status": row["status"],
                "contract": json.loads(row["contract_json"]),
                "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def authorize_feedback(self, submission_hash: str, exposure_state: Mapping[str, Any],
                           metrics: Sequence[str], granularity: str) -> dict[str, Any]:
        submission = self.get_submission(submission_hash)
        contract = submission["contract"]
        requested = list(metrics)
        package = contract["response_package"]
        reason = None
        try:
            final_holdout_region(exposure_state, contract["validation_region_id"])
            if (not requested or len(requested) != len(set(requested))
                    or any(x not in package["metrics"] for x in requested)):
                raise ValueError("requested metrics exceed the frozen response package")
            if granularity not in package["granularities"]:
                raise ValueError("requested granularity exceeds the frozen response package")
            returned_rounds = self.connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE submission_hash=? AND decision='returned'",
                (submission_hash,),
            ).fetchone()[0]
            if returned_rounds >= package["max_feedback_rounds"]:
                raise ValueError("feedback budget exhausted")
            decision = "allowed"
            feedback_round = returned_rounds + 1
        except ValueError as exc:
            decision = "denied"
            reason = str(exc)
            feedback_round = self.connection.execute(
                "SELECT COUNT(*) FROM feedback WHERE submission_hash=?",
                (submission_hash,),
            ).fetchone()[0] + 1
        now = _now()
        cursor = self.connection.execute(
            "INSERT INTO feedback(submission_hash,region_id,requested_metrics_json,"
            "granularity,feedback_round,decision,response_hash,reason,created_at) "
            "VALUES(?,?,?,?,?,?,NULL,?,?)",
            (submission_hash, contract["validation_region_id"],
             _canonical_bytes(requested).decode(), granularity, feedback_round,
             decision, reason, now),
        )
        result = {"feedback_id": cursor.lastrowid, "decision": decision,
                  "feedback_round": feedback_round, "reason": reason}
        if decision == "denied":
            raise PermissionError(json.dumps(result, sort_keys=True))
        return result

    def record_feedback(self, feedback_id: int, response: Mapping[str, Any]) -> dict[str, Any]:
        row = self.connection.execute(
            "SELECT * FROM feedback WHERE feedback_id=?", (feedback_id,)
        ).fetchone()
        if row is None:
            raise KeyError(feedback_id)
        if row["decision"] != "allowed":
            raise ValueError("feedback request is not open")
        value = _object(response, "feedback response")
        requested = json.loads(row["requested_metrics_json"])
        if set(value) != set(requested):
            raise ValueError("response must contain exactly the authorized metrics")
        digest = canonical_hash(value)
        self.connection.execute(
            "UPDATE feedback SET decision='returned',response_hash=? WHERE feedback_id=? "
            "AND decision='allowed'", (digest, feedback_id),
        )
        return {"feedback_id": feedback_id, "response_hash": digest,
                "decision": "returned"}

    def calibrate(self, submission_hash: str, pvalues: Mapping[str, float]) -> dict[str, Any]:
        submission = self.get_submission(submission_hash)
        if submission["status"] != "frozen":
            raise ValueError("submission has already been finalized")
        rule = submission["contract"]["evidence_rule"]
        if set(pvalues) != set(rule["family_members"]):
            raise ValueError("p-values must cover exactly the frozen test family")
        returned = self.connection.execute(
            "SELECT COUNT(*) FROM feedback WHERE submission_hash=? AND decision='returned'",
            (submission_hash,),
        ).fetchone()[0]
        if returned < 1:
            raise ValueError("calibration requires an authorized returned feedback package")
        adjusted = adjust_pvalues(pvalues, rule["method"])
        alpha = float(rule["alpha"])
        evidence = {
            "schema_version": "CALIBRATED-EVIDENCE-1",
            "submission_hash": submission_hash,
            "test_family_id": rule["test_family_id"],
            "method": rule["method"], "alpha": alpha,
            "raw_pvalues": dict(pvalues), "adjusted_pvalues": adjusted,
            "decisions": {name: value <= alpha for name, value in adjusted.items()},
            "claim_limit": "calibration follows the frozen rule; validity still depends on its assumptions and the selection/data boundary",
        }
        now = _now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "INSERT INTO evidence VALUES(?,?,?)",
                (submission_hash, _canonical_bytes(evidence).decode(), now),
            )
            self.connection.execute(
                "UPDATE submissions SET status='validated',updated_at=? WHERE submission_hash=? AND status='frozen'",
                (now, submission_hash),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return evidence

    def release(self, submission_hash: str, allowed_uses: Sequence[str]) -> dict[str, Any]:
        submission = self.get_submission(submission_hash)
        if submission["status"] != "validated":
            raise ValueError("only validated submissions can be considered for release")
        uses = list(allowed_uses)
        contract = submission["contract"]
        if (not uses or len(uses) != len(set(uses))
                or any(x not in _ALLOWED_USES for x in uses)
                or contract["intended_use"] not in uses):
            raise ValueError("release uses must include the frozen intended use")
        evidence_row = self.connection.execute(
            "SELECT evidence_json FROM evidence WHERE submission_hash=?", (submission_hash,)
        ).fetchone()
        evidence = json.loads(evidence_row[0])
        primary_claims = contract["factor_ids"]
        if any(evidence["decisions"].get(name) is not True for name in primary_claims):
            raise ValueError("primary frozen claims did not pass calibrated evidence")
        release = {
            "schema_version": "RESEARCH-RELEASE-1",
            "submission_hash": submission_hash,
            "factor_ids": primary_claims,
            "allowed_uses": uses,
            "production_ready": False,
            "independent_oos": True,
        }
        now = _now()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute(
                "INSERT INTO releases VALUES(?,?,?)",
                (submission_hash, _canonical_bytes(release).decode(), now),
            )
            self.connection.execute(
                "UPDATE submissions SET status='released',updated_at=? WHERE submission_hash=? AND status='validated'",
                (now, submission_hash),
            )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        return release

    def register_lineage(self, candidate_id: str, definition_hash: str,
                         hypothesis_family_id: str, formula_family_id: str,
                         parents: Sequence[str], transformation: str,
                         metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        candidate_id = _safe_id(candidate_id, "candidate_id")
        definition_hash = _sha(definition_hash, "definition_hash")
        hypothesis_family_id = _safe_id(hypothesis_family_id, "hypothesis_family_id")
        formula_family_id = _safe_id(formula_family_id, "formula_family_id")
        parents = list(parents)
        if (len(parents) != len(set(parents)) or candidate_id in parents
                or any(not isinstance(x, str) or _SAFE_ID.fullmatch(x) is None for x in parents)):
            raise ValueError("parents must be unique safe IDs and exclude the candidate")
        for parent in parents:
            if self.connection.execute(
                "SELECT 1 FROM lineage WHERE candidate_id=?", (parent,)
            ).fetchone() is None:
                raise ValueError("all parents must be registered first")
        # Existing graph is acyclic; requiring parents to pre-exist and forbidding
        # self-parenting keeps each new insertion acyclic.
        value = {
            "candidate_id": candidate_id, "definition_hash": definition_hash,
            "hypothesis_family_id": hypothesis_family_id,
            "formula_family_id": formula_family_id, "parents": parents,
            "transformation": _text(transformation, "transformation"),
            "metadata": _object(metadata or {}, "metadata"),
        }
        self.connection.execute(
            "INSERT INTO lineage VALUES(?,?,?,?,?,?,?,?)",
            (candidate_id, definition_hash, hypothesis_family_id, formula_family_id,
             _canonical_bytes(parents).decode(), value["transformation"],
             _canonical_bytes(value["metadata"]).decode(), _now()),
        )
        return value

    def audit(self) -> dict[str, Any]:
        counts = {}
        for table in ("submissions", "feedback", "evidence", "releases", "lineage"):
            counts[table] = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        invalid_release = self.connection.execute(
            "SELECT COUNT(*) FROM releases r LEFT JOIN evidence e USING(submission_hash) "
            "WHERE e.submission_hash IS NULL"
        ).fetchone()[0]
        return {"status": "PASS" if invalid_release == 0 else "FAIL",
                "counts": counts, "release_without_evidence": invalid_release}
