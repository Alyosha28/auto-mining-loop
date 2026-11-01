#!/usr/bin/env python3
"""Analyze an agent evaluation matrix and (optionally) emit a pruned claim."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'auto_research/agents/experiments'
OUT = ROOT / 'auto_research/agents'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--matrix', required=True)
    parser.add_argument('--claim', action='store_true')
    parser.add_argument('--claim-id', default='CLM-AGENT-MATRIX-RESULT-001')
    args = parser.parse_args()
    path = ROOT / args.matrix if not Path(args.matrix).is_absolute() else Path(args.matrix)
    matrix = json.loads(path.read_text(encoding='utf-8'))
    model = matrix.get('model')
    rows = matrix['matrix']
    baseline = matrix['summary']['baseline']
    random_mean = baseline['random_mean']
    evolution_mean = baseline['evolution_mean']
    analysis = {'schema': 'AUTO-RESEARCH-AGENT-ANALYSIS-1', 'created_at': now(),
                'matrix': str(path.relative_to(ROOT)), 'model': model,
                'baseline_random_mean': random_mean, 'baseline_evolution_mean': evolution_mean,
                'arms': {}}
    for arm, arm_rows in rows.items():
        if arm == 'baseline':
            continue
        values = [row['holdout_top5'] for row in arm_rows if row.get('holdout_top5') is not None]
        if not values:
            continue
        deltas = [row['holdout_top5'] - evolution_mean for row in arm_rows if row.get('holdout_top5') is not None]
        analysis['arms'][arm] = {
            'n': len(values),
            'mean': round(float(np.mean(values)), 6),
            'median': round(float(np.median(values)), 6),
            'min': round(min(values), 6),
            'max': round(max(values), 6),
            'delta_vs_evolution_mean': round(float(np.mean(deltas)), 6),
            'delta_vs_random_mean': round(float(np.mean(values) - random_mean), 6),
            'elo_spearman_mean': matrix['summary'][arm].get('elo_spearman_mean'),
            'valid_mean': round(float(np.mean([row['n_valid'] for row in arm_rows if row.get('n_valid')])), 2)}
    # per-seed win comparison for full arm vs baseline arms
    full = rows.get('agentic_full') or []
    wins = []
    for row in full:
        seed = row['seed']
        baseline_row = next((item for item in rows.get('baseline', []) if item['seed'] == seed), None)
        if baseline_row and row.get('holdout_top5') is not None:
            wins.append({'seed': seed, 'holdout': row['holdout_top5'],
                         'random': baseline_row['random'], 'evolution': baseline_row['evolution'],
                         'beat_random': row['holdout_top5'] > baseline_row['random'],
                         'beat_evolution': row['holdout_top5'] > baseline_row['evolution']})
    analysis['agentic_full_per_seed'] = wins
    OUT.mkdir(parents=True, exist_ok=True)
    analysis_path = OUT / f'analysis_{path.stem}.json'
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(analysis, ensure_ascii=False, indent=1))
    if args.claim and model:
        sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
        import claim_ledger
        import review_claims
        full_arm = analysis['arms'].get('agentic_full', {})
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'agentic_matrix',
                'claims': [{'claim_id': args.claim_id, 'type': 'experimental_result',
                            'statement': (f"agentic matrix（{model}，budget {matrix['budget']}，seeds {matrix['seeds']}）："
                                          f"full arm holdout top5 均值 {full_arm.get('mean')}（vs random {random_mean}、"
                                          f"evolution {evolution_mean}）；逐 seed 记录 {[(row['seed'], row['beat_random'], row['beat_evolution']) for row in wins]}；"
                                          f"消融 arms：{[(arm, value.get('mean')) for arm, value in analysis['arms'].items() if arm != 'agentic_full']}。"),
                            'evidence': [str(analysis_path.relative_to(ROOT)), str(path.relative_to(ROOT)),
                                         'auto_research/agents/PLAN_CN.md'],
                            'run_refs': ['auto_research/agents'], 'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        claim_path = OUT / f'claim_spec_{args.claim_id}.json'
        claim_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(claim_path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
