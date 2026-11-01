#!/usr/bin/env python3
"""Bayesian optimization baseline over mutation candidates (GP + UCB)."""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.gaussian_process import GaussianProcessRegressor
from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from agents.expr_grammar import parse, validate  # noqa: E402
from agents.qd_search import ast_features  # noqa: E402

OUT = ROOT / 'auto_research/agents/experiments'
LIB = ROOT / 'auto_research/expr_library/top_expressions_seed1.json'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--split-frac', type=float, default=0.7)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--init', type=int, default=5)
    parser.add_argument('--pool', type=int, default=300)
    parser.add_argument('--beta', type=float, default=1.5)
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * args.split_frac)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    library = json.loads(LIB.read_text(encoding='utf-8'))
    parents = []
    for row in sorted(library, key=lambda item: -item.get('fitness', -9))[:3]:
        try:
            node = parse(row['formula'], set(features)); validate(node, set(features))
            parents.append({'formula': row['formula'], 'node': node})
        except Exception:  # noqa: BLE001
            continue
    parent_formulas = {es.canonical(p['node']) for p in parents}
    rng = random.Random(args.seed)
    evaluated = []
    seen = set()

    def mutate_candidate():
        parent = rng.choice(parents)
        node = es.mutate(parent['node'], rng, features)
        formula = es.canonical(node)
        if formula in seen or formula in parent_formulas:
            return None
        return {'formula': formula, 'node': node}

    def evaluate(row):
        result = es.evaluate_expr(row['node'], panel, search_dates)
        if not result:
            return None
        row = {**row, 'search_fitness': result['fitness'], 'turnover_proxy': result['turnover_proxy']}
        evaluated.append(row)
        seen.add(row['formula'])
        return row

    # initial design
    while len(evaluated) < args.init:
        cand = mutate_candidate()
        if cand:
            evaluate(cand)
    # BO loop
    while len(evaluated) < args.budget:
        X = np.asarray([ast_features(row['node']) for row in evaluated], dtype=float)
        y = np.asarray([row['search_fitness'] for row in evaluated], dtype=float)
        X = (X - X.mean(0)) / (X.std(0) + 1e-9)
        kernel = ConstantKernel(0.3) * RBF(length_scale=np.ones(X.shape[1])) + WhiteKernel(0.05)
        try:
            gp = GaussianProcessRegressor(kernel=kernel, normalize_y=True, alpha=1e-4, n_restarts_optimizer=0)
            gp.fit(X, y)
        except Exception:  # noqa: BLE001
            gp = None
        pool = []
        while len(pool) < args.pool:
            cand = mutate_candidate()
            if cand:
                pool.append(cand)
        if gp is None:
            chosen = rng.choice(pool)
        else:
            P = np.asarray([ast_features(row['node']) for row in pool], dtype=float)
            P = (P - X.mean(0)) / (X.std(0) + 1e-9)
            mean, std = gp.predict(P, return_std=True)
            ucb = mean + args.beta * std
            chosen = pool[int(np.argmax(ucb))]
        evaluate(chosen)
    top = sorted(evaluated, key=lambda row: -row['search_fitness'])[:5]
    holdout = []
    for row in top:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            holdout.append(result['fitness'])
    bo_top5 = float(np.mean(holdout)) if holdout else None
    # random mutation baseline with same rng seed
    rng2 = random.Random(args.seed * 31 + 7)
    rm_rows = []
    rm_seen = set()
    while len(rm_rows) < args.budget:
        parent = rng2.choice(parents)
        node = es.mutate(parent['node'], rng2, features)
        formula = es.canonical(node)
        if formula in rm_seen or formula in parent_formulas:
            continue
        rm_seen.add(formula)
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            rm_rows.append({'node': node, 'fitness': result['fitness']})
    rm_hold = []
    for row in sorted(rm_rows, key=lambda item: -item['fitness'])[:5]:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            rm_hold.append(result['fitness'])
    rm_top5 = float(np.mean(rm_hold)) if rm_hold else None
    payload = {'schema': 'AUTO-RESEARCH-BO-1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'provider': 'bo', 'model': 'gp_ucb', 'seed': args.seed, 'split_frac': args.split_frac,
               'budget': args.budget, 'n_evaluated': len(evaluated),
               'bo_top5_holdout_fitness': bo_top5, 'random_mutation_top5_holdout_fitness': rm_top5,
               'delta_vs_random_mutation': (bo_top5 - rm_top5) if (bo_top5 is not None and rm_top5 is not None) else None,
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'bo_random_split{args.split_frac}_seed{args.seed}_b{args.budget}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('seed', 'split_frac', 'budget', 'n_evaluated',
                                                    'bo_top5_holdout_fitness',
                                                    'random_mutation_top5_holdout_fitness',
                                                    'delta_vs_random_mutation')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
