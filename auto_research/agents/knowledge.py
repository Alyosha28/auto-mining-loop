#!/usr/bin/env python3
"""Typed knowledge slices for agent prompts (from the project KG and claim ledger)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOTP = ROOT / 'auto_research'
GRAPH = ROOTP / 'knowledge/graph.json'
CLAIMS = ROOTP / 'ledger/claims.jsonl'


def load_graph() -> dict:
    return json.loads(GRAPH.read_text(encoding='utf-8'))


def load_claims() -> list[dict]:
    if not CLAIMS.is_file():
        return []
    return [json.loads(line) for line in CLAIMS.read_text(encoding='utf-8').splitlines() if line.strip()]


def counterintuitive(limit: int = 4) -> list[str]:
    pattern = re.compile(r'不成立|未提升|为负|负结果|无证据|mixed|weak|non-additive|非可加|不支持', re.I)
    out = []
    for claim in reversed(load_claims()):
        if pattern.search(claim.get('statement', '')):
            out.append(f"[{claim['claim_id']}] {claim['statement'][:200]}")
        if len(out) >= limit:
            break
    return out


def cross_domain_operators(limit: int = 5) -> list[str]:
    graph = load_graph()
    index = {node['id']: node for node in graph['nodes']}
    experiments: dict[str, set[str]] = {}
    domains: dict[str, str] = {}
    for edge in graph['edges']:
        if edge['type'] == 'proposed_operator':
            experiments.setdefault(edge['target'], set()).add(edge['source'])
    for node_id, node in index.items():
        if node['type'] == 'experiment' and node['attrs'].get('domain'):
            domains[node_id] = node['attrs']['domain']
    rows = []
    for operator_id, exps in experiments.items():
        seen = sorted({domains[exp] for exp in exps if exp in domains})
        if len(seen) >= 2:
            rows.append(f"{operator_id.replace('operator:', '')}: {', '.join(seen)}")
    return rows[:limit]


def similar_claims(query: str, limit: int = 3) -> list[str]:
    tokens = set(re.findall(r'[a-z0-9_]+', query.lower()))
    scored = []
    for claim in load_claims():
        statement = claim.get('statement', '')
        overlap = len(tokens & set(re.findall(r'[a-z0-9_]+', statement.lower())))
        scored.append((overlap, claim))
    scored.sort(key=lambda item: -item[0])
    return [f"[{claim['claim_id']}] {claim['statement'][:200]}" for score, claim in scored[:limit] if score > 0] or \
           [f"[{claim['claim_id']}] {claim['statement'][:200]}" for _, claim in scored[:limit]]


def compose(brief: str) -> str:
    brief = brief[:600]
    parts = ['## Known negative / mixed results', *[f'- {row[:180]}' for row in counterintuitive(3)],
             '## Operators that appeared across domains (but transfer was non-additive)', *[f'- {row}' for row in cross_domain_operators(3)],
             '## Claims related to this brief', *[f'- {row[:180]}' for row in similar_claims(brief, 2)]]
    return '\n'.join(parts)


if __name__ == '__main__':
    print(compose('find composite factors for cross-sectional returns'))
