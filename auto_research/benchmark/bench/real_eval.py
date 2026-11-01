#!/usr/bin/env python3
"""Local-only evaluation on the project's dev panel (no data is shipped)."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/benchmark'))
import expr_search as es  # noqa: E402
from bench import core  # noqa: E402
from agents.expr_grammar import parse, validate  # noqa: E402

OUT = ROOT / 'auto_research/benchmark/results'


def library_parents(features):
    path = ROOT / 'auto_research/expr_library/top_expressions_seed1.json'
    rows = json.loads(path.read_text())
    parents = []
    for row in sorted(rows, key=lambda item: -item.get('fitness', -9))[:3]:
        try:
            node = parse(row['formula'], set(features)); validate(node, set(features))
            parents.append({'formula': row['formula'], 'node': node})
        except Exception:  # noqa: BLE001
            continue
    return parents


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--seeds', default='18,19,20')
    args = parser.parse_args()
    seeds = [int(s) for s in args.seeds.split(',') if s]
    panel = es.build_panel()
    features = es.features(panel)
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = int(round(len(dates) * 0.7))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    parents = library_parents(features)
    parent_formulas = {es.canonical(p['node']) for p in parents}
    results = []
    for seed in seeds:
        qd_rows = core.run_qd_novelty(panel, features, search_dates, seed=seed, budget=args.budget)
        qd = core.top_stats(qd_rows, panel, holdout_dates)
        rng = random.Random(seed * 31 + 7)
        rm_rows = []
        seen = set()
        while len(rm_rows) < args.budget:
            parent = rng.choice(parents)
            node = es.mutate(parent['node'], rng, features)
            formula = es.canonical(node)
            if formula in seen or formula in parent_formulas:
                continue
            seen.add(formula)
            result = es.evaluate_expr(node, panel, search_dates)
            if result:
                rm_rows.append({'node': node, 'fitness': result['fitness']})
        rm = core.top_stats(rm_rows, panel, holdout_dates)
        results.append({'seed': seed, 'qd_no_llm': qd, 'random_mutation': rm,
                        'delta': (qd['mean'] - rm['mean']) if (qd['mean'] is not None and rm['mean'] is not None) else None})
    payload = {'schema': 'AUTO-RESEARCH-REAL-EVAL-1', 'split_frac': 0.7, 'budget': args.budget, 'results': results,
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'real_eval_qd_no_llm_b{args.budget}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
