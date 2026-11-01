#!/usr/bin/env python3
"""Run the offline benchmark: tasks x seeds x baselines, with paired deltas."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from bench import core

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))


OUT = ROOT / 'auto_research/benchmark/results'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', default='planted,weak,null')
    parser.add_argument('--seeds', default='1,2,3,4,5')
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--baselines', default='random,evolution,bo,qd')
    parser.add_argument('--tag', default='smoke')
    parser.add_argument('--force-gate-init', action='store_true')
    args = parser.parse_args()
    tasks = [item for item in args.tasks.split(',') if item]
    seeds = [int(item) for item in args.seeds.split(',') if item]
    baselines = [item for item in args.baselines.split(',') if item]
    results = {}
    for task in tasks:
        rows = {}
        for seed in seeds:
            panel = core.make_task_panel(task, seed)
            features = [f'f{i:02d}' for i in range(40)]
            if 'f_state' in panel.columns:
                features.append('f_state')
            dates = __import__('pandas').DatetimeIndex(sorted(panel['date'].unique()))
            cut = int(round(len(dates) * 0.7))
            search_dates, holdout_dates = dates[:cut], dates[cut:]
            seed_rows = {}
            if 'random' in baselines:
                evaluated = core.run_random(panel, features, search_dates, seed, args.budget)
                seed_rows['random'] = core.top_stats(evaluated, panel, holdout_dates)
            if 'evolution' in baselines:
                evaluated = core.run_evolution(panel, features, search_dates, seed + 1000, args.budget)
                seed_rows['evolution'] = core.top_stats(evaluated, panel, holdout_dates)
            if 'bo' in baselines:
                evaluated = core.run_bo(panel, features, search_dates, seed, args.budget)
                seed_rows['bo'] = core.top_stats(evaluated, panel, holdout_dates)
            if 'qd' in baselines:
                evaluated = core.run_qd_novelty(panel, features, search_dates, seed, args.budget,
                                           gate_feature=('f_state' if task.startswith('regime_state') else None),
                                           force_gate_init=args.force_gate_init)
                seed_rows['qd'] = core.top_stats(evaluated, panel, holdout_dates)
            rows[seed] = seed_rows
        results[task] = rows
    summary = {}
    for task, rows in results.items():
        summary[task] = {}
        for baseline in baselines:
            vals = [row[baseline]['mean'] for row in rows.values() if row.get(baseline) and row[baseline].get('mean') is not None]
            passes = [row[baseline]['passes'] for row in rows.values() if row.get(baseline)]
            summary[task][baseline] = {'n': len(vals), 'mean': round(float(np.mean(vals)), 6) if vals else None,
                                       'passed_mean': round(float(np.mean(passes)), 3) if passes else None}
            if baseline != 'random' and 'random' in baselines:
                deltas = [row[baseline]['mean'] - row['random']['mean'] for row in rows.values()
                          if row.get(baseline) and row.get('random') and row[baseline].get('mean') is not None and row['random'].get('mean') is not None]
                summary[task][baseline]['delta_vs_random'] = round(float(np.mean(deltas)), 6) if deltas else None
                summary[task][baseline]['wins_vs_random'] = int(sum(d > 0 for d in deltas)) if deltas else None
    payload = {'schema': 'AUTO-RESEARCH-BENCH-1', 'created_at': now(), 'budget': args.budget,
               'tasks': tasks, 'seeds': seeds, 'baselines': baselines,
               'results': results, 'summary': summary, 'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'bench_{args.tag}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    lines = ['# Benchmark leaderboard', '', f"budget={args.budget}, seeds={seeds}", '',
             '| task | baseline | mean holdout top5 | delta vs random | wins | passed(top5) |', '|---|---|---|---|---|---|']
    for task, task_summary in summary.items():
        for baseline, value in task_summary.items():
            lines.append(f"| {task} | {baseline} | {value['mean']} | {value.get('delta_vs_random', '—')} | {value.get('wins_vs_random', '—')} | {value.get('passed_mean', '—')} |")
    leaderboard = OUT / f'LEADERBOARD_{args.tag}.md'
    leaderboard.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'result': str(path.relative_to(ROOT)), 'leaderboard': str(leaderboard.relative_to(ROOT)),
                      'summary': summary}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
