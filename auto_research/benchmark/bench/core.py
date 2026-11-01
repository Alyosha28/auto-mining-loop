#!/usr/bin/env python3
"""Offline benchmark harness: synthetic panel + LLM-free baselines (random/evolution/BO/QD)."""
from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from synth_benchmark import make_panel  # noqa: E402


def make_task_panel(task: str, seed: int, n_symbols: int = 250, n_weeks: int = 160,
                    n_features: int = 40):
    """Extend synth tasks with nonlinear / regime-switching / heteroskedastic variants."""
    if task in ('planted', 'weak', 'null'):
        return make_panel(task, seed, n_symbols=n_symbols, n_weeks=n_weeks, n_features=n_features)
    rng = np.random.default_rng(seed)
    dates = pd.date_range('2017-01-06', periods=n_weeks, freq='W-FRI')
    symbols = [f'S{index:04d}' for index in range(n_symbols)]
    frames = []
    for index, date in enumerate(dates):
        features = rng.standard_normal((n_symbols, n_features))
        planted = features[:, :5].sum(axis=1)
        signal = (planted - planted.mean()) / (planted.std() + 1e-9)
        if task == 'nonlinear':
            signal = np.tanh(signal)
            forward = 0.30 * signal + rng.standard_normal(n_symbols)
        elif task in ('regime', 'regime_state', 'regime_state2', 'regime_state3'):
            if task == 'regime_state2':
                state = 1.0 if features[:, 0].mean() >= 0 else -1.0
                beta = 0.35 * state
            elif task == 'regime_state3':
                state = 1.0 if np.abs(features[:, 0]).mean() > 0.8 else -1.0
                beta = 0.35 * state
            else:
                state = 1.0 if index % 2 == 0 else -1.0
                beta = 0.35 * state
            forward = beta * signal + rng.standard_normal(n_symbols)
        elif task == 'hetero':
            scale = 0.5 + np.abs(features[:, 0])
            forward = 0.20 * signal + rng.standard_normal(n_symbols) * scale
        else:
            raise SystemExit(f'unknown task {task}')
        frame = pd.DataFrame(features, columns=[f'f{i:02d}' for i in range(n_features)])
        if task in ('regime_state', 'regime_state2', 'regime_state3'):
            frame['f_state'] = state
        frame.insert(0, 'forward_return', forward)
        frame.insert(0, 'research_mask', True)
        frame.insert(0, 'log_market_cap', rng.standard_normal(n_symbols))
        frame.insert(0, 'symbol', symbols)
        frame.insert(0, 'date', date)
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def ast_features(node: dict) -> list[float]:
    ops = ['rank', 'zscore', 'winsorize', 'vol_scaled', 'tanh', 'sign', 'residual_size',
           'delta4', 'delta13', 'delta_w', 'ema5', 'lag5', 'neg', 'ratio', 'diff', 'rank_minus', 'product']
    counts = {op: 0 for op in ops}
    leaves = [0]
    def depth(n):
        if n['kind'] == 'leaf':
            leaves[0] += 1
            return 1
        if n['kind'] == 'unary':
            counts[n['op']] = counts.get(n['op'], 0) + 1
            return 1 + depth(n['arg'])
        counts[n['op']] = counts.get(n['op'], 0) + 1
        return 1 + max(depth(n['left']), depth(n['right']))
    d = depth(node)
    return [d, leaves[0]] + [counts.get(op, 0) for op in ops]


def skeleton(node: dict) -> str:
    if node['kind'] == 'leaf':
        return 'L'
    if node['kind'] == 'unary':
        return f"U{node['op']}({skeleton(node['arg'])})"
    return f"B{node['op']}({skeleton(node['left'])},{skeleton(node['right'])})"


def top_holdout(rows: list[dict], panel, holdout_dates, k: int = 5) -> float | None:
    return top_stats(rows, panel, holdout_dates, k)['mean']

def top_stats(rows: list[dict], panel, holdout_dates, k: int = 5) -> dict:
    vals = []
    passes = 0
    for row in sorted(rows, key=lambda item: -item['fitness'])[:k]:
        node = row.get('node') or es.expr_lookup(row['formula'])
        if node is None:
            continue
        result = es.evaluate_expr(node, panel, holdout_dates)
        if result:
            vals.append(result['fitness'])
            passes += 1 if result.get('passed') else 0
    return {'mean': float(np.mean(vals)) if vals else None, 'passes': passes, 'n': len(vals)}


def run_random(panel, features, search_dates, seed, budget):
    return es.run_search('random', seed, budget, panel, features, search_dates)


def run_evolution(panel, features, search_dates, seed, budget):
    return es.run_search('evolution', seed, budget, panel, features, search_dates)


def run_bo(panel, features, search_dates, seed, budget, init=5, pool_size=200):
    rng = random.Random(seed)
    rows = []
    seen = set()
    def evaluate(node):
        result = es.evaluate_expr(node, panel, search_dates)
        if not result:
            return None
        row = {'node': node, 'formula': result['formula'], 'fitness': result['fitness']}
        rows.append(row); seen.add(row['formula'])
        return row
    while len(rows) < init:
        node = es.random_expr(rng, features)
        if es.canonical(node) in seen:
            continue
        evaluate(node)
    while len(rows) < budget:
        X = np.asarray([ast_features(row['node']) for row in rows])
        y = np.asarray([row['fitness'] for row in rows])
        X = (X - X.mean(0)) / (X.std(0) + 1e-9)
        try:
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import RBF, WhiteKernel, ConstantKernel
            gp = GaussianProcessRegressor(kernel=ConstantKernel(0.3) * RBF(np.ones(X.shape[1])) + WhiteKernel(0.05),
                                          normalize_y=True, alpha=1e-4)
            gp.fit(X, y)
            pool = []
            while len(pool) < pool_size:
                node = es.random_expr(rng, features)
                if es.canonical(node) not in seen:
                    pool.append(node)
            P = np.asarray([ast_features(node) for node in pool])
            P = (P - X.mean(0)) / (X.std(0) + 1e-9)
            mean, std = gp.predict(P, return_std=True)
            chosen = pool[int(np.argmax(mean + 1.5 * std))]
        except Exception:  # noqa: BLE001
            chosen = es.random_expr(rng, features)
        evaluate(chosen)
    return rows


def run_qd_novelty(panel, features, search_dates, seed, budget, init=5, batch=1, return_stats=False, gate_feature=None, gate_prob=0.2, force_gate_init=False):
    """QD-Elites over random mutations with operator-skeleton novelty (no LLM)."""
    rng = random.Random(seed)
    rows = []
    archive = {}
    skeleton_best = {}
    seen = set()
    stats = {'proposed': 0, 'duplicate_formula': 0, 'duplicate_skeleton': 0, 'evaluated': 0, 'cell_visits': {}}
    def evaluate(node):
        result = es.evaluate_expr(node, panel, search_dates)
        if not result:
            return None
        formula = result['formula']
        stats['proposed'] += 1
        if formula in seen:
            stats['duplicate_formula'] += 1
            return None
        sk = skeleton(node)
        best = skeleton_best.get(sk)
        if best is not None and result['fitness'] <= best + 0.01:
            stats['duplicate_skeleton'] += 1
            return None
        row = {'node': node, 'formula': formula, 'fitness': result['fitness'],
               'turnover_proxy': result['turnover_proxy']}
        rows.append(row); seen.add(formula); stats['evaluated'] += 1
        skeleton_best[sk] = max(best if best is not None else -9.0, result['fitness'])
        depth = 1
        def d(n):
            if n['kind'] == 'leaf':
                return 1
            if n['kind'] == 'unary':
                return 1 + d(n['arg'])
            return 1 + max(d(n['left']), d(n['right']))
        depth = d(node)
        db = 'shallow' if depth <= 2 else ('mid' if depth <= 4 else 'deep')
        tv = result.get('turnover_proxy')
        tb = 'unknown' if tv is None else ('low' if tv < 0.5 else ('mid' if tv < 0.8 else 'high'))
        key = (node['op'] if node['kind'] != 'leaf' else 'leaf', db, tb)
        stats['cell_visits'][key] = stats['cell_visits'].get(key, 0) + 1
        if key not in archive or result['fitness'] > archive[key]['fitness']:
            archive[key] = row
        return row
    def apply_gate(node):
        if force_gate_init and gate_feature:
            return {'kind': 'binary', 'op': 'product',
                    'left': {'kind': 'leaf', 'feature': gate_feature}, 'right': node}
        return node
    attempts = 0
    while len(rows) < init and attempts < init * 100:
        attempts += 1
        node = apply_gate(es.random_expr(rng, features))
        if es.canonical(node) in seen:
            continue
        evaluate(node)
    attempts = 0
    while len(rows) < budget and attempts < budget * 100:
        attempts += 1
        elites = sorted(archive.values(), key=lambda row: -row['fitness'])
        if not elites:
            node = apply_gate(es.random_expr(rng, features))
            evaluate(node)
            continue
        cell_counts = {}
        for row in rows:
            node = row['node']
            depth = 1
            def d(n):
                if n['kind'] == 'leaf':
                    return 1
                if n['kind'] == 'unary':
                    return 1 + d(n['arg'])
                return 1 + max(d(n['left']), d(n['right']))
            depth = d(node)
            db = 'shallow' if depth <= 2 else ('mid' if depth <= 4 else 'deep')
            tv = row.get('turnover_proxy')
            tb = 'unknown' if tv is None else ('low' if tv < 0.5 else ('mid' if tv < 0.8 else 'high'))
            key = (node['op'] if node['kind'] != 'leaf' else 'leaf', db, tb)
            cell_counts[key] = cell_counts.get(key, 0) + 1
        least = sorted(elites, key=lambda row: cell_counts.get((row['node']['op'] if row['node']['kind'] != 'leaf' else 'leaf',
                                                                'shallow' if 1 <= 2 else 'mid', 'unknown'), 0))
        parent = rng.choice(least[:max(1, min(5, len(least)))])
        node = es.mutate(parent['node'], rng, features)
        if gate_feature and rng.random() < gate_prob:
            node = {'kind': 'binary', 'op': 'product',
                    'left': {'kind': 'leaf', 'feature': gate_feature}, 'right': node}
        evaluate(node)
    if return_stats:
        import math
        visits = list(stats['cell_visits'].values())
        total = sum(visits) or 1
        stats['cell_entropy'] = -sum((v / total) * math.log2(v / total) for v in visits) if visits else 0.0
        stats['unique_skeletons'] = len(skeleton_best)
        stats['archive_cells'] = len(archive)
        stats.pop('cell_visits', None)
        return rows, stats
    return rows
