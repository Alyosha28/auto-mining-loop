#!/usr/bin/env python3
"""Cost budgeter for auto-research experiments (P2).

Simple deterministic planner: batches and fair-replay admissions have measured
costs; experiments declare a budget and the planner truncates rounds if needed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/scheduler/budget_plan.json'
BATCH_MINUTES = 3.0
FAIR_REPLAY_MINUTES = 1.5


def plan(rounds: int, budget_minutes: float, fair_replay: bool = True) -> dict:
    per_round = BATCH_MINUTES + (FAIR_REPLAY_MINUTES if fair_replay else 0.0)
    allowed = int(budget_minutes // per_round) if per_round > 0 else 0
    return {'rounds_requested': rounds, 'rounds_allowed': min(rounds, allowed),
            'minutes_per_round': per_round, 'budget_minutes': budget_minutes,
            'planned_minutes': round(min(rounds, allowed) * per_round, 1),
            'truncated': allowed < rounds}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rounds', type=int, default=12)
    parser.add_argument('--budget-minutes', type=float, default=60.0)
    parser.add_argument('--fair-replay', action='store_true', default=True)
    args = parser.parse_args()
    payload = {'schema': 'AUTO-RESEARCH-BUDGET-PLAN-1', **plan(args.rounds, args.budget_minutes,
                                                               args.fair_replay),
               'cost_model': {'batch_minutes': BATCH_MINUTES, 'fair_replay_minutes': FAIR_REPLAY_MINUTES}}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
