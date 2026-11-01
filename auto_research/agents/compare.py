#!/usr/bin/env python3
"""Compare agentic loop vs random / evolution baselines at equal evaluation budget."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402

OUT = ROOT / 'auto_research/agents/experiments'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3 or len(x) != len(y):
        return None
    def ranks(values):
        order = np.argsort(np.argsort(values))
        return order.astype(float)
    rx, ry = ranks(np.asarray(x)), ranks(np.asarray(y))
    if rx.std() == 0 or ry.std() == 0:
        return None
    return round(float(np.corrcoef(rx, ry)[0, 1]), 4)


def evaluate_top(rows: list[dict], panel, holdout_dates, top_k: int = 5, critic_first: bool = False) -> dict:
    for row in rows:
        row.setdefault('search_fitness', row.get('fitness'))
        row.setdefault('critic_accept', str(row.get('critic', '')).startswith('accept'))
    if critic_first:
        ordered = sorted(rows, key=lambda row: (row['critic_accept'], row.get('search_fitness') or -9), reverse=True)[:max(top_k, 8)]
    else:
        ordered = sorted(rows, key=lambda row: -(row.get('search_fitness') or -9))[:max(top_k, 8)]
    holdout = []
    for row in ordered:
        node = row.get('node') or es.expr_lookup(row['formula'])
        if node is None:
            continue
        record = es.evaluate_expr(node, panel, holdout_dates)
        if record:
            holdout.append({'formula': row['formula'], 'fitness': record['fitness'],
                            'ic': record['mean_ic'], 'search_fitness': row['search_fitness']})
    holdout.sort(key=lambda item: -item['search_fitness'])
    top = holdout[:top_k]
    return {'top5_holdout_fitness': round(float(np.mean([row['fitness'] for row in top])), 6) if top else None,
            'top5_holdout_ic': round(float(np.mean([row['ic'] for row in top])), 6) if top else None,
            'details': holdout[:top_k]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=None, help='LLM model id; omit to skip the agentic arm')
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--generations', type=int, default=2)
    parser.add_argument('--per-generation', type=int, default=6)
    parser.add_argument('--claim', action='store_true')
    parser.add_argument('--proposer', choices=['llm', 'mock'], default='llm')
    parser.add_argument('--no-critic', action='store_true')
    parser.add_argument('--no-reflection', action='store_true')
    parser.add_argument('--no-elo', action='store_true')
    parser.add_argument('--tag', default='')
    parser.add_argument('--require-accepted', action='store_true')
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    all_dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * 0.7)))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    results = {}
    # random arm
    random_rows = es.run_search('random', args.seed, args.budget, panel, features, search_dates)
    results['random'] = {'n_valid': len(random_rows), **evaluate_top(random_rows, panel, holdout_dates)}
    # evolution arm
    evolution_rows = es.run_search('evolution', args.seed + 1000, args.budget, panel, features, search_dates)
    results['evolution'] = {'n_valid': len(evolution_rows), **evaluate_top(evolution_rows, panel, holdout_dates)}
    # agentic arm
    if args.model:
        command = [sys.executable, str(ROOT / 'auto_research/agents/orchestrator.py'),
                   '--model', args.model, '--generations', str(args.generations),
                   '--per-generation', str(args.per_generation), '--budget', str(args.budget),
                   '--seed', str(args.seed), '--tag', f'compare_{args.seed}',
                   '--proposer', args.proposer]
        if args.no_critic: command.append('--no-critic')
        if args.no_reflection: command.append('--no-reflection')
        if args.no_elo: command.append('--no-elo')
        completed = subprocess.run(command, capture_output=True, text=True, timeout=3600)
        run_dirs = sorted((ROOT / 'auto_research/agents/runs').glob(f'agentic_compare_{args.seed}_*'))
        if completed.returncode != 0 or not run_dirs:
            raise SystemExit(f'agentic arm failed: {completed.stderr[-800:]}')
        run_dir = run_dirs[-1]
        candidates = json.loads((run_dir / 'candidates.json').read_text(encoding='utf-8'))
        n_evaluated = len(candidates)
        if args.require_accepted:
            # keep all evaluated candidates; critic is a ranking prior, not a hard filter
            for row in candidates:
                row['critic_accept'] = str(row.get('critic', '')).startswith('accept')
        summary = json.loads((run_dir / 'summary.json').read_text(encoding='utf-8'))
        results['agentic'] = {'n_valid': n_evaluated, 'n_accepted': len(candidates),
                              'elo_fitness_spearman': spearman([row['elo'] for row in candidates],
                                                               [row['search_fitness'] for row in candidates]),
                              'run': str(run_dir.relative_to(ROOT)),
                              **evaluate_top(candidates, panel, holdout_dates, critic_first=args.require_accepted)}
    provider = os.environ.get('AUTO_RESEARCH_LLM_PROVIDER', 'local')
    payload = {'schema': 'AUTO-RESEARCH-AGENT-COMPARE-1', 'created_at': now(),
               'provider': provider, 'model': args.model, 'budget': args.budget, 'seed': args.seed,
               'generations': args.generations, 'results': results,
               'note': 'single seed; compare models only when each has its own claims; do not mix seeds/models',
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    tag = args.tag or (args.model.replace('/', '_') if args.model else 'baselines')
    path = OUT / f'compare_{tag}_seed{args.seed}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: {k: v for k, v in value.items() if k in ('n_valid', 'top5_holdout_fitness',
                                                                   'top5_holdout_ic', 'elo_fitness_spearman')}
                      for key, value in results.items()}, ensure_ascii=False, indent=1))
    if args.claim and args.model:
        sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
        import claim_ledger
        import review_claims
        agentic = results['agentic']
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'agentic_compare',
                'claims': [{'claim_id': f'CLM-AGENT-{args.seed:03d}', 'type': 'experimental_result',
                            'statement': (f"agentic loop（{args.model}，budget {args.budget}，seed {args.seed}）"
                                          f"holdout top5 fitness = {agentic['top5_holdout_fitness']}，"
                                          f"random = {results['random']['top5_holdout_fitness']}，"
                                          f"evolution = {results['evolution']['top5_holdout_fitness']}；"
                                          f"Elo↔fitness Spearman = {agentic.get('elo_fitness_spearman')}。"),
                            'evidence': [str(path.relative_to(ROOT)),
                                         'auto_research/agents/PLAN_CN.md'],
                            'run_refs': ['auto_research/agents'], 'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        spec_path = OUT / f'claim_spec_agent_seed{args.seed}.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(spec_path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
