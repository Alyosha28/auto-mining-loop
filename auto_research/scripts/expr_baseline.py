#!/usr/bin/env python3
"""Existing panel features as the baseline for generated expressions (dev holdout)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import expr_search as es  # noqa: E402

OUT = ROOT / 'auto_research/expr_search'
BASELINE = OUT / 'baseline_result.json'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--holdout-fraction', type=float, default=0.3)
    parser.add_argument('--claim', action='store_true')
    args = parser.parse_args()
    panel = es.build_panel()
    feature_pool = es.features(panel)
    all_dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * (1.0 - args.holdout_fraction))))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    rows = []
    for feature in feature_pool:
        record = es.evaluate_expr({'kind': 'leaf', 'feature': feature}, panel, holdout_dates)
        if record:
            rows.append(record)
    rows.sort(key=lambda row: -row['fitness'])
    top = rows[:10]
    stats = {'n_features': len(rows),
             'top1_fitness': top[0]['fitness'] if top else None,
             'top5_mean_fitness': round(float(np.mean([row['fitness'] for row in top[:5]])), 6) if top else None,
             'pass_count': sum(1 for row in rows if row['passed']),
             'top5': [{'feature': row['formula'], 'fitness': row['fitness'], 'mean_ic': row['mean_ic'],
                       'icir': row['icir'], 'turnover': row['turnover_proxy']} for row in top[:5]]}
    # generated expressions: per-seed holdout top5 means from expr search
    generated = []
    for seed in (1, 2, 3):
        path = OUT / 'results' / f'expr_search_result_seed{seed}.json'
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding='utf-8'))
        for mode, block in (('random', data['random_holdout']), ('evolution', data['evolution_holdout'])):
            generated.append({'seed': seed, 'mode': mode, 'holdout_top5_fitness': block['top5_mean_fitness']})
    gen_best = max((row['holdout_top5_fitness'] for row in generated
                    if row['holdout_top5_fitness'] is not None), default=None)
    baseline_top5 = stats['top5_mean_fitness']
    delta = round(gen_best - baseline_top5, 6) if gen_best is not None and baseline_top5 is not None else None
    verdict = 'generated_better' if (delta is not None and delta > 0) else 'baseline_better_or_equal'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-BASELINE-1',
               'search_dates': [str(search_dates[0].date()), str(search_dates[-1].date())],
               'holdout_dates': [str(holdout_dates[0].date()), str(holdout_dates[-1].date())],
               'baseline_existing_features': stats,
               'generated_best_holdout_top5': gen_best,
               'generated_per_seed': generated,
               'delta_generated_minus_baseline': delta,
               'verdict': verdict,
               'score_window_read': False}
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'baseline_top5': baseline_top5, 'generated_best': gen_best,
                      'delta': delta, 'verdict': verdict}, ensure_ascii=False, indent=1))
    if args.claim:
        import claim_ledger
        import review_claims
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_search',
                'claims': [{'claim_id': 'CLM-EXPR-SEARCH-BASELINE-001', 'type': 'experimental_result',
                            'statement': (f"生成表达式 vs 现有面板特征 baseline（dev holdout {payload['holdout_dates'][0]}.."
                                          f"{payload['holdout_dates'][1]}）：baseline top5 holdout fitness = {baseline_top5}；"
                                          f"生成表达式最好 holdout top5 = {gen_best}；Δ = {delta}；verdict={verdict}。"),
                            'evidence': ['auto_research/expr_search/baseline_result.json',
                                         'auto_research/expr_search/results/expr_search_result_seed1.json',
                                         'auto_research/expr_search/results/expr_search_result_seed2.json',
                                         'auto_research/expr_search/results/expr_search_result_seed3.json',
                                         'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                            'run_refs': ['auto_research/expr_search'],
                            'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        path = OUT / 'claim_spec_baseline.json'
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
