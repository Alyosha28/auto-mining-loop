#!/usr/bin/env python3
"""Agent roles: proposer, critic (LLM prompts + parsing)."""
from __future__ import annotations

import json

import re

from . import knowledge, llm, sanitize
from .expr_grammar import parse, validate

PROPOSAL_SCHEMA = {'type': 'object', 'properties': {'hypotheses': {'type': 'array', 'minItems': 2, 'items': {
    'type': 'object', 'properties': {'expression': {'type': 'string'}, 'rationale': {'type': 'string'},
                                     'expected_ic': {'type': 'number'}, 'falsification': {'type': 'string'}},
    'required': ['expression', 'rationale', 'expected_ic', 'falsification'], 'additionalProperties': False}}},
    'required': ['hypotheses'], 'additionalProperties': False}

PROPOSER_SYSTEM = """You are a quantitative researcher proposing testable factor hypotheses.
Rules:
- Output ONLY a JSON array of objects with keys: expression, rationale, expected_ic, falsification.
- expression must use the restricted grammar: leaf = feature name; unary ops = rank/zscore/winsorize/vol_scaled/tanh/sign/residual_size/delta4/delta13/delta_w/ema5/lag5/neg; binary ops = ratio/diff/rank_minus/product. Examples: rank(delta4(pv_gk_vol_60)), ratio(rank(volatility_60),tanh(turnover_cv_20)), rank_minus(amount_ratio_5_60,volume_shock_20).
- rationale: one sentence of economic mechanism; expected_ic: a small number like 0.02; falsification: one sentence on what would refute it.
- Do NOT invent features outside the provided list. Do not propose duplicates of existing examples. Keep internal reasoning brief (under ~1000 tokens), then output the JSON array."""

CRITIC_SYSTEM = """You are a skeptical reviewer. Think briefly (under ~500 tokens). For each proposed hypothesis decide accept/reject.
Reject if: expression uses unknown feature/op; mechanism conflicts with known negative results; duplicate of another proposal; expected effect implausibly large; no falsification test.
Output ONLY a JSON array with keys: index, decision (accept/reject), reason (short).
Be strict: at most 60% of proposals should be accepted. Do not overthink; answer immediately after a short review."""


def proposer_prompt(brief: str, features: list[str], n: int, mapping: dict[str, str] | None = None) -> list[dict]:
    feature_names = ', '.join(mapping[feature] for feature in sorted(features)) if mapping else ', '.join(features)
    user = f"""Research brief: {brief}

Feature pool (use exact names): {feature_names}

{knowledge.compose(brief)}

Propose exactly {n} diverse hypotheses that could improve the dev-window fitness (IC/ICIR/turnover) beyond simple single-factor baselines. Return ONLY the JSON array."""
    system = PROPOSER_SYSTEM
    if mapping:
        user = sanitize.to_alias(user, mapping)
        user = re.sub(r'CLM-[A-Za-z0-9-]+', 'CLAIM-REDACTED', user)
        user = sanitize.scrub(user)
        system = sanitize.scrub(sanitize.to_alias(PROPOSER_SYSTEM, mapping))
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


def propose(brief: str, features: list[str], n: int = 6, temperature: float = 0.4,
            model: str | None = None) -> dict:
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    messages = proposer_prompt(brief, features, n, mapping)
    response_format = None
    if llm.PROVIDER == 'local':
        response_format = {'type': 'json_schema', 'json_schema': {'name': 'proposal', 'strict': True,
                                                                   'schema': PROPOSAL_SCHEMA}}
    try:
        raw = llm.chat(messages, temperature=temperature, model=model, max_tokens=3600,
                      response_format=response_format)
    except Exception as first_error:  # noqa: BLE001
        short = [{'role': 'system', 'content': PROPOSER_SYSTEM},
                 {'role': 'user', 'content': f'Brief: {brief[:300]}\nFeatures: ' +
                  (', '.join(mapping.values()) if mapping else ', '.join(features)) +
                  f'\nPropose exactly {n} hypotheses. Return ONLY the JSON array.'}]
        try:
            raw = llm.chat(short, temperature=temperature, model=model, max_tokens=2400)
        except Exception as second_error:  # noqa: BLE001
            return {'proposals': [], 'error': f'{first_error}; fallback: {second_error}'[:800], 'raw': ''}
    try:
        extracted = llm.extract_all_json(raw)
        if not extracted:
            raise ValueError('no JSON found')
        items = []
        for value in extracted:
            if isinstance(value, list):
                items.extend(value)
            elif isinstance(value, dict):
                inner = value.get('hypotheses') or value.get('proposals') or value.get('ideas')
                items.extend(inner if isinstance(inner, list) else [value])
    except ValueError as exc:
        return {'proposals': [], 'error': str(exc), 'raw': raw[:1500]}
    proposals = []
    for item in items[:n]:
        item = dict(item)
        expression = str(item.get('expression', '')).strip()
        if mapping:
            expression = sanitize.from_alias(expression, mapping)
        record = {'expression': expression, 'rationale': item.get('rationale'),
                  'expected_ic': item.get('expected_ic'), 'falsification': item.get('falsification')}
        try:
            node = parse(expression, set(features))
            validate(node, set(features))
            record['status'] = 'PARSED'
            record['node'] = node
        except Exception as exc:  # noqa: BLE001
            record['status'] = 'REJECTED_PARSE'
            record['error'] = str(exc)
        proposals.append(record)
    return {'proposals': proposals, 'raw': raw}


def critique(brief: str, proposals: list[dict], model: str | None = None) -> dict:
    compact = [{'index': index, 'expression': item['expression'], 'rationale': item.get('rationale'),
                'falsification': item.get('falsification')} for index, item in enumerate(proposals)]
    if llm.PROVIDER != 'local':
        from .sanitize import to_alias
        import json as _json
        leaves = set()
        def _walk(node):
            if node.get('kind') == 'leaf':
                leaves.add(node['feature'])
            elif node.get('kind') == 'unary':
                _walk(node['arg'])
            elif node.get('kind') == 'binary':
                _walk(node['left']); _walk(node['right'])
        for item in proposals:
            _walk(item['node'])
        alias_map = sanitize.alias_map(sorted(leaves))
        proposals_text = sanitize.scrub(to_alias(_json.dumps(compact, ensure_ascii=False), alias_map))
    else:
        proposals_text = json.dumps(compact, ensure_ascii=False)
    user = ('Research brief: ' + brief + '\n\nProposals:\n' + proposals_text +
            '\n\nReturn ONLY the JSON array of decisions.')
    raw = llm.chat([{'role': 'system', 'content': CRITIC_SYSTEM}, {'role': 'user', 'content': user}],
                   temperature=0.0, model=model, max_tokens=8000)
    try:
        extracted = llm.extract_all_json(raw)
        decisions = []
        for value in extracted:
            if isinstance(value, list):
                decisions.extend(value)
            elif isinstance(value, dict):
                decisions.append(value)
    except ValueError as exc:
        return {'decisions': [], 'error': str(exc), 'raw': raw[:1500]}
    if isinstance(decisions, dict):
        decisions = [decisions]
    if isinstance(decisions, list):
        decisions = [item for item in decisions if isinstance(item, dict)]
    else:
        decisions = []
    return {'decisions': decisions, 'raw': raw}
