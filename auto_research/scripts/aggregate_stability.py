#!/usr/bin/env python3
"""Aggregate stability-search results and create one claim."""
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
        path = OUT / 'results' / f'stability_result_seed{seed}.json'
        if not path.is_file():
            raise SystemExit(f'missing stability result: {path}')
        data = json.loads(path.read_text(encoding='utf-8'))
        rows.append({'seed': seed, 'verdict': data['verdict'],
                     'evolution_holdout': data['evolution']['top5_holdout_fitness'],
                     'random_holdout': data['random']['top5_holdout_fitness'],
                     'baseline_holdout': data['baseline_existing_features']['top5_holdout_fitness'],
                     'delta_vs_baseline': data['delta_best_generated_vs_baseline']})
    mean_evo = round(sum(row['evolution_holdout'] for row in rows) / len(rows), 6)
    mean_rand = round(sum(row['random_holdout'] for row in rows) / len(rows), 6)
    mean_base = round(sum(row['baseline_holdout'] for row in rows) / len(rows), 6)
    mean_delta = round(sum(row['delta_vs_baseline'] for row in rows) / len(rows), 6)
    supported = mean_delta > 0.005
    verdict = 'supported' if supported else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-STABILITY-AGG-1', 'seeds': rows,
               'mean_evolution_holdout': mean_evo, 'mean_random_holdout': mean_rand,
               'mean_baseline_holdout': mean_base, 'mean_delta_vs_baseline': mean_delta,
               'verdict': verdict,
               'rule': 'supported only if mean best-generated delta vs baseline > +0.005',
               'score_window_read': False}
    path = OUT / 'stability_aggregate.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_stability',
            'claims': [{'claim_id': 'CLM-EXPR-STAB-AGG-001', 'type': 'experimental_result',
                        'statement': (f"稳定性正则化搜索 3-seed：holdout top5 fitness evolution 平均 {mean_evo}、"
                                      f"random {mean_rand}、现有特征 baseline {mean_base}；"
                                      f"best generated vs baseline 平均 Δ {mean_delta}；verdict={verdict}。"),
                        'evidence': ['auto_research/expr_search/stability_aggregate.json',
                                     'auto_research/expr_search/results/stability_result_seed1.json',
                                     'auto_research/expr_search/results/stability_result_seed2.json',
                                     'auto_research/expr_search/results/stability_result_seed3.json',
                                     'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                        'run_refs': ['auto_research/expr_search'],
                        'preregistration': 'auto_research/PREREGISTRATION.json',
                        'score_window_read': False}]}
    claim_path = OUT / 'claim_spec_stability_agg.json'
    claim_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'payload': payload, 'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
