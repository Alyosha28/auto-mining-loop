#!/usr/bin/env python3
"""Aggregate per-seed expression-search A/B results into one claim."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/expr_search'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger
import review_claims


def main() -> None:
    rows = []
    for seed in (1, 2, 3):
        path = OUT / 'results' / f'expr_search_result_seed{seed}.json'
        if not path.is_file():
            raise SystemExit(f'missing per-seed result: {path}')
        data = json.loads(path.read_text(encoding='utf-8'))
        rows.append({'seed': seed, 'verdict': data['verdict'],
                     'in_sample_delta': data['delta_top5_fitness'],
                     'holdout_delta': data['delta_holdout_fitness'],
                     'random_holdout': data['random_holdout']['top5_mean_fitness'],
                     'evolution_holdout': data['evolution_holdout']['top5_mean_fitness'],
                     'random_pass': data['random']['pass_count'],
                     'evolution_pass': data['evolution']['pass_count']})
    mean_holdout = round(sum(row['holdout_delta'] for row in rows) / len(rows), 6)
    mean_in_sample = round(sum(row['in_sample_delta'] for row in rows) / len(rows), 6)
    positive = sum(1 for row in rows if (row['holdout_delta'] or 0) >= 0.005)
    pass_ge = sum(1 for row in rows if row['evolution_pass'] >= row['random_pass'])
    supported = bool(positive >= 2 and mean_holdout >= 0.005)
    verdict = 'supported' if supported else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-SEARCH-AGGREGATE-1',
               'seeds': rows, 'mean_in_sample_delta': mean_in_sample,
               'mean_holdout_delta': mean_holdout,
               'holdout_positive_seeds': positive,
               'pass_count_not_lower_seeds': pass_ge,
               'verdict': verdict,
               'rule': 'supported only if >=2/3 seeds have holdout delta >= 0.005 and mean holdout delta >= 0.005',
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / 'aggregate_result.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_search',
            'claims': [{'claim_id': 'CLM-EXPR-SEARCH-AGG-001', 'type': 'experimental_result',
                        'statement': (f"表达式搜索 3-seed A/B（holdout 协议）：holdout Δ 分别为 "
                                      f"{[row['holdout_delta'] for row in rows]}，平均 Δ {mean_holdout}，"
                                      f"仅 {positive}/3 seed 为正（>=0.005）；pass_count 不低者 {pass_ge}/3；"
                                      f"in-sample 平均 Δ {mean_in_sample}；verdict={verdict}。"),
                        'evidence': ['auto_research/expr_search/aggregate_result.json',
                                     'auto_research/expr_search/results/expr_search_result_seed1.json',
                                     'auto_research/expr_search/results/expr_search_result_seed2.json',
                                     'auto_research/expr_search/results/expr_search_result_seed3.json',
                                     'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                        'run_refs': ['auto_research/expr_search'],
                        'preregistration': 'auto_research/PREREGISTRATION.json',
                        'score_window_read': False}]}
    claim_path = OUT / 'claim_spec_aggregate.json'
    claim_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'payload': payload, 'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
