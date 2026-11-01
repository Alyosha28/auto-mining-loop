#!/usr/bin/env python3
"""Executable budget and adoption contract for bounded auto-research loops."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from evo_probe import GuardrailError

CONTRACT_SCHEMA = 'AUTO-RESEARCH-LOOP-CONTRACT-1'
USAGE_SCHEMA = 'AUTO-RESEARCH-MODEL-USAGE-1'
GUARD_FIELDS = {
    'min_delta_coverage',
    'min_delta_pass_rate',
    'max_delta_turnover_proxy',
    'min_delta_novelty',
    'max_delta_duplicate_rate',
}
BUDGET_FIELDS = {
    'max_decisions',
    'max_candidate_budget',
    'max_model_calls',
    'max_input_tokens',
    'max_output_tokens',
    'max_no_improvement',
    'max_debug_depth',
}


def canonical_hash(payload) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _integer(payload: dict, key: str, minimum: int) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise GuardrailError(f'{key} must be an integer >= {minimum}')
    return value


def _number(payload: dict, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GuardrailError(f'{key} must be numeric')
    value = float(value)
    if not math.isfinite(value):
        raise GuardrailError(f'{key} must be finite')
    return value


def validate_contract(payload: dict) -> dict:
    if not isinstance(payload, dict) or payload.get('schema') != CONTRACT_SCHEMA:
        raise GuardrailError(f'loop contract must use {CONTRACT_SCHEMA}')
    allowed = {'schema', 'budget', 'primary', 'guards', 'online_evaluation', 'final_evaluation'}
    if set(payload) != allowed:
        raise GuardrailError(f'loop contract fields must be exactly {sorted(allowed)}')
    if payload['online_evaluation'] != 'probe' or payload['final_evaluation'] != 'select':
        raise GuardrailError('online evaluation must be probe and final evaluation must be select')
    budget = payload['budget']
    if not isinstance(budget, dict) or set(budget) != BUDGET_FIELDS:
        raise GuardrailError(f'budget fields must be exactly {sorted(BUDGET_FIELDS)}')
    for key in BUDGET_FIELDS:
        _integer(budget, key, 0 if key == 'max_debug_depth' else 1)
    primary = payload['primary']
    if not isinstance(primary, dict) or set(primary) != {'metric', 'min_delta'}:
        raise GuardrailError('primary must contain metric and min_delta')
    if primary['metric'] != 'mean_abs_ic':
        raise GuardrailError('the current loop primary metric must be mean_abs_ic')
    _number(primary, 'min_delta')
    guards = payload['guards']
    if not isinstance(guards, dict) or set(guards) != GUARD_FIELDS:
        raise GuardrailError(f'guard fields must be exactly {sorted(GUARD_FIELDS)}')
    for key in GUARD_FIELDS:
        _number(guards, key)
    return payload


def load_contract(path: Path) -> dict:
    return validate_contract(json.loads(Path(path).read_text(encoding='utf-8')))


def validate_usage(payload: dict, model_id: str) -> dict:
    if not isinstance(payload, dict) or payload.get('schema') != USAGE_SCHEMA:
        raise GuardrailError(f'model usage must use {USAGE_SCHEMA}')
    if set(payload) != {'schema', 'model_id', 'calls', 'input_tokens', 'output_tokens'}:
        raise GuardrailError('model usage contains unsupported fields')
    if payload['model_id'] != model_id:
        raise GuardrailError('model usage model_id differs from the frozen model')
    for key in ('calls', 'input_tokens', 'output_tokens'):
        _integer(payload, key, 0)
    return payload


def metric_delta(reference: dict, adopted: dict) -> dict:
    metrics = ('mean_abs_ic', 'coverage', 'pass_rate', 'turnover_proxy', 'novelty', 'duplicate_rate')
    delta = {}
    for metric in metrics:
        if metric not in reference or metric not in adopted:
            raise GuardrailError(f'contract metrics are missing {metric}')
        base = _number(reference, metric)
        candidate = _number(adopted, metric)
        delta[f'delta_{metric}'] = candidate - base
    return delta


def evaluate_adoption(contract: dict, reference: dict, adopted: dict) -> dict:
    delta = metric_delta(reference, adopted)
    primary_delta = delta['delta_mean_abs_ic']
    reasons = []
    if primary_delta <= float(contract['primary']['min_delta']):
        reasons.append('primary_delta_not_above_threshold')
    for key, delta_key in (
            ('min_delta_coverage', 'delta_coverage'),
            ('min_delta_pass_rate', 'delta_pass_rate'),
            ('min_delta_novelty', 'delta_novelty')):
        if delta[delta_key] < float(contract['guards'][key]):
            reasons.append(key)
    for key, delta_key in (
            ('max_delta_turnover_proxy', 'delta_turnover_proxy'),
            ('max_delta_duplicate_rate', 'delta_duplicate_rate')):
        if delta[delta_key] > float(contract['guards'][key]):
            reasons.append(key)
    return {'supported': not reasons, 'primary_delta': primary_delta,
            'reject_reasons': reasons, 'delta': delta}
