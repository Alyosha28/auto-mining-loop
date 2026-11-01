#!/usr/bin/env python3
"""Robustness aggregation for combination search: extra seeds + time-split holdouts."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/expr_search'
RESULTS = OUT / 'results'
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger
import review_claims


def collect(prefix: str, seeds: list[int], split_label: str) -> dict:
    rows = []
    for seed in seeds:
        path = RESULTS / f'{prefix}result_seed{seed}.json'
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        rows.append({'seed': seed, 'evolution': data['evolution_holdout']['top5_holdout_fitness'],
                     'random': data['random_holdout']['top5_holdout_fitness'],
                     'baseline': data['baseline_holdout']['top5_holdout_fitness'],
                     'delta': data['delta_best_generated_vs_baseline'],
                     'evolution_better_than_random': data['evolution_holdout']['top5_holdout_fitness'] > data['random_holdout']['top5_holdout_fitness']})
    if not rows:
        return {'split': split_label, 'rows': [], 'available': False}
    return {'split': split_label, 'available': True, 'rows': rows,
            'mean_evolution': round(sum(r['evolution'] for r in rows) / len(rows), 6),
            'mean_random': round(sum(r['random'] for r in rows) / len(rows), 6),
            'mean_baseline': round(sum(r['baseline'] for r in rows) / len(rows), 6),
            'mean_delta': round(sum(r['delta'] for r in rows) / len(rows), 6),
            'positive_seeds': sum(1 for r in rows if r['delta'] > 0.005),
            'evolution_better_seeds': sum(1 for r in rows if r['evolution_better_than_random'])}


def main() -> None:
    splits = [collect('combo_', [1, 2, 3, 4, 5, 6], 'frac_70_30'),
              collect('combo_20200101_', [1, 2, 3], 'holdout_2020'),
              collect('combo_20190101_', [1, 2, 3], 'holdout_2019_2020')]
    available = [entry for entry in splits if entry['available']]
    splits_positive = sum(1 for entry in available if entry['mean_delta'] > 0.005)
    evolution_better_total = sum(entry['evolution_better_seeds'] for entry in available)
    evolution_total = sum(len(entry['rows']) for entry in available)
    supported = bool(splits_positive >= 2)
    verdict = 'robust' if supported else 'not_robust'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-COMBO-ROBUSTNESS-1', 'splits': splits,
               'splits_positive': splits_positive, 'splits_available': len(available),
               'evolution_better_seeds': evolution_better_total, 'evolution_total_seeds': evolution_total,
               'verdict': verdict,
               'rule': 'robust if >=2/3 holdout splits have mean delta vs baseline > +0.005',
               'score_window_read': False}
    path = OUT / 'combo_robustness.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    evidence = ['auto_research/expr_search/combo_robustness.json',
                'auto_research/expr_search/PREREG_EXPR_SEARCH.json']
    for entry in available:
        prefix = {'frac_70_30': 'combo_', 'holdout_2020': 'combo_20200101_', 'holdout_2019_2020': 'combo_20190101_'}[entry['split']]
        for row in entry['rows']:
            evidence.append(f'auto_research/expr_search/results/{prefix}result_seed{row["seed"]}.json')
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_combo',
            'claims': [{'claim_id': 'CLM-EXPR-COMBO-ROBUST-001', 'type': 'experimental_result',
                        'statement': (f"组合搜索稳健性：{len(available)} 个 holdout 切分中 {splits_positive} 个 mean Δ > +0.005；"
                                      f"各切分 mean Δ：{[(entry['split'], entry['mean_delta']) for entry in available]}；"
                                      f"evolution 优于 random：{evolution_better_total}/{evolution_total} seed；verdict={verdict}。"),
                        'evidence': evidence, 'run_refs': ['auto_research/expr_search'],
                        'preregistration': 'auto_research/PREREGISTRATION.json',
                        'score_window_read': False}]}
    claim_path = OUT / 'claim_spec_combo_robustness.json'
    claim_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'splits': [{k: entry[k] for k in ('split', 'mean_delta', 'positive_seeds') if k in entry}
                                 for entry in available],
                      'splits_positive': splits_positive,
                      'evolution_better': f'{evolution_better_total}/{evolution_total}',
                      'verdict': verdict,
                      'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
