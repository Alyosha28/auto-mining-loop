#!/usr/bin/env python3
"""Run the pre-registered evaluation matrix: baselines + agentic full/ablations."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
from agents import manifest  # noqa: E402
OUT = ROOT / 'auto_research/agents/experiments'
COMPARE = ROOT / 'auto_research/agents/compare.py'
COMPARE_OUT = ROOT / 'auto_research/agents/experiments'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_compare(args, arm: str, seed: int, tag: str) -> None:
    command = [sys.executable, str(COMPARE), '--budget', str(args.budget), '--seed', str(seed), '--tag', tag]
    if arm != 'baseline':
        command += ['--model', args.model, '--proposer', args.proposer,
                    '--generations', str(args.generations), '--per-generation', str(args.per_generation)]
        if args.require_accepted:
            command.append('--require-accepted')
        if arm == 'agentic_no_critic':
            command.append('--no-critic')
        elif arm == 'agentic_no_reflection':
            command.append('--no-reflection')
        elif arm == 'agentic_no_elo':
            command.append('--no-elo')
    completed = subprocess.run(command, capture_output=True, text=True, timeout=7200)
    if completed.returncode != 0:
        raise SystemExit(f'compare failed for {arm} seed {seed}: {completed.stderr[-1200:]}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=None)
    parser.add_argument('--proposer', choices=['llm', 'mock'], default='llm')
    parser.add_argument('--seeds', default='7,11,17')
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--generations', type=int, default=2)
    parser.add_argument('--per-generation', type=int, default=6)
    parser.add_argument('--claim', action='store_true')
    parser.add_argument('--ablation-seeds', default='7', help='seeds for the 3 ablation arms (compute-limited)')
    args = parser.parse_args()
    seeds = [int(value) for value in args.seeds.split(',') if value.strip()]
    arms = ['baseline']
    if args.model:
        arms += ['agentic_full', 'agentic_no_critic', 'agentic_no_reflection', 'agentic_no_elo']
    ablation_seeds = [int(value) for value in args.ablation_seeds.split(',') if value.strip()]
    matrix = {}
    provider = __import__('os').environ.get('AUTO_RESEARCH_LLM_PROVIDER', 'local')
    tag_base = f"matrix_{provider}_{(args.model or 'baselines').replace('/', '_')}"
    for arm in arms:
        arm_rows = []
        arm_seeds = seeds if arm in ('baseline', 'agentic_full') else ablation_seeds
        for seed in arm_seeds:
            tag = f'{tag_base}_{arm}'
            if arm == 'agentic_full':
                args.require_accepted = True
            else:
                args.require_accepted = False
            run_compare(args, arm, seed, tag)
            path = COMPARE_OUT / f'compare_{tag}_seed{seed}.json'
            payload = json.loads(path.read_text(encoding='utf-8'))
            row = {'seed': seed}
            if arm == 'baseline':
                row['random'] = payload['results']['random']['top5_holdout_fitness']
                row['evolution'] = payload['results']['evolution']['top5_holdout_fitness']
            else:
                result = payload['results']['agentic']
                row.update({'holdout_top5': result.get('top5_holdout_fitness'),
                            'holdout_ic': result.get('top5_holdout_ic'),
                            'n_valid': result.get('n_valid'),
                            'elo_fitness_spearman': result.get('elo_fitness_spearman')})
            arm_rows.append(row)
        matrix[arm] = arm_rows
    summary = {}
    for arm, rows in matrix.items():
        if arm == 'baseline':
            summary[arm] = {'random_mean': round(float(np.mean([row['random'] for row in rows])), 6),
                            'evolution_mean': round(float(np.mean([row['evolution'] for row in rows])), 6)}
        else:
            values = [row['holdout_top5'] for row in rows if row['holdout_top5'] is not None]
            summary[arm] = {'holdout_top5_mean': round(float(np.mean(values)), 6) if values else None,
                            'holdout_ic_mean': round(float(np.mean([row['holdout_ic'] for row in rows if row['holdout_ic'] is not None])), 6)
                            if all(row['holdout_ic'] is not None for row in rows) else None,
                            'elo_spearman_mean': round(float(np.mean([row['elo_fitness_spearman'] for row in rows
                                                                      if row['elo_fitness_spearman'] is not None])), 4)
                            if any(row['elo_fitness_spearman'] is not None for row in rows) else None}
    import os
    payload = {'schema': 'AUTO-RESEARCH-AGENT-MATRIX-1', 'created_at': now(),
               'provider': os.environ.get('AUTO_RESEARCH_LLM_PROVIDER', 'local'), 'model': args.model,
               'proposer': args.proposer, 'seeds': seeds, 'budget': args.budget,
               'generations': args.generations, 'per_generation': args.per_generation,
               'matrix': matrix, 'summary': summary,
               'note': 'mock proposer = plumbing only; llm models must be compared only against their own runs',
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    tag = (args.model or 'baselines').replace('/', '_')
    path = OUT / f'matrix_{tag}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    manifest_path = manifest.write_manifest(path, payload['provider'], args.model, {
        'seeds': seeds, 'ablation_seeds': ablation_seeds, 'budget': args.budget,
        'generations': args.generations, 'per_generation': args.per_generation, 'arms': arms})
    print(json.dumps({'path': str(path.relative_to(ROOT)), 'manifest': str(manifest_path.relative_to(ROOT)),
                      'summary': summary}, ensure_ascii=False, indent=1))
    if args.claim and args.model:
        sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
        import claim_ledger
        import review_claims
        baseline = summary['baseline']
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'agentic_matrix',
                'claims': [{'claim_id': f'CLM-AGENT-MATRIX-{provider.upper()}-{str(args.model or "BASELINES").replace("-", "").upper()[:12]}-001', 'type': 'experimental_result',
                            'statement': (f"agentic matrix（{args.model}，budget {args.budget}，seeds {seeds}）："
                                          f"full holdout top5 = {summary.get('agentic_full', {}).get('holdout_top5_mean')}，"
                                          f"no-critic = {summary.get('agentic_no_critic', {}).get('holdout_top5_mean')}，"
                                          f"no-reflection = {summary.get('agentic_no_reflection', {}).get('holdout_top5_mean')}，"
                                          f"no-elo = {summary.get('agentic_no_elo', {}).get('holdout_top5_mean')}；"
                                          f"random = {baseline['random_mean']}，evolution = {baseline['evolution_mean']}。"),
                            'evidence': [str(path.relative_to(ROOT)), 'auto_research/agents/PLAN_CN.md'],
                            'run_refs': ['auto_research/agents'], 'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        spec_path = OUT / 'claim_spec_matrix.json'
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(spec_path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
