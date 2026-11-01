#!/usr/bin/env python3
"""Shared fail-closed contract for structured proposer traces."""
from __future__ import annotations

import re

from evo_probe import GuardrailError

TRACE_SCHEMA = 'AUTO-RESEARCH-PROPOSER-TRACE-1'
TRACE_KEYS = {'schema', 'source_run', 'scope', 'seed_semantics', 'search_policy',
              'reference_probe_metrics', 'adopted_probe_metrics', 'proposals',
              'kept_edits', 'next_question'}
PROPOSAL_KEYS = {'edit', 'probe_metrics', 'delta', 'support_reason', 'verdict',
                 'roster_effect', 'candidate_roster'}
EDIT_KEYS = {'id', 'kind', 'component', 'op', 'value', 'intent'}
ROSTER_KEYS = {'name', 'kind', 'operator', 'parents', 'mean_ic', 'icir', 'n_dates',
               'fit_pair_correlation', 'fit_pair_strength'}
FORBIDDEN_KEYS = {'forward_return', 'research_mask', 'symbol', 'raw_panel',
                  'select', 'adopted_select', 'select_metrics'}
REFERENCE_KEYS = {'child_run'}


def reject_sensitive_fields(payload) -> None:
    """Reject raw-market and held-out/child-result key aliases at any depth."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            text = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', str(key))
            normalized = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')
            tokens = set(normalized.split('_'))
            if (normalized not in REFERENCE_KEYS
                    and (normalized in FORBIDDEN_KEYS or normalized.startswith('select_')
                         or normalized.endswith('_select') or 'holdout' in tokens or 'child' in tokens)):
                raise GuardrailError(f'trace evidence contains forbidden field: {key}')
            reject_sensitive_fields(value)
    elif isinstance(payload, list):
        for value in payload:
            reject_sensitive_fields(value)


def _check_keys(value: dict, allowed: set[str], context: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise GuardrailError(f'{context} contains fields outside the trace contract: {sorted(unknown)}')


def validate_execution_trace(payload) -> dict:
    if not isinstance(payload, dict) or payload.get('schema') != TRACE_SCHEMA:
        raise GuardrailError(f'trace file must use {TRACE_SCHEMA}')
    _check_keys(payload, TRACE_KEYS, 'trace file')
    if not isinstance(payload.get('source_run'), str) or not payload['source_run']:
        raise GuardrailError('trace file needs a nonempty source_run')
    proposals = payload.get('proposals')
    if not isinstance(proposals, list):
        raise GuardrailError('trace file needs a proposals list')
    for index, proposal in enumerate(proposals):
        if not isinstance(proposal, dict):
            raise GuardrailError('each trace proposal must be an object')
        _check_keys(proposal, PROPOSAL_KEYS, f'proposal {index}')
        if not {'edit', 'probe_metrics', 'delta', 'verdict'}.issubset(proposal):
            raise GuardrailError('trace proposals need edit, probe_metrics, delta and verdict')
        edit = proposal['edit']
        if not isinstance(edit, dict):
            raise GuardrailError(f'proposal {index} edit must be an object')
        _check_keys(edit, EDIT_KEYS, f'proposal {index} edit')
        roster = proposal.get('candidate_roster', [])
        if not isinstance(roster, list):
            raise GuardrailError(f'proposal {index} candidate_roster must be a list')
        for row in roster:
            if not isinstance(row, dict):
                raise GuardrailError(f'proposal {index} candidate roster rows must be objects')
            _check_keys(row, ROSTER_KEYS, f'proposal {index} candidate roster')
    reject_sensitive_fields(payload)
    return payload
