#!/usr/bin/env python3
"""Type-scenario retrieval over the auto-research knowledge graph (v0).

Modes (aligned with PARNESS's scenario-typed retrieval):
  similar          : nodes sharing neighbours/tokens with a target node
  contradictory    : claim pairs connected by `contradicts` edges
  cross_domain     : operators/lessons explored across >=2 domains
  counterintuitive : claims whose results are negative/mixed/surprising
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KG = ROOT / 'auto_research/knowledge/graph.json'
DEMO = ROOT / 'auto_research/knowledge/retrieval_demo.json'


def load_graph() -> dict:
    return json.loads(KG.read_text(encoding='utf-8'))


def node_index(graph: dict) -> dict:
    return {node['id']: node for node in graph['nodes']}


def neighbours(graph: dict) -> dict:
    index: dict[str, set[str]] = {}
    for edge in graph['edges']:
        index.setdefault(edge['source'], set()).add(edge['target'])
        index.setdefault(edge['target'], set()).add(edge['source'])
    return index


def tokens(text: str) -> set[str]:
    text = text.lower()
    english = set(re.findall(r'[a-z0-9_]+', text))
    chinese = {text[i:i + 2] for i in range(len(text) - 1) if '\u4e00' <= text[i] <= '\u9fff'}
    return english | chinese


def similar(graph: dict, node_id: str, top_k: int = 5) -> list[dict]:
    index = node_index(graph)
    neighbour_index = neighbours(graph)
    if node_id not in index:
        raise SystemExit(f'unknown node: {node_id}')
    base = index[node_id]
    base_tokens = tokens(json.dumps(base['attrs'], ensure_ascii=False))
    base_neighbours = neighbour_index.get(node_id, set())
    scored = []
    for other_id, other in index.items():
        if other_id == node_id:
            continue
        other_tokens = tokens(json.dumps(other['attrs'], ensure_ascii=False))
        token_score = len(base_tokens & other_tokens) / max(1, len(base_tokens | other_tokens))
        neighbour_score = len(base_neighbours & neighbour_index.get(other_id, set())) / max(1, len(base_neighbours | (neighbour_index.get(other_id) or {1})))
        score = 0.6 * neighbour_score + 0.4 * token_score
        if score > 0:
            scored.append({'node': other_id, 'type': other['type'], 'score': round(score, 4),
                           'shared_neighbours': sorted(base_neighbours & neighbour_index.get(other_id, set()))[:5]})
    scored.sort(key=lambda row: -row['score'])
    return scored[:top_k]


def contradictory(graph: dict) -> list[dict]:
    index = node_index(graph)
    results = []
    for edge in graph['edges']:
        if edge['type'] != 'contradicts':
            continue
        left = index.get(edge['source'], {})
        right = index.get(edge['target'], {})
        results.append({'left': edge['source'], 'right': edge['target'],
                        'reason': edge['attrs'].get('reason'),
                        'left_statement': left.get('attrs', {}).get('statement'),
                        'right_statement': right.get('attrs', {}).get('statement')})
    # deduplicate symmetric pairs
    seen, unique = set(), []
    for row in results:
        key = tuple(sorted((row['left'], row['right'])))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique


def cross_domain(graph: dict) -> list[dict]:
    index = node_index(graph)
    operator_experiments: dict[str, set[str]] = {}
    experiment_domains: dict[str, str] = {}
    for edge in graph['edges']:
        if edge['type'] == 'proposed_operator':
            operator_experiments.setdefault(edge['target'], set()).add(edge['source'])
    for experiment_id, node in index.items():
        if node['type'] == 'experiment':
            domain = node['attrs'].get('domain')
            if domain:
                experiment_domains[experiment_id] = domain
    results = []
    for operator_id, experiments in operator_experiments.items():
        domains = sorted({experiment_domains[exp] for exp in experiments if exp in experiment_domains})
        if len(domains) >= 2:
            results.append({'operator': operator_id, 'domains': domains,
                            'experiments': sorted(experiments)})
    return sorted(results, key=lambda row: -len(row['domains']))


def counterintuitive(graph: dict) -> list[dict]:
    index = node_index(graph)
    pattern = re.compile(r'不成立|未提升|为负|负结果|无证据|mixed|weak|limited|反直觉|非可加|没有.*证据', re.I)
    results = []
    for node_id, node in index.items():
        if node['type'] != 'claim':
            continue
        statement = node['attrs'].get('statement', '')
        if pattern.search(statement):
            results.append({'claim': node_id, 'verdict': node['attrs'].get('verdict'),
                            'statement': statement,
                            'score_window_read': node['attrs'].get('score_window_read')})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', required=True,
                        choices=['similar', 'contradictory', 'cross_domain', 'counterintuitive'])
    parser.add_argument('--node', default=None)
    parser.add_argument('--top', type=int, default=5)
    args = parser.parse_args()
    graph = load_graph()
    if args.mode == 'similar':
        node = args.node or 'claim:CLM-20261021-0003'
        result = {'mode': 'similar', 'target': node, 'results': similar(graph, node, args.top)}
    elif args.mode == 'contradictory':
        result = {'mode': 'contradictory', 'results': contradictory(graph)}
    elif args.mode == 'cross_domain':
        result = {'mode': 'cross_domain', 'results': cross_domain(graph)}
    else:
        result = {'mode': 'counterintuitive', 'results': counterintuitive(graph)}
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
