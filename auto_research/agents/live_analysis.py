#!/usr/bin/env python3
"""Blinded validation and preregistered paired analysis for matched live loops."""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))

from agents.live_manifest import code_hashes, read_json, read_jsonl, sha256_file, utc_now, write_immutable_json
from agents.live_secondary import secondary_summary

EVIDENCE = "evidence-gated"
RAW = "raw-control"
DOMAINS = ("synthetic", "real_dev")
VALID_EVALUATED = {"EVALUATED"}
BLINDED_EVALUATED = {"ARCHIVED", "ARCHIVED_RAW", "ARCHIVED_EVIDENCE", "SEARCH_INVALID",
                     "SEARCH_VALID_NOT_ELITE", "EVALUATED_NOT_ELITE", "REJECTED_SCHEMA"}


def _block_key(block: dict) -> str:
    return f"{block['domain']}/{block['task']}/{block['block_id']}"


def sanitize_loop(loop: dict) -> dict:
    keys = (
        "status", "not_run_slots", "attempted_slots", "proposal_slots", "archive_cells", "nominated",
        "audit_confirmed_unique_discoveries", "audit_confirmed_per_slot", "audit_confirmed_unique_per_24_slots",
        "trajectory", "audit", "independent_recompute", "cost", "random_fallback_used",
        "audit_feedback_applied", "real_data_read", "score_window_read", "oos_window_read",
        "embargo_read", "independent_oos",
    )
    result = {key: loop[key] for key in keys if key in loop}
    status_counts = {}
    for status, count in loop["proposal_status_counts"].items():
        canonical = "EVALUATED" if status in BLINDED_EVALUATED else status
        status_counts[canonical] = status_counts.get(canonical, 0) + count
    result["proposal_status_counts"] = status_counts
    if "trajectory" in result:
        result["trajectory"] = [{**row, "status": "EVALUATED" if row["status"] in BLINDED_EVALUATED
                                  else row["status"]} for row in result["trajectory"]]
    return result


def validate_blinded(payload: dict, expected_blocks: set[str] | None = None) -> dict:
    blocks = payload.get("blocks", [])
    keys = {_block_key(block) for block in blocks}
    if len(keys) != len(blocks) or (expected_blocks is not None and keys != expected_blocks):
        raise ValueError("blinded block set is incomplete or duplicated")
    n_loops = 0
    for block in blocks:
        if block["domain"] not in DOMAINS or set(block["arms"]) != {"A", "B"}:
            raise ValueError("invalid domain or blinded arms")
        if not block.get("initial_library") or not block.get("feature_names"):
            raise ValueError("missing initial-library or feature contract")
        for blind_arm, record in block["arms"].items():
            n_loops += 1
            loop = record["result"]
            if loop["status"] != "COMPLETED" or loop["not_run_slots"] != 0:
                raise ValueError("incomplete loop cannot enter primary analysis")
            if loop["attempted_slots"] != loop["proposal_slots"]:
                raise ValueError("proposal budget is incomplete")
            if len(loop["trajectory"]) != loop["attempted_slots"]:
                raise ValueError("trajectory does not account for every proposal slot")
            if len(loop["audit"]) != loop["nominated"]:
                raise ValueError("not every frozen nomination has an audit")
            if not loop["independent_recompute"] or not loop["independent_recompute"]["consistent"]:
                raise ValueError("independent recompute is missing or inconsistent")
            if loop["random_fallback_used"] or loop["audit_feedback_applied"]:
                raise ValueError("fallback or audit feedback violates the loop contract")
            llm = loop["cost"]["llm"]
            if llm["cache_hits"] or llm["cached_calls"]:
                raise ValueError("cache use is forbidden in matched live loops")
            if any(loop.get(key) for key in ("score_window_read", "oos_window_read", "embargo_read")):
                raise ValueError("closed-window access detected")
            expected_real = block["domain"] == "real_dev"
            if bool(loop["real_data_read"]) != expected_real:
                raise ValueError("domain and real-data access disagree")
            if loop["audit_confirmed_unique_discoveries"] != sum(
                    bool(row["audit_confirmed"]) for row in loop["audit"]):
                raise ValueError("discovery count does not match audit rows")
    return {"n_blocks": len(blocks), "n_loops": n_loops, "all_loops_complete": True}


def unblind_rows(payload: dict, blind_map: dict) -> list[dict]:
    rows = []
    for block in payload["blocks"]:
        key = _block_key(block)
        mapping = blind_map[key]
        if set(mapping) != {"A", "B"} or set(mapping.values()) != {EVIDENCE, RAW}:
            raise ValueError("invalid arm unblinding map")
        for blind_arm, record in block["arms"].items():
            loop = record["result"]
            rows.append({
                "domain": block["domain"], "task": block["task"], "block_id": block["block_id"],
                "cluster_id": block["cluster_id"], "arm": mapping[blind_arm],
                "initial_library": block["initial_library"], "feature_names": block["feature_names"],
                "confirmed": loop["audit_confirmed_unique_discoveries"],
                "proposal_slots": loop["proposal_slots"],
                "per_slot": loop["audit_confirmed_per_slot"],
                "nominated": loop["nominated"],
                "status_counts": loop["proposal_status_counts"],
                "audit": loop["audit"], "cost": loop["cost"],
                "trajectory": loop["trajectory"], "independent_recompute": loop["independent_recompute"],
            })
    return rows


def _stratified_mean(values: np.ndarray, strata: np.ndarray) -> float:
    return float(np.mean([values[strata == stratum].mean() for stratum in sorted(set(strata))]))


def paired_inference(deltas, strata, bootstrap_seed: int, signflip_seed: int,
                     n_resamples: int = 10000) -> dict:
    values = np.asarray(deltas, dtype=float)
    groups = np.asarray(strata)
    if (not len(values) or len(values) != len(groups) or not np.isfinite(values).all()
            or any(np.sum(groups == group) < 1 for group in set(groups))):
        raise ValueError("invalid paired inference input")
    if n_resamples < 1000:
        raise ValueError("at least 1000 preregistered resamples are required")
    observed = _stratified_mean(values, groups)
    bootstrap_rng = np.random.default_rng(bootstrap_seed)
    bootstrap = np.empty(n_resamples)
    for index in range(n_resamples):
        means = []
        for group in sorted(set(groups)):
            subset = values[groups == group]
            means.append(float(bootstrap_rng.choice(subset, size=len(subset), replace=True).mean()))
        bootstrap[index] = np.mean(means)
    signflip_rng = np.random.default_rng(signflip_seed)
    randomized = np.empty(n_resamples)
    for index in range(n_resamples):
        randomized[index] = _stratified_mean(values * signflip_rng.choice([-1.0, 1.0], size=len(values)), groups)
    ci = np.quantile(bootstrap, [0.025, 0.975])
    p_value = (1 + int(np.sum(np.abs(randomized) >= abs(observed)))) / (n_resamples + 1)
    return {
        "delta_per_slot": observed,
        "delta_per_24_slots": observed * 24,
        "ci95_per_slot": [float(ci[0]), float(ci[1])],
        "ci95_per_24_slots": [float(ci[0] * 24), float(ci[1] * 24)],
        "p_value_two_sided": p_value,
        "n_resamples": n_resamples,
        "ci_method": "paired block bootstrap within fixed strata, percentile 95% CI",
        "p_method": "two-sided paired sign-flip randomization with +1 correction",
        "bootstrap_seed": bootstrap_seed,
        "signflip_seed": signflip_seed,
        "analysis_units": len(values),
        "strata": sorted(set(groups)),
    }


def _paired_deltas(rows: list[dict], domain: str, tasks: set[str] | None = None) -> tuple[list[dict], list[dict]]:
    grouped = {}
    for row in rows:
        if row["domain"] != domain or (tasks is not None and row["task"] not in tasks):
            continue
        key = (row["task"], row["block_id"])
        grouped.setdefault(key, {})[row["arm"]] = row
    pairs = []
    for (task, block_id), arms in sorted(grouped.items()):
        if set(arms) != {EVIDENCE, RAW}:
            raise ValueError("unpaired task/block")
        if arms[EVIDENCE]["proposal_slots"] != 24 or arms[RAW]["proposal_slots"] != 24:
            raise ValueError("formal primary endpoint requires 24 slots per loop")
        pairs.append({"task": task, "block_id": block_id, "cluster_id": arms[EVIDENCE]["cluster_id"],
                      "delta": arms[EVIDENCE]["per_slot"] - arms[RAW]["per_slot"]})
    if not pairs:
        raise ValueError("no matched pairs for analysis")
    breakdown = [{"task": task, "n_pairs": sum(row["task"] == task for row in pairs),
                  "delta_per_slot": float(np.mean([row["delta"] for row in pairs if row["task"] == task]))}
                 for task in sorted({row["task"] for row in pairs})]
    return pairs, breakdown


def _wilson(successes: int, total: int) -> list[float]:
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def _cost(rows: list[dict]) -> dict:
    totals = {"llm": {}, "evaluator_calls": {}, "wall_seconds": 0.0, "cpu_seconds": 0.0}
    statuses = {}
    for row in rows:
        for section in ("llm", "evaluator_calls"):
            for key, value in row["cost"][section].items():
                totals[section][key] = totals[section].get(key, 0) + value
        totals["wall_seconds"] += row["cost"]["wall_seconds"]
        totals["cpu_seconds"] += row["cost"]["cpu_seconds"]
        for status, count in row["status_counts"].items():
            statuses[status] = statuses.get(status, 0) + count
    evaluated = sum(statuses.get(status, 0) for status in VALID_EVALUATED)
    schema_ast_valid = sum(
        proposal.get("schema_valid") is True and proposal.get("search_valid") is not None
        for row in rows for proposal in row.get("trajectory", [])
    )
    slots = sum(row["proposal_slots"] for row in rows)
    return {
        **totals,
        "wall_seconds_sum_not_elapsed_time": totals.pop("wall_seconds"),
        "cpu_seconds_sum": totals.pop("cpu_seconds"),
        "proposal_status_counts": statuses,
        "evaluated_proposals": evaluated,
        "evaluated_rate": evaluated / slots if slots else None,
        "schema_ast_valid_evaluated_proposals": schema_ast_valid,
        "schema_ast_valid_evaluated_rate": schema_ast_valid / slots if slots else None,
        "duplicate_rate": statuses.get("DUPLICATE", 0) / slots if slots else None,
        "parse_rejection_rate": statuses.get("REJECTED_PARSE", 0) / slots if slots else None,
        "ast_rejection_rate": statuses.get("REJECTED_AST", 0) / slots if slots else None,
        "schema_invalid_evaluated_rate": (evaluated - schema_ast_valid) / slots if slots else None,
    }


def analyze_rows(rows: list[dict], test_only: bool = False) -> dict:
    result = {"created_at": utc_now(), "test_only": test_only, "cost": _cost(rows)}
    if test_only:
        result.update(primary_analysis_performed=False, effect_estimation_from_smoke_forbidden=True)
        return result
    synthetic, task_breakdown = _paired_deltas(rows, "synthetic", {"planted", "weak", "nonlinear", "regime_state"})
    primary = paired_inference([row["delta"] for row in synthetic], [row["task"] for row in synthetic],
                               bootstrap_seed=2026102401, signflip_seed=2026102402)
    primary.update(endpoint="audit-confirmed unique discoveries / 24 proposal slots, evidence minus raw",
                   n_matched_pairs=len(synthetic), task_breakdown=task_breakdown,
                   negative_control_excluded=True)
    real_pairs, _ = _paired_deltas(rows, "real_dev")
    clusters = []
    for cluster in sorted({row["cluster_id"] for row in real_pairs}):
        values = [row["delta"] for row in real_pairs if row["cluster_id"] == cluster]
        if len(values) != 4:
            raise ValueError("each real-dev temporal cluster requires four matched agent pairs")
        clusters.append({"cluster_id": cluster, "delta": float(np.mean(values)), "n_agent_pairs": len(values)})
    real = paired_inference([row["delta"] for row in clusters], ["real_dev"] * len(clusters),
                            bootstrap_seed=2026102403, signflip_seed=2026102404)
    real.update(n_matched_pairs=len(real_pairs), temporal_clusters=len(clusters),
                independent_market_samples=1, cluster_breakdown=clusters,
                interpretation="dev-internal temporal replication; not independent real-data OOS",
                descriptive_only=True,
                overlapping_expanding_search_history=True,
                agent_seed_pairs_are_not_independent_markets=True)
    null = []
    for arm in (EVIDENCE, RAW):
        selected = [row for row in rows if row["domain"] == "synthetic" and row["task"] == "null" and row["arm"] == arm]
        if len(selected) != 8:
            raise ValueError("negative control requires eight null loops per arm")
        successes = sum(row["confirmed"] > 0 for row in selected)
        null.append({"arm": arm, "n_loops": len(selected), "false_declaration_loops": successes,
                     "false_declaration_rate": successes / len(selected),
                     "wilson95": _wilson(successes, len(selected)), "not_in_primary_effect": True})
    result.update(primary_analysis_performed=True, synthetic_primary=primary, real_dev=real,
                  null_negative_control=null, exploratory=secondary_summary(rows),
                  independent_real_data_oos=False)
    return result


def analyze_run(run_dir: Path) -> dict:
    run_dir = Path(run_dir).resolve()
    manifest = read_json(run_dir / "manifest.json")
    if manifest["frozen_hashes"]["code"]["files"] != code_hashes():
        raise ValueError("analysis code differs from the frozen run code")
    receipt = read_json(run_dir / "execution_receipt.json")
    payload = read_json(run_dir / "blinded_results.json")
    if (receipt["status"] != "COMPLETED" or receipt["blinded_results_sha256"] != sha256_file(run_dir / "blinded_results.json")
            or receipt["provenance_sha256"] != sha256_file(run_dir / "provenance.json")
            or manifest["blind_map_sha256"] != sha256_file(run_dir / "blind_map.json")):
        raise ValueError("execution receipt or blinded artifact hash mismatch")
    for path, expected_hash in receipt["artifact_hashes"].items():
        if sha256_file(run_dir / path) != expected_hash:
            raise ValueError(f"execution artifact hash mismatch: {path}")
    validation = validate_blinded(payload, set(manifest["planned_blocks"]))
    blind_map = read_json(run_dir / "blind_map.json")
    provenance = read_json(run_dir / "provenance.json")
    for block in payload["blocks"]:
        key = _block_key(block)
        for blind_arm, record in block["arms"].items():
            arm = blind_map[key][blind_arm]
            source = provenance[key][arm]
            completion = (run_dir / source["path"]).resolve()
            if not completion.is_relative_to(run_dir) or sha256_file(completion) != source["completion_sha256"]:
                raise ValueError("provenance completion path or hash mismatch")
            if sanitize_loop(read_json(completion)) != record["result"]:
                raise ValueError("blinded result differs from sanitized source completion")
            operations = read_jsonl(completion.parent / "operations.jsonl")
            started = [row for row in operations if row.get("event") == "started"]
            finished = [row for row in operations if row.get("event") == "finished"]
            expected = record["result"]["attempted_slots"] + record["result"]["cost"]["evaluator_calls"]["total"]
            if len(started) != expected or len(finished) != expected or len(operations) != 2 * expected:
                raise ValueError("operation ledger is incomplete or has unaccounted calls")
            if len(read_jsonl(completion.parent / "llm_responses.jsonl")) != record["result"]["attempted_slots"]:
                raise ValueError("LLM response ledger does not account for every proposal slot")
    validation["operation_ledgers_verified"] = True
    result = {"schema": "AUTO-RESEARCH-LIVE-QD-ANALYSIS-1", "run_id": manifest["run_id"],
              "validation": validation,
              "blinding": {"validation_before_unblinding": True, "provenance_verified": True,
                           "arm_identifiers_removed_from_blinded_results": True}}
    if manifest.get("test_only"):
        result.update(analyze_rows([], test_only=True))
        neutral = [{"cost": record["result"]["cost"],
                    "status_counts": record["result"]["proposal_status_counts"],
                    "trajectory": record["result"]["trajectory"],
                    "proposal_slots": record["result"]["proposal_slots"]}
                   for block in payload["blocks"] for record in block["arms"].values()]
        result["cost"] = _cost(neutral)
    else:
        result.update(analyze_rows(unblind_rows(payload, blind_map)))
    write_immutable_json(run_dir / "analysis.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args()
    result = analyze_run(args.run_dir)
    print(__import__("json").dumps(result, ensure_ascii=False, indent=1, allow_nan=False))


if __name__ == "__main__":
    main()
