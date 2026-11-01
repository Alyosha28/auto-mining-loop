#!/usr/bin/env python3
"""Evolution-harness objects, deterministic edit catalog and guardrails (v2 evo loop).

The "harness" here is the *process* used to evolve factors: operator library,
seed-pool policy, probe set, selection/composition rules, budget and stop rules.
It is a JSON-able dict with a frozen protocol/guardrail section that edits may
never touch.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "EVO-HARNESS-1"
FROZEN_COMPONENTS = ("protocol", "guardrails")

PREFERRED_SEEDS = [
    "momentum_20", "volatility_20", "turnover_mean_20", "reversal_5",
    "amihud_20", "volume_ratio_5_20", "momentum_60", "volatility_60",
]


def default_harness(domain: str = "price_volume", seed_features: list[str] | None = None) -> dict:
    seeds = list(seed_features or PREFERRED_SEEDS[:6])
    return {
        "schema": SCHEMA,
        "domain": domain,
        "protocol": {
            "dev_start": "2017-01-01",
            "dev_end": "2020-11-30",
            "embargo": ["2020-12-01", "2020-12-31"],
            "score_window": ["2021-01-01", "2023-12-31"],
            "budget_candidates": 24,
            "min_ic_pairs": 30,
            "probe_split": 0.7,
        },
        "guardrails": {
            "min_coverage": 0.96,
            "max_runtime_seconds": 900,
            "score_window_reads": 0,
            "fail_closed": True,
        },
        "operators": ["residual_size", "delta4", "delta13", "neg"],
        "seed_pool": [{"name": name, "feature": name, "transform": "raw"} for name in seeds],
        "skills": {"mutation_per_seed": 1, "crossover_pairs": 2, "sign_by_ic": True},
        "probe": {"year_split": False, "duplication_corr": 0.9},
        "memory": {
            "experience_dir": "evo_rsif/experience",
            "top_k_lessons": 3,
        },
        "plugin": {"coverage_stop": True, "ledger": True, "fail_not_zero": True},
        "selection": {"rule": "select_pass_then_ic", "anonymize": True},
    }


# Deterministic editors: each one is an executable harness edit with an intent.
# ``patch`` is applied by apply_edit; only non-frozen components are accepted.
EDIT_CATALOG: list[dict] = [
    {"id": "inv_add_rank", "kind": "INV", "component": "operators", "op": "append", "value": "rank",
     "intent": "cross-sectional normalization; can rescue skewed raw features"},
    {"id": "inv_add_zscore", "kind": "INV", "component": "operators", "op": "append", "value": "zscore",
     "intent": "cross-sectional standardization; compares features on a common scale"},
    {"id": "inv_add_winsorize", "kind": "INV", "component": "operators", "op": "append", "value": "winsorize",
     "intent": "clip 1%/99% tails; reduces outlier-driven IC instability"},
    {"id": "inv_enable_year_split", "kind": "INV", "component": "probe.year_split", "op": "set", "value": True,
     "intent": "add a per-year IC stability probe; detects one-year flukes"},
    {"id": "inv_more_crossover", "kind": "INV", "component": "skills.crossover_pairs", "op": "set", "value": 4,
     "intent": "widen crossover set; may add complementary candidates at the same budget"},
    {"id": "dom_add_vol_scaled", "kind": "DOM", "component": "operators", "op": "append", "value": "vol_scaled",
     "intent": "domain knowledge: idio-vol scaling in price-volume families"},
    {"id": "dom_add_delta_w", "kind": "DOM", "component": "operators", "op": "append", "value": "delta_w",
     "intent": "domain knowledge: one-week change matters more than daily noise in A-share weekly labels"},
]


def find_edit(edit_id: str) -> dict | None:
    for edit in EDIT_CATALOG:
        if edit["id"] == edit_id:
            return edit
    return None


def validate_edit(edit: dict) -> None:
    component = edit.get("component", "")
    if component.split(".")[0] in FROZEN_COMPONENTS:
        raise ValueError(f"edit may not touch frozen component: {component}")
    if component not in ("atomic", "operators", "operators.remove", "skills.crossover", "skills.crossover_pairs",
                         "skills.crossover_rule", "skills.pair_selection", "skills.mutation_per_seed",
                         "probe.year_split", "probe.duplication_corr"):
        raise ValueError(f"unsupported edit component: {component}")


def _set_path(harness: dict, path: str, value) -> None:
    parts = path.split(".")
    node = harness
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def apply_edits(harness: dict, edits: list[dict]) -> dict:
    """Apply one atomic proposal and validate the final operator roster."""
    new = copy.deepcopy(harness)
    for edit in edits:
        if edit['component'] == 'operators.remove':
            validate_edit(edit)
            value = edit['value']
            removed = {value} if isinstance(value, str) else set(value)
            new['operators'] = [operator for operator in new.get('operators', [])
                                if operator not in removed]
        else:
            new = apply_edit(new, edit)
    if not new.get('operators'):
        raise ValueError("atomic proposal must retain at least one operator")
    return new


def apply_edit(harness: dict, edit: dict) -> dict:
    """Return a new harness with the edit applied. Frozen sections are hash-pinned."""
    validate_edit(edit)
    new = copy.deepcopy(harness)
    component = edit["component"]
    if component == "atomic":
        return apply_edits(new, edit['value'])
    if component == "operators":
        operators = list(new.get("operators", []))
        value = edit["value"]
        if value not in operators:
            operators.append(value)
        new["operators"] = operators
    elif component == "operators.remove":
        value = edit["value"]
        removed = {value} if isinstance(value, str) else set(value)
        operators = [operator for operator in new.get("operators", []) if operator not in removed]
        if not operators:
            raise ValueError("operator removal must retain at least one operator")
        new["operators"] = operators
    elif component == "skills.crossover":
        skills = new.setdefault("skills", {})
        value = edit["value"]
        skills["crossover_pairs"] = value["pairs"]
        skills["crossover_rule"] = value["rule"]
        if "pair_selection" in value:
            skills["pair_selection"] = value["pair_selection"]
    else:
        _set_path(new, component, edit["value"])
    return new


def harness_hash(harness: dict) -> str:
    payload = json.dumps(harness, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def guardrail_snapshot(harness: dict) -> dict:
    return {
        "protocol": copy.deepcopy(harness.get("protocol", {})),
        "guardrails": copy.deepcopy(harness.get("guardrails", {})),
        "score_window_reads": 0,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------- LLM proposer surface

def editable_surface() -> dict:
    """The editable surface an LLM proposer may touch (frozen sections excluded)."""
    from evo_probe import AVAILABLE_OPERATORS
    active_operators = [operator for operator in AVAILABLE_OPERATORS if operator != 'neg']
    return {
        'operators': {'op': 'append', 'allowed': active_operators,
                      'semantics': 'append to operator order; direction is already fitted by sign_by_ic'},
        'operators.remove': {'op': 'remove', 'allowed': list(AVAILABLE_OPERATORS),
                             'value_shape': 'one operator or a non-empty list; retain at least one operator',
                             'semantics': 'remove lower-priority operators to reallocate a bounded search budget'},
        'skills.crossover': {'op': 'configure',
                             'allowed': {'pairs': [0, 1, 2, 3, 4, 6, 8],
                                         'rule': ['legacy_rank_minus', 'signed_rank_sum'],
                                         'pair_selection': ['strength_then_corr', 'diversified']},
                             'required': ['pairs', 'rule'],
                             'semantics': 'atomic crossover configuration; use this to enable a rule and its slots together '
                                          'from crossover_pairs=0; pair_selection is optional and defaults to the parent policy'},
        'skills.crossover_pairs': {'op': 'set', 'allowed': [0, 1, 2, 3, 4, 6, 8],
                                   'semantics': 'slots reserved before mutations and raw-seed padding'},
        'skills.crossover_rule': {'op': 'set', 'allowed': ['legacy_rank_minus', 'signed_rank_sum'],
                                  'semantics': 'signed_rank_sum adds equal-weight parent ranks, each signed by fit-only mean IC; '
                                               'legacy_rank_minus preserves the prior A-minus-B behavior'},
        'skills.pair_selection': {'op': 'set', 'allowed': ['strength_then_corr', 'diversified'],
                                  'semantics': 'strength_then_corr sorts by total absolute fit IC then |IC-series corr|; '
                                               'diversified sorts by total absolute fit IC * (1 - |fit IC-series corr|)'},
        'skills.mutation_per_seed': {'op': 'set', 'allowed': [1, 2, 3],
                                     'semantics': 'maximum operator depth per seed, in current operator order; '
                                                  'round-robin across seeds within remaining budget'},
        'probe.year_split': {'op': 'set', 'allowed': [True, False],
                             'semantics': 'reporting diagnostic only; does not change candidates or adoption'},
        'probe.duplication_corr': {'op': 'set', 'allowed': [0.8, 0.85, 0.9, 0.95],
                                   'semantics': 'duplicate-rate threshold only; does not prune candidates'},
    }


def validate_proposal(edit: dict) -> dict:
    """Validate and normalise one LLM-proposed harness edit (fail closed)."""
    if not isinstance(edit, dict):
        raise ValueError('proposal must be a JSON object')
    edit_id = str(edit.get('id') or '').strip()
    if not edit_id:
        raise ValueError('proposal needs a non-empty id')
    kind = str(edit.get('kind') or '').upper()
    if kind not in ('INV', 'DOM'):
        raise ValueError(f'proposal {edit_id}: kind must be INV or DOM')
    component = str(edit.get('component') or '')
    surface = editable_surface()
    if component not in surface:
        raise ValueError(f'proposal {edit_id}: component {component!r} is outside the editable surface')
    op = str(edit.get('op') or surface[component]['op'])
    if op != surface[component]['op']:
        raise ValueError(f'proposal {edit_id}: component {component!r} only supports op {surface[component]["op"]}')
    value = edit.get('value')
    allowed = surface[component]['allowed']
    if component == 'operators':
        if value not in allowed:
            raise ValueError(f'proposal {edit_id}: operator {value!r} not in the operator library')
    elif component == 'operators.remove':
        values = [value] if isinstance(value, str) else value
        if not isinstance(values, list) or not values or any(item not in allowed for item in values):
            raise ValueError(f'proposal {edit_id}: invalid operator removal {value!r}')
        value = list(dict.fromkeys(values))
    elif component == 'skills.crossover':
        if not isinstance(value, dict) or set(value) - set(allowed) or not {'pairs', 'rule'}.issubset(value):
            raise ValueError(f'proposal {edit_id}: crossover needs only pairs/rule and optional pair_selection')
        if type(value['pairs']) is not int or value['pairs'] not in allowed['pairs']:
            raise ValueError(f'proposal {edit_id}: invalid crossover pairs {value["pairs"]!r}')
        if value['rule'] not in allowed['rule']:
            raise ValueError(f'proposal {edit_id}: invalid crossover rule {value["rule"]!r}')
        if 'pair_selection' in value and value['pair_selection'] not in allowed['pair_selection']:
            raise ValueError(f'proposal {edit_id}: invalid pair selection {value["pair_selection"]!r}')
        value = {key: value[key] for key in ('pairs', 'rule', 'pair_selection') if key in value}
    elif value not in allowed:
        raise ValueError(f'proposal {edit_id}: value {value!r} not allowed for {component!r} (allowed: {allowed})')
    intent = str(edit.get('intent') or '').strip()
    if not intent:
        raise ValueError(f'proposal {edit_id}: missing intent (what deficiency it targets and expected change)')
    return {'id': edit_id, 'kind': kind, 'component': component, 'op': op,
            'value': value, 'intent': intent}
