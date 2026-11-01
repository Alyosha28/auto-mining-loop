#!/usr/bin/env python3
"""P2 offline scheduler experiment (panel-level bootstrap).

Reads auto_research/scheduler/panel_table.json (fair-replay admission per 1000
evaluated for a panel pool, base harness) and compares scheduling policies:
round_robin / greedy / ucb1 (plus optional epsilon_greedy).

Honesty note: batch mining is deterministic given (panel, harness, budget), so
experimental seeds do not exist at batch level. Variability is introduced by
panel-level bootstrap resampling (seeds = RNG seeds), and results are labelled
as offline policy evaluation, not as stochastic experiments.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger  # noqa: E402
import review_claims  # noqa: E402

TABLE = ROOT / 'auto_research/scheduler/panel_table.json'
OUT = ROOT / 'auto_research/scheduler'
RESULT = OUT / 'scheduler_result.json'
COST_MINUTES_PER_BATCH = 3.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_table() -> list[dict]:
    rows = json.loads(TABLE.read_text(encoding='utf-8'))['rows']
    return [row for row in rows if row.get('status') == 'COMPLETED' and row.get('added_per_1000') is not None]


def policy_round_robin(pool: list[int], rounds: int) -> list[int]:
    return [pool[index % len(pool)] for index in range(rounds)]


def policy_greedy(pool: list[int], rounds: int, rewards: list[float]) -> list[int]:
    best = max(pool, key=lambda index: rewards[index])
    return [best] * rounds


def policy_ucb1(pool: list[int], rounds: int, rewards: list[float], rng: random.Random,
                scale: float) -> list[int]:
    counts = {index: 0 for index in pool}
    sums = {index: 0.0 for index in pool}
    actions = []
    # forced initial exploration
    for index in pool:
        actions.append(index)
        counts[index] += 1
        sums[index] += rewards[index]
        if len(actions) >= rounds:
            return actions[:rounds]
    while len(actions) < rounds:
        total = len(actions)
        scores = {}
        for index in pool:
            mean = sums[index] / counts[index]
            bonus = scale * ((2.0 * math.log(total)) ** 0.5) / (counts[index] ** 0.5)
            scores[index] = mean + bonus
        choice = max(pool, key=lambda index: scores[index])
        actions.append(choice)
        counts[choice] += 1
        sums[choice] += rewards[choice]
    return actions


def policy_epsilon_greedy(pool: list[int], rounds: int, rewards: list[float], rng: random.Random,
                          epsilon: float = 0.2) -> list[int]:
    counts = {index: 0 for index in pool}
    sums = {index: 0.0 for index in pool}
    actions = []
    for index in pool:
        actions.append(index)
        counts[index] += 1
        sums[index] += rewards[index]
        if len(actions) >= rounds:
            return actions[:rounds]
    while len(actions) < rounds:
        if rng.random() < epsilon:
            choice = rng.choice(pool)
        else:
            choice = max(pool, key=lambda index: sums[index] / counts[index])
        actions.append(choice)
        counts[choice] += 1
        sums[choice] += rewards[choice]
    return actions


def simulate(policy: str, order: list[int], rewards: list[float], rounds: int, rng: random.Random) -> dict:
    scale = max(rewards) if rewards else 1.0
    normalized = [value / scale if scale else 0.0 for value in rewards]
    if policy == 'round_robin':
        actions = policy_round_robin(order, rounds)
    elif policy == 'greedy':
        actions = policy_greedy(order, rounds, normalized)
    elif policy == 'ucb1':
        actions = policy_ucb1(order, rounds, normalized, rng, scale=1.0)
    elif policy == 'epsilon_greedy':
        actions = policy_epsilon_greedy(order, rounds, normalized, rng)
    else:
        raise SystemExit(f'unknown policy: {policy}')
    cumulative = [sum(rewards[index] for index in actions[:step + 1]) for step in range(len(actions))]
    regret = [sum(max(rewards) - rewards[index] for index in actions[:step + 1]) for step in range(len(actions))]
    return {'policy': policy, 'actions': actions,
            'cumulative_per_1000': cumulative[-1] if cumulative else 0.0,
            'regret_per_1000': regret[-1] if regret else 0.0,
            'budget_minutes': round(len(actions) * COST_MINUTES_PER_BATCH, 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=12)
    parser.add_argument('--seeds', default='7,11,17')
    parser.add_argument('--policies', default='round_robin,greedy,ucb1,epsilon_greedy')
    args = parser.parse_args()
    rows = load_table()
    if len(rows) < 3:
        raise SystemExit('need >=3 completed panels in panel_table.json')
    rewards = [float(row['added_per_1000']) for row in rows]
    seeds = [int(value) for value in args.seeds.split(',') if value.strip()]
    policies = [value.strip() for value in args.policies.split(',') if value.strip()]
    runs = []
    for seed in seeds:
        rng = random.Random(seed)
        order = list(range(len(rows)))
        rng.shuffle(order)
        for policy in policies:
            runs.append({'seed': seed, **simulate(policy, order, rewards, args.rounds, rng)})
    summary = {}
    for policy in policies:
        values = [run['cumulative_per_1000'] for run in runs if run['policy'] == policy]
        regrets = [run['regret_per_1000'] for run in runs if run['policy'] == policy]
        summary[policy] = {'mean_cumulative_per_1000': round(sum(values) / len(values), 2),
                           'min': round(min(values), 2), 'max': round(max(values), 2),
                           'mean_regret_per_1000': round(sum(regrets) / len(regrets), 2),
                           'n': len(values)}
    best = max(summary, key=lambda policy: summary[policy]['mean_cumulative_per_1000'])
    payload = {
        'schema': 'AUTO-RESEARCH-SCHEDULER-RESULT-1',
        'created_at': now(), 'rounds': args.rounds, 'seeds': seeds,
        'selection': 'panel-level bootstrap (batch mining is deterministic; seeds are RNG seeds, not experimental seeds)',
        'policies': policies, 'panels': [row['panel'] for row in rows],
        'panel_rewards_per_1000': {row['panel']: row['added_per_1000'] for row in rows},
        'summary': summary, 'best_policy': best, 'runs': runs,
        'cost_model': f'{COST_MINUTES_PER_BATCH} minutes per batch',
        'score_window_read': False,
    }
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    # claim (offline evaluation)
    claim_spec = {
        'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'p2_scheduler',
        'claim': None,
        'claims': [{
            'claim_id': 'CLM-P2-SCHEDULER-001',
            'type': 'experimental_result',
            'statement': (f"离线面板级 bootstrap 评估（{args.rounds} 轮、seeds {seeds}）中，调度策略 {best} 的累计 "
                          f"admission/1000 最高（均值 {summary[best]['mean_cumulative_per_1000']}，范围 "
                          f"{summary[best]['min']}–{summary[best]['max']}），优于 round_robin（均值 "
                          f"{summary['round_robin']['mean_cumulative_per_1000']}）；batch mining 确定性，故为离线评估而非随机实验。"),
            'evidence': ['auto_research/scheduler/panel_table.json', 'auto_research/scheduler/scheduler_result.json',
                         'auto_research/PREREGISTRATION.json'],
            'run_refs': ['auto_research/scheduler'],
            'preregistration': 'auto_research/PREREGISTRATION.json',
            'score_window_read': False,
        }],
    }
    claim_path = OUT / 'claim_spec.json'
    claim_path.write_text(json.dumps(claim_spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed = review_claims.review_all()
    print(json.dumps({'best_policy': best, 'summary': summary,
                      'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
