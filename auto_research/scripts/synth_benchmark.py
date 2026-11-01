#!/usr/bin/env python3
"""Synthetic benchmark: does evidence governance reduce agent self-deception?

Tasks: planted / weak / null cross-sectional factor tasks. An evolution agent
searches expressions on the first 70% of weeks; naive governance declares a
discovery from the search window alone, governed governance additionally requires
holdout replication. We measure declaration rates and replication-failure rates.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import expr_search as es  # noqa: E402

OUT = ROOT / 'auto_research/synth_benchmark'
RESULT = OUT / 'synth_result.json'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_panel(task: str, seed: int, n_symbols: int = 250, n_weeks: int = 160,
               n_features: int = 40) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range('2017-01-06', periods=n_weeks, freq='W-FRI')
    symbols = [f'S{index:04d}' for index in range(n_symbols)]
    frames = []
    for date in dates:
        features = rng.standard_normal((n_symbols, n_features))
        if task == 'null':
            forward = rng.standard_normal(n_symbols)
        else:
            beta = 0.35 if task == 'planted' else 0.12
            planted = features[:, :5].sum(axis=1)
            signal = (planted - planted.mean()) / (planted.std() + 1e-9)
            forward = beta * signal + rng.standard_normal(n_symbols)
        frame = pd.DataFrame(features, columns=[f'f{index:02d}' for index in range(n_features)])
        frame.insert(0, 'forward_return', forward)
        frame.insert(0, 'research_mask', True)
        frame.insert(0, 'log_market_cap', rng.standard_normal(n_symbols))
        frame.insert(0, 'symbol', symbols)
        frame.insert(0, 'date', date)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def run_one(task: str, seed: int, budget: int = 20, generations: int = 2) -> dict:
    panel = make_panel(task, seed)
    feature_pool = [f'f{index:02d}' for index in range(40)]
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = int(round(len(dates) * 0.7))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    rows = es.run_search('evolution', seed, budget, panel, feature_pool, search_dates, generations)
    if not rows:
        return {'task': task, 'seed': seed, 'status': 'NO_SEARCH_RESULT'}
    top = max(rows, key=lambda row: row['fitness'])
    expr = es.expr_lookup(top['formula'])
    holdout = es.evaluate_expr(expr, panel, holdout_dates)
    naive_pass = bool(top['passed'])
    governed_pass = bool(naive_pass and holdout and holdout['passed'])
    strict_pass = bool(governed_pass and holdout and holdout['mean_ic'] is not None and holdout['mean_ic'] >= 0.02)
    return {'task': task, 'seed': seed, 'status': 'COMPLETED',
            'search_formula': top['formula'], 'search_fitness': top['fitness'],
            'search_ic': top['mean_ic'], 'search_passed': naive_pass,
            'holdout_fitness': holdout['fitness'] if holdout else None,
            'holdout_ic': holdout['mean_ic'] if holdout else None,
            'holdout_pass': holdout['passed'] if holdout else False,
            'naive_declares': naive_pass, 'governed_declares': governed_pass,
            'strict_declares': strict_pass}


def summarize(rows: list[dict]) -> dict:
    completed = [row for row in rows if row['status'] == 'COMPLETED']
    out = {'n': len(completed)}
    naive = [row for row in completed if row['naive_declares']]
    governed = [row for row in completed if row['governed_declares']]
    strict = [row for row in completed if row.get('strict_declares')]
    out['naive_declarations'] = len(naive)
    out['governed_declarations'] = len(governed)
    out['naive_replication_failures'] = sum(1 for row in naive if not row['holdout_pass'])
    out['naive_replication_failure_rate'] = round(out['naive_replication_failures'] / len(naive), 4) if naive else None
    out['governed_replication_failures'] = sum(1 for row in governed if not row['holdout_pass'])
    out['strict_declarations'] = len(strict)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', default='1,2,3,4,5')
    parser.add_argument('--budget', type=int, default=20)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    seeds = [int(value) for value in args.seeds.split(',') if value.strip()]
    all_rows = []
    for task in ('planted', 'weak', 'null'):
        for seed in seeds:
            row = run_one(task, seed, args.budget)
            all_rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {task: summarize([row for row in all_rows if row['task'] == task])
               for task in ('planted', 'weak', 'null')}
    payload = {'schema': 'AUTO-RESEARCH-SYNTH-BENCHMARK-1', 'created_at': now(),
               'seeds': seeds, 'budget': args.budget,
               'tasks': {'planted': '5 planted features (beta 0.6)',
                         'weak': '5 planted features (beta 0.18)',
                         'null': 'no signal (forward_return = noise)'},
               'summary': summary, 'rows': all_rows,
               'claim_rule': 'naive: search pass; governed: search pass AND holdout pass; strict: governed AND holdout |IC|>=0.02',
               'score_window_read': False}
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'summary': summary}, ensure_ascii=False, indent=1))
    # claim
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import claim_ledger
    import review_claims
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'synth_benchmark',
            'claims': [{'claim_id': 'CLM-SYNTH-BENCH-002', 'type': 'experimental_result',
                        'statement': (f"合成 benchmark（planted/weak/null × {len(seeds)} seeds，budget {args.budget}）："
                                      f"null 任务声明数：naive {summary['null']['naive_declarations']}、governed {summary['null']['governed_declarations']}、strict {summary['null']['strict_declarations']}；"
                                      f"planted 任务：naive {summary['planted']['naive_declarations']}、governed {summary['planted']['governed_declarations']}、strict {summary['planted']['strict_declarations']}；"
                                      f"weak 任务：naive {summary['weak']['naive_declarations']}、governed {summary['weak']['governed_declarations']}、strict {summary['weak']['strict_declarations']}。"),
                        'evidence': ['auto_research/synth_benchmark/synth_result.json',
                                     'auto_research/paper/PLAN_CN.md'],
                        'run_refs': ['auto_research/synth_benchmark'],
                        'preregistration': 'auto_research/PREREGISTRATION.json',
                        'score_window_read': False}]}
    path = OUT / 'claim_spec.json'
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
