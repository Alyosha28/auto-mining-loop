"""Resolve a replay contract without reading market data or changing frozen inputs."""
from __future__ import annotations

import copy
from datetime import date, timedelta
import hashlib
import json
import math
import re


def canonical_hash(value) -> str:
    """SHA-256 of deterministic strict JSON (not a hash of source-file bytes)."""
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _date(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"{name} must be an ISO YYYY-MM-DD date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid {name}: {value}") from exc


def _number(value, name, *, integer=False, minimum=0, strict=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric, not boolean")
    if not math.isfinite(value) or (integer and not isinstance(value, int)):
        raise ValueError(f"invalid {name}")
    if value < minimum or (strict and value == minimum):
        raise ValueError(f"{name} below allowed minimum")


def _access(config, state):
    start, end = _date(config.get("start"), "start"), _date(config.get("end"), "end")
    if start > end:
        raise ValueError("start must not exceed end")
    purpose = config.get("evaluation_purpose", "diagnostic_replay")
    if purpose not in {"diagnostic_replay", "development", "independent_evaluation"}:
        raise ValueError("unrecognized evaluation_purpose")
    if not isinstance(state, dict) or state.get("schema_version") not in {
            "RESEARCH-EXPOSURE-1", "RESEARCH-EXPOSURE-2", "RESEARCH-EXPOSURE-3"}:
        raise ValueError("unrecognized exposure state schema")
    version = state['schema_version']
    if end >= date(2023, 1, 1):
        authorization = state.get('authorization', {})
        research_release = (version in {'RESEARCH-EXPOSURE-2', 'RESEARCH-EXPOSURE-3'}
                            and authorization.get('allow_post_2023_research') is True
                            and authorization.get('source') == 'explicit_user_message')
        final_release = (version == 'RESEARCH-EXPOSURE-3'
                         and authorization.get('allow_final_holdout_evaluation') is True
                         and authorization.get('source') == 'explicit_user_message')
        if not research_release and not (purpose == 'independent_evaluation' and final_release):
            raise ValueError("2023+ remains sealed without explicit current authorization")
    allowed_statuses = {"already_used_for_selection", "already_viewed", "sealed"}
    if version == 'RESEARCH-EXPOSURE-2':
        allowed_statuses.add('authorized_for_research')
    if version == 'RESEARCH-EXPOSURE-3':
        allowed_statuses.update({'authorized_for_research', 'fresh_holdout'})
    intervals = state.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        raise ValueError("exposure intervals required")
    parsed = []
    for interval in intervals:
        if not isinstance(interval, dict):
            raise ValueError("invalid exposure interval")
        a, b = _date(interval.get("start"), "interval.start"), _date(interval.get("end"), "interval.end")
        if a > b or interval.get("status") not in allowed_statuses:
            raise ValueError("invalid exposure interval or status")
        parsed.append((a, b, interval))
    parsed.sort(key=lambda item: item[0])
    for previous, current in zip(parsed, parsed[1:]):
        if current[0] <= previous[1]:
            raise ValueError("overlapping exposure intervals")
    cursor, matched = start, []
    for a, b, interval in parsed:
        if b < start or a > end:
            continue
        if a > cursor:
            raise ValueError("requested dates are not covered by exposure state")
        if interval["status"] == "sealed":
            raise ValueError("requested interval is sealed")
        if purpose == "independent_evaluation":
            if interval["status"] != "fresh_holdout":
                raise ValueError("exposed interval is ineligible; independent evaluation requires a fresh holdout interval")
            if (interval.get("selection_access") is not False
                    or interval.get("outcome_access") is not False):
                raise ValueError("fresh holdout lacks explicit prior-access denials")
        elif interval["status"] == "fresh_holdout":
            raise ValueError("fresh holdout cannot be consumed by development or replay")
        matched.append(copy.deepcopy(interval))
        cursor = min(b, end) + timedelta(days=1)
    if cursor <= end:
        raise ValueError("requested dates are not covered by exposure state")
    independent = purpose == "independent_evaluation"
    return {"evaluation_purpose": purpose, "independent_evaluation_eligible": independent,
            "start": start.isoformat(), "end": end.isoformat(), "intervals": matched,
            "verdict": "allowed_independent_evaluation" if independent else "allowed_non_independent_research"}


def resolve_run_config(config: dict, research_config: dict, exposure_state: dict,
                       task: str, factor_ids: list[str] | None = None) -> dict:
    """Freeze effective settings. Raises ValueError on ambiguous/unsafe inputs.

    Legacy top-level schema/scope are retained as provenance, never used to grant
    independent evaluation. The supplied current exposure ledger is authoritative.
    """
    if not isinstance(config, dict) or not isinstance(research_config, dict):
        raise ValueError("config and research_config must be dictionaries")
    if task not in ("all", "factors", "portfolio"):
        raise ValueError("task must be all, factors, or portfolio")
    if factor_ids is not None:
        if not isinstance(factor_ids, list) or not factor_ids or any(not isinstance(x, str) or not x.strip() for x in factor_ids):
            raise ValueError("factor_ids must be a list of nonempty strings or None")
        if len(factor_ids) != len(set(factor_ids)):
            raise ValueError("duplicate requested factors")
    if config.get("production_ready", False) is not False:
        raise ValueError("production_ready must remain false")
    periods = config.get("periods_per_year")
    _number(periods, "periods_per_year", strict=True)
    defaults = {"quantiles": 5, "winsorize": [0.01, 0.99], "standardize": True,
                "min_obs_per_day": 30, "periods_per_year": periods,
                "icir_ddof": 1, "icir_annualized": False}
    overrides = config.get("factor_evaluation", {})
    if not isinstance(overrides, dict) or set(overrides) - set(defaults):
        raise ValueError("unknown factor_evaluation settings")
    evaluation = copy.deepcopy({**defaults, **overrides})
    _number(evaluation["quantiles"], "quantiles", integer=True, minimum=2)
    _number(evaluation["min_obs_per_day"], "min_obs_per_day", integer=True, minimum=2)
    _number(evaluation["periods_per_year"], "factor_evaluation.periods_per_year", strict=True)
    if evaluation["periods_per_year"] != periods:
        raise ValueError("conflicting periods_per_year")
    _number(evaluation["icir_ddof"], "icir_ddof", integer=True)
    for name in ("standardize", "icir_annualized"):
        if not isinstance(evaluation[name], bool):
            raise ValueError(f"{name} must be boolean")
    if evaluation["icir_ddof"] != 1 or evaluation["icir_annualized"] is not False:
        raise ValueError("ICIR semantics are frozen: icir_ddof=1, icir_annualized=false")
    bounds = evaluation["winsorize"]
    if not isinstance(bounds, list) or len(bounds) != 2:
        raise ValueError("winsorize must contain two quantiles")
    for value in bounds:
        _number(value, "winsorize")
    if not 0 <= bounds[0] < bounds[1] <= 1:
        raise ValueError("invalid winsorize bounds")
    costs = research_config.get("costs")
    if not isinstance(costs, dict) or not costs:
        raise ValueError("research_config.costs required")
    if "costs" in config and canonical_hash(config["costs"]) != canonical_hash(costs):
        raise ValueError("conflicting costs; research_config.costs is authoritative")
    resolved = copy.deepcopy(config)
    resolved.update({"resolved_run_schema": "RESOLVED-RUN-1", "factor_evaluation": evaluation,
                     "costs": copy.deepcopy(costs), "task": task,
                     "requested_factors": copy.deepcopy(factor_ids), "production_ready": False,
                     "research_access": _access(config, exposure_state),
                     "exposure_state": copy.deepcopy(exposure_state),
                     "evaluation_purpose": config.get("evaluation_purpose", "diagnostic_replay"),
                     "resolution_metadata": {
                         "costs_source": "research_config.costs",
                         "evaluation_overrides": sorted(overrides),
                         "inherited_evaluation_defaults": sorted(set(defaults) - set(overrides)),
                         "research_config_hash": canonical_hash(research_config),
                         "exposure_state_hash": canonical_hash(exposure_state)}})
    canonical_hash(resolved)  # Reject non-JSON and non-finite values before recording.
    return resolved
