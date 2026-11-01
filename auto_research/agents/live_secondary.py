#!/usr/bin/env python3
"""Exploratory trajectory, mechanism-overlap and cost summaries for live loops."""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
from agents.expr_grammar import parse


def _mean(values):
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return sum(finite) / len(finite) if finite else None


def mechanism_cluster(formula: str, features: list[str]) -> str:
    node = parse(formula, set(features))
    leaves = set()

    def walk(value):
        if value["kind"] == "leaf":
            leaves.add(value["feature"])
        elif value["kind"] == "unary":
            walk(value["arg"])
        else:
            walk(value["left"])
            walk(value["right"])

    walk(node)
    if not leaves:
        raise ValueError(f"mechanism has no input leaves: {formula}")
    return "|".join(sorted(leaves))


def _loop_metrics(row: dict) -> dict:
    features = row["feature_names"]
    initial_clusters = {mechanism_cluster(formula, features) for formula in row["initial_library"]}
    trajectory = sorted(row["trajectory"], key=lambda item: (item["generation"], item["slot"]))
    curve = []
    valid_count = 0
    first_valid = None
    dry = 0
    max_dry = 0
    for index, proposal in enumerate(trajectory, start=1):
        if proposal.get("search_valid") is True:
            valid_count += 1
            if first_valid is None:
                first_valid = index
            dry = 0
        else:
            dry += 1
            max_dry = max(max_dry, dry)
        curve.append(valid_count)
    confirmed_formulas = {audit["formula"] for audit in row["audit"] if audit["audit_confirmed"]}
    first_confirmed = next((index for index, proposal in enumerate(trajectory, start=1)
                            if proposal.get("formula") in confirmed_formulas), None)
    parent_counts = {}
    for proposal in trajectory:
        parent = proposal["parent_id"]
        parent_counts[parent] = parent_counts.get(parent, 0) + 1
    nominated_clusters = [mechanism_cluster(audit["formula"], features) for audit in row["audit"]]
    novel = {cluster for cluster, audit in zip(nominated_clusters, row["audit"])
             if audit["audit_confirmed"] and cluster not in initial_clusters}
    nominated = row["nominated"]
    recompute = row["independent_recompute"]
    coverage = ([proposal.get("coverage") for proposal in trajectory]
                + [audit.get("coverage") for audit in row["audit"]])
    coverage = [value for value in coverage if value is not None]
    return {
        "block_id": row["block_id"],
        "curve": curve,
        "curve_auc": _mean(value / row["proposal_slots"] for value in curve),
        "search_valid": valid_count,
        "budget_to_first_valid": first_valid,
        "budget_to_first_audit_confirmed": first_confirmed,
        "audit_confirmation_precision": row["confirmed"] / nominated if nominated else None,
        "zero_nominations": nominated == 0,
        "audit_ic": _mean(audit.get("signed_mean_ic") for audit in row["audit"]),
        "audit_icir": _mean(audit.get("icir") for audit in row["audit"]),
        "confirmed_ic": _mean(audit.get("signed_mean_ic") for audit in row["audit"] if audit["audit_confirmed"]),
        "confirmed_icir": _mean(audit.get("icir") for audit in row["audit"] if audit["audit_confirmed"]),
        "min_coverage": min(coverage, default=None),
        "mechanism_novel": len(novel),
        "mechanism_dedup_rate": 1 - len(set(nominated_clusters)) / nominated if nominated else None,
        "unique_parent_ids": len(parent_counts),
        "max_parent_reuse": max(parent_counts.values(), default=0),
        "max_without_search_valid": max_dry,
        "search_fitness": _mean(proposal.get("search_fitness") for proposal in trajectory),
        "recompute_checks": recompute["n_checks"],
        "recompute_consistent": recompute["n_consistent"],
    }


def secondary_summary(rows: list[dict]) -> dict:
    grouped = {}
    for row in rows:
        grouped.setdefault((row["domain"], row["task"], row["arm"]), []).append(row)
    result = {}
    for (domain, task, arm), selected in sorted(grouped.items()):
        loops = [_loop_metrics(row) for row in selected]
        confirmed = sum(row["confirmed"] for row in selected)
        llm = {}
        evaluator = {}
        wall = 0.0
        cpu = 0.0
        for row in selected:
            for key, value in row["cost"]["llm"].items():
                llm[key] = llm.get(key, 0) + value
            for key, value in row["cost"]["evaluator_calls"].items():
                evaluator[key] = evaluator.get(key, 0) + value
            wall += row["cost"]["wall_seconds"]
            cpu += row["cost"]["cpu_seconds"]
        summary = {
            "n_loops": len(loops),
            "mean_search_valid": _mean(loop["search_valid"] for loop in loops),
            "loops_with_search_valid": sum(loop["budget_to_first_valid"] is not None for loop in loops),
            "mean_budget_to_first_valid": _mean(loop["budget_to_first_valid"] for loop in loops),
            "budget_to_first_valid_denominator": "only loops with a search-valid proposal; loops_with_search_valid gives the count",
            "mean_search_curve_auc": _mean(loop["curve_auc"] for loop in loops),
            "mean_cumulative_search_valid_by_slot": [
                _mean(loop["curve"][index] for loop in loops) for index in range(24)
            ],
            "loops_with_audit_confirmed": sum(loop["budget_to_first_audit_confirmed"] is not None for loop in loops),
            "mean_budget_to_first_audit_confirmed": _mean(loop["budget_to_first_audit_confirmed"] for loop in loops),
            "budget_to_first_audit_confirmed_denominator": "only loops with an audit-confirmed discovery; loops_with_audit_confirmed gives the count",
            "mean_audit_confirmation_precision": _mean(loop["audit_confirmation_precision"] for loop in loops),
            "audit_confirmation_precision_denominator": "only loops with nominations; zero_nomination_loops is reported separately",
            "zero_nomination_loops": sum(loop["zero_nominations"] for loop in loops),
            "audit_signed_mean_ic": _mean(loop["audit_ic"] for loop in loops),
            "audit_icir": _mean(loop["audit_icir"] for loop in loops),
            "confirmed_signed_mean_ic": _mean(loop["confirmed_ic"] for loop in loops),
            "confirmed_icir": _mean(loop["confirmed_icir"] for loop in loops),
            "minimum_coverage": min((loop["min_coverage"] for loop in loops if loop["min_coverage"] is not None),
                                    default=None),
            "mechanism_novel_discoveries": sum(loop["mechanism_novel"] for loop in loops),
            "mechanism_novel_discoveries_per_24_slots": sum(loop["mechanism_novel"] for loop in loops) / len(loops),
            "mean_mechanism_dedup_rate": _mean(loop["mechanism_dedup_rate"] for loop in loops),
            "mean_unique_parent_ids": _mean(loop["unique_parent_ids"] for loop in loops),
            "mean_max_parent_reuse": _mean(loop["max_parent_reuse"] for loop in loops),
            "mean_max_consecutive_without_search_valid": _mean(loop["max_without_search_valid"] for loop in loops),
            "mean_search_fitness": _mean(loop["search_fitness"] for loop in loops),
            "recompute_checks": sum(loop["recompute_checks"] for loop in loops),
            "recompute_consistent": sum(loop["recompute_consistent"] for loop in loops),
            "recompute_consistency_rate": (sum(loop["recompute_consistent"] for loop in loops)
                                           / sum(loop["recompute_checks"] for loop in loops)
                                           if sum(loop["recompute_checks"] for loop in loops) else None),
            "llm_calls_per_confirmed": llm.get("actual_calls", 0) / confirmed if confirmed else None,
            "tokens_per_confirmed": llm.get("total_tokens", 0) / confirmed if confirmed else None,
            "evaluator_calls_per_confirmed": evaluator.get("total", 0) / confirmed if confirmed else None,
            "cpu_seconds_per_confirmed": cpu / confirmed if confirmed else None,
            "wall_seconds_sum_per_confirmed": wall / confirmed if confirmed else None,
            "exploratory_not_a_coprimary_endpoint": True,
            "loops": loops,
        }
        result.setdefault(domain, {}).setdefault(task, {})[arm] = summary
    return result
