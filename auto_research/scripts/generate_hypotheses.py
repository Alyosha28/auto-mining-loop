#!/usr/bin/env python3
"""Hypothesis Generator v0 (template + knowledge-graph constraints, no score-window data).

Reads the KG, picks counterintuitive claims and cross-domain operators, and emits
falsifiable hypotheses with economic/statistical mechanism, expected outcome and
falsification condition. Output: auto_research/hypotheses/HYP-*.json + index.jsonl.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KG = ROOT / 'auto_research/knowledge/graph.json'
OUT = ROOT / 'auto_research/hypotheses'
INDEX = OUT / 'index.jsonl'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_graph() -> dict:
    return json.loads(KG.read_text(encoding='utf-8'))


def counterintuitive_claims(graph: dict) -> list[dict]:
    pattern = re.compile(r'不成立|未提升|为负|负结果|无证据|mixed|weak|limited|非可加|反直觉', re.I)
    return [node for node in graph['nodes']
            if node['type'] == 'claim' and pattern.search(node['attrs'].get('statement', ''))]


def cross_domain_operators(graph: dict) -> list[dict]:
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
            rows.append({'operator': operator_id.replace('operator:', ''), 'domains': seen,
                         'experiments': sorted(exps)})
    return sorted(rows, key=lambda row: -len(row['domains']))


def build_hypotheses(graph: dict) -> list[dict]:
    counter = counterintuitive_claims(graph)
    cross = cross_domain_operators(graph)
    counter_ids = [node['id'] for node in counter]
    rank = next((row for row in cross if row['operator'] == 'rank'), None)
    operator_ids = [row['operator'] for row in cross][:3]
    base = {
        'schema': 'AUTO-RESEARCH-HYPOTHESIS-1',
        'created_at': now(),
        'score_window_read': False,
        'knowledge_nodes': [],
    }
    hypotheses = []
    hypotheses.append({
        **base,
        'hypothesis_id': 'HYP-20261021-001',
        'title': '组合算子 harness 在未见面板族上的公平回放增量',
        'statement': ('在未见过的 joint_mining_r2 面板族上，包含 rank/zscore/vol_scaled 的 evolved harness '
                      '相对 base harness 的 fair-replay 新增成员/1000 evaluated 更高（H3 的组合效应可跨面板族迁移）。'),
        'mechanism': ('固定预算下加算子会替换候选（Jaccard 0.33–0.66）；若替换后的候选多样性在未见面板上仍有优势，'
                      '则 fair-replay 新增成员应高于 base。'),
        'knowledge_nodes': counter_ids + ([f"operator:{rank['operator']}"] if rank else []) + ['panel:data_processed/joint_mining_r2_features_2016_2025_v2'],
        'expected_outcome': {'metric': 'added_per_1000', 'direction': 'evolved > base', 'threshold': '>= +1.0 per 1000'},
        'falsification': '若 evolved 的 added_per_1000 <= base，则该面板上 H3 组合效应不迁移（负结果入账）。',
        'proposed_experiment': {
            'panel': 'data_processed/joint_mining_r2_features_2016_2025_v2',
            'arms': {
                'base': 'evo_rsif/runs/rsif_e1_s7/round_01/harness/base.json',
                'evolved': 'evo_rsif/runs/rsif_e1_s7/round_03/harness/adopted.json',
            },
            'max_candidates': 40,
            'pools': '000300.SH,000905.SH,000852.SH',
            'fair_replay_baseline_version': 9,
        },
    })
    hypotheses.append({
        **base,
        'hypothesis_id': 'HYP-20261021-002',
        'title': '同域经验（price_volume memory）对同域新批次的增量',
        'statement': ('在 price_volume 面板上，加载同域经验库的 evo 链相对无经验链，在相同轮数/预算下达到更高 '
                      'adopted utility 或更少轮次到 plateau（同域记忆有效，跨域迁移的负结果不否定同域）。'),
        'mechanism': 'H2 的负结果来自跨域迁移；同域内 cue/lesson 与目标分布一致，应至少不差于无记忆。',
        'knowledge_nodes': counter_ids + ['operator:rank', 'panel:data_processed/local_quote_library_2017_2025_v1'],
        'expected_outcome': {'metric': 'utility_gain', 'direction': 'memory >= no_memory', 'threshold': '>= 0'},
        'falsification': '若同域 memory 的 utility_gain 显著低于 no_memory，则记忆机制本身有害（负结果入账）。',
        'proposed_experiment': {
            'panel': 'data_processed/local_quote_library_2017_2025_v1',
            'arms': {'no_memory': 'e2a', 'memory': 'e2b'},
            'max_candidates': 24, 'pools': '000300.SH,000905.SH,000852.SH',
            'fair_replay_baseline_version': 9,
        },
    })
    hypotheses.append({
        **base,
        'hypothesis_id': 'HYP-20261021-003',
        'title': '跨域算子组合的 OOD 优势是否来自候选多样性（而非单算子）',
        'statement': ('在 intraday/fundamental 两个 OOD domain 上，把 evolved harness 的算子集替换为"仅保留 base 算子 + 三个新算子"'
                      '的子集后，若三算子组合仍优于任一单算子，则 OOD 增益来自组合/多样性；否则来自特定算子。'),
        'mechanism': 'H3b 归因显示单算子为负、三算子为正；若该模式在更多 seed 上复现，则可作为"多样性效应"的机制证据。',
        'knowledge_nodes': counter_ids + ['operator:rank', 'operator:zscore', 'operator:vol_scaled'],
        'expected_outcome': {'metric': 'ood_utility_delta', 'direction': 'all_three > single_operator', 'threshold': '>= +0.01'},
        'falsification': '若任一单算子 >= 三算子组合，则"多样性/组合"解释不成立。',
        'proposed_experiment': {
            'panel': 'data_processed/joint_mining_features_20260924_r1',
            'arms': {'base': 'base', 'rank': 'rank', 'all': 'all'},
            'max_candidates': 24, 'pools': '', 'fair_replay_baseline_version': None,
        },
    })
    return hypotheses


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    graph = load_graph()
    hypotheses = build_hypotheses(graph)
    existing = set()
    if INDEX.is_file():
        for line in INDEX.read_text(encoding='utf-8').splitlines():
            if line.strip():
                existing.add(json.loads(line)['hypothesis_id'])
    new_rows = []
    for hypothesis in hypotheses:
        path = OUT / f"{hypothesis['hypothesis_id']}.json"
        if hypothesis['hypothesis_id'] in existing and path.is_file():
            continue
        path.write_text(json.dumps(hypothesis, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        new_rows.append({'hypothesis_id': hypothesis['hypothesis_id'], 'title': hypothesis['title'],
                         'path': str(path.relative_to(ROOT)), 'created_at': hypothesis['created_at'],
                         'score_window_read': False})
    if new_rows:
        with INDEX.open('a', encoding='utf-8') as stream:
            for row in new_rows:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(json.dumps({'generated': [row['hypothesis_id'] for row in new_rows],
                      'total': len(existing) + len(new_rows),
                      'index': str(INDEX.relative_to(ROOT))}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
