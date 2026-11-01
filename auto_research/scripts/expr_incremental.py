#!/usr/bin/env python3
"""Incremental (residual-target) expression search.

Definition of "better" used here: a factor is better if it explains return variance
that existing panel factors do NOT explain. We fit a ridge base model on the dev
search window (OOF residuals for candidate selection), then score expressions by
their IC against the residual, and finally compare on the holdout dev window
against (a) random search and (b) existing panel features' own residual IC.

Score window is never loaded.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import expr_search as es  # noqa: E402

OUT = ROOT / 'auto_research/expr_search'
RESULTS = OUT / 'results'
RESULT = OUT / 'incremental_result.json'
N_FOLDS = 5
RIDGE_ALPHA = 1.0


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rank_frame(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    ranked = panel.groupby('date')[columns].rank(pct=True)
    return ranked.astype('float32').fillna(0.5)


def build_targets(panel: pd.DataFrame, feature_pool: list[str], holdout_fraction: float):
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * (1.0 - holdout_fraction))))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    usable = panel['research_mask'].fillna(False).astype(bool) & panel['forward_return'].notna()
    X = rank_frame(panel, feature_pool)
    y = panel.groupby('date')['forward_return'].rank(pct=True).astype('float32')
    keep = usable.to_numpy()
    Xv = X.to_numpy()
    yv = y.fillna(np.nan).to_numpy()
    valid = keep & np.isfinite(yv) & np.isfinite(Xv).any(axis=1)
    dates_series = panel['date']
    search_mask = dates_series.isin(search_dates).to_numpy() & valid
    holdout_mask = dates_series.isin(holdout_dates).to_numpy() & valid
    # OOF residuals on search window: contiguous date blocks
    search_rows = np.where(search_mask)[0]
    block_dates = np.array_split(search_dates, N_FOLDS)
    residual_oof = pd.Series(np.nan, index=panel.index, dtype='float32')
    for block in block_dates:
        block_mask = dates_series.isin(block).to_numpy()
        train = search_mask & ~block_mask
        test = search_mask & block_mask
        model = Ridge(alpha=RIDGE_ALPHA, solver='lsqr', fit_intercept=True)
        model.fit(Xv[train], yv[train])
        residual_oof.iloc[np.where(test)[0]] = (yv[test] - model.predict(Xv[test])).astype('float32')
    # holdout residuals from a model fit on the whole search window
    model = Ridge(alpha=RIDGE_ALPHA, solver='lsqr', fit_intercept=True)
    model.fit(Xv[search_mask], yv[search_mask])
    residual_holdout = pd.Series(np.nan, index=panel.index, dtype='float32')
    residual_holdout.iloc[np.where(holdout_mask)[0]] = (yv[holdout_mask] - model.predict(Xv[holdout_mask])).astype('float32')
    return {'search_dates': search_dates, 'holdout_dates': holdout_dates,
            'residual_oof': residual_oof, 'residual_holdout': residual_holdout,
            'base_holdout_ic': None}


def ic_against(panel: pd.DataFrame, values: pd.Series, dates: pd.DatetimeIndex,
               target: pd.Series, min_pairs: int = 30) -> tuple[pd.Series, int]:
    frame = panel.loc[panel['date'].isin(dates), ['date', 'symbol']].copy()
    frame['v'] = values.loc[frame.index].astype(float)
    frame['y'] = target.loc[frame.index].astype(float)
    frame = frame.dropna(subset=['v', 'y'])
    if frame.empty:
        return pd.Series(dtype=float), 0
    frame['vr'] = frame.groupby('date')['v'].rank()
    frame['yr'] = frame.groupby('date')['y'].rank()
    grouped = frame.groupby('date')
    counts = grouped.size()
    mx = grouped['vr'].mean()
    my = grouped['yr'].mean()
    cov = grouped.apply(lambda g: float(((g['vr'] - g['vr'].mean()) * (g['yr'] - g['yr'].mean())).sum()))
    vx = grouped['vr'].apply(lambda s: float(((s - s.mean()) ** 2).sum()))
    vy = grouped['yr'].apply(lambda s: float(((s - s.mean()) ** 2).sum()))
    corr = (cov / np.sqrt((vx * vy).replace(0, np.nan))).dropna()
    corr = corr.loc[counts.reindex(corr.index) >= min_pairs]
    return corr.sort_index(), len(corr)


def eval_candidate(expr: dict, panel: pd.DataFrame, targets: dict) -> dict | None:
    values = es.eval_tree(expr, panel)
    if values is None:
        return None
    values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5000:
        return None
    search_ic, n_search = ic_against(panel, values, targets['search_dates'], targets['residual_oof'])
    if n_search < 20:
        return None
    mean_ic = float(search_ic.mean())
    std = float(search_ic.std(ddof=1))
    icir = mean_ic / std if std > 0 else float('nan')
    direction = 1.0 if mean_ic >= 0 else -1.0
    values = values * direction
    holdout_ic, n_holdout = ic_against(panel, values, targets['holdout_dates'], targets['residual_holdout'])
    holdout_mean = float(holdout_ic.mean()) if len(holdout_ic) else float('nan')
    holdout_std = float(holdout_ic.std(ddof=1)) if len(holdout_ic) > 1 else float('nan')
    holdout_icir = holdout_mean / holdout_std if np.isfinite(holdout_std) and holdout_std > 0 else float('nan')
    fitness = abs(mean_ic) + 0.3 * abs(icir) if np.isfinite(icir) else abs(mean_ic)
    return {'formula': es.canonical(expr), 'search_ic': round(abs(mean_ic), 6),
            'search_icir': round(abs(icir), 6) if np.isfinite(icir) else None,
            'fitness_search': round(fitness, 6),
            'holdout_residual_ic': round(holdout_mean, 6), 'holdout_residual_icir': round(holdout_icir, 6) if np.isfinite(holdout_icir) else None,
            'passed_holdout': bool(np.isfinite(holdout_mean) and holdout_mean >= es.EP.PASS_MIN_IC),
            'n_search': int(n_search), 'n_holdout': int(n_holdout),
            'direction': 'raw' if direction > 0 else 'negated'}


def run_mode(mode: str, seed: int, budget: int, panel: pd.DataFrame, feature_pool: list[str],
             targets: dict, generations: int = 3) -> list[dict]:
    import random
    rng = random.Random(seed)
    results: list[dict] = []
    seen: set[str] = set()

    def add(expr: dict) -> None:
        formula = es.register(expr)
        if formula in seen:
            return
        seen.add(formula)
        record = eval_candidate(expr, panel, targets)
        if record:
            results.append(record)

    if mode == 'random':
        while len(results) < budget:
            add(es.random_expr(rng, feature_pool))
            if len(seen) > budget * 20:
                break
        return results[:budget]
    population = [es.random_expr(rng, feature_pool) for _ in range(min(20, budget))]
    for generation in range(generations):
        start = len(results)
        for expr in population:
            add(expr)
        scored = sorted(results[start:], key=lambda row: -row['fitness_search'])[:5]
        elites = [es.expr_lookup(row['formula']) for row in scored]
        elites = [expr for expr in elites if expr is not None]
        if generation == generations - 1:
            break
        children = []
        while len(children) < 15:
            roll = rng.random()
            if roll < 0.5 and elites:
                children.append(es.mutate(rng.choice(elites), rng, feature_pool))
            elif len(elites) >= 2:
                children.append(es.crossover(rng.choice(elites), rng.choice(elites), rng))
            else:
                children.append(es.random_expr(rng, feature_pool))
        population = children
    while len(results) < budget:
        add(es.random_expr(rng, feature_pool))
        if len(seen) > budget * 20:
            break
    return results[:budget]


def summarize(rows: list[dict], top_k: int = 5) -> dict:
    ordered = sorted(rows, key=lambda row: -row['fitness_search'])
    deduped, keys = [], set()
    for row in ordered:
        key = (round(row['search_ic'], 6), round(row['search_icir'] or 0.0, 6))
        if key in keys:
            continue
        keys.add(key)
        deduped.append(row)
    top = deduped[:top_k]
    holdout = [row['holdout_residual_ic'] for row in top if np.isfinite(row['holdout_residual_ic'])]
    return {'n_valid': len(rows),
            'top5_search_fitness': round(float(np.mean([row['fitness_search'] for row in top])), 6) if top else None,
            'top5_holdout_residual_ic': round(float(np.mean(holdout)), 6) if holdout else None,
            'pass_holdout_count': sum(1 for row in rows if row['passed_holdout']),
            'top5': [{'formula': row['formula'], 'search_ic': row['search_ic'],
                      'holdout_residual_ic': row['holdout_residual_ic']} for row in top]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--budget', type=int, default=60)
    parser.add_argument('--generations', type=int, default=3)
    parser.add_argument('--holdout-fraction', type=float, default=0.3)
    parser.add_argument('--claim', action='store_true')
    args = parser.parse_args()
    panel = es.build_panel()
    feature_pool = es.features(panel)
    targets = build_targets(panel, feature_pool, args.holdout_fraction)
    random_rows = run_mode('random', args.seed, args.budget, panel, feature_pool, targets, args.generations)
    evolution_rows = run_mode('evolution', args.seed + 1000, args.budget, panel, feature_pool, targets, args.generations)
    baseline_rows = []
    for feature in feature_pool:
        record = eval_candidate({'kind': 'leaf', 'feature': feature}, panel, targets)
        if record:
            baseline_rows.append(record)
    random_stats = summarize(random_rows)
    evolution_stats = summarize(evolution_rows)
    baseline_stats = summarize(baseline_rows)
    baseline_top5 = baseline_stats['top5_holdout_residual_ic']
    best_generated = max([value for value in (random_stats['top5_holdout_residual_ic'],
                                               evolution_stats['top5_holdout_residual_ic']) if value is not None],
                         default=None)
    delta_vs_baseline = round(best_generated - baseline_top5, 6) if (best_generated is not None and baseline_top5 is not None) else None
    verdict = 'generated_better_incremental' if (delta_vs_baseline is not None and delta_vs_baseline > 0) else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-INCREMENTAL-1', 'created_at': now(),
               'seed': args.seed, 'budget_per_mode': args.budget,
               'search_dates': [str(targets['search_dates'][0].date()), str(targets['search_dates'][-1].date())],
               'holdout_dates': [str(targets['holdout_dates'][0].date()), str(targets['holdout_dates'][-1].date())],
               'random': random_stats, 'evolution': evolution_stats,
               'baseline_existing_features': baseline_stats,
               'delta_best_generated_vs_baseline': delta_vs_baseline,
               'verdict': verdict,
               'rule': 'generated better if best generated top5 holdout residual IC > existing-feature baseline top5 + 0.005',
               'score_window_read': False}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f'incremental_random_seed{args.seed}.json').write_text(json.dumps(random_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'incremental_evolution_seed{args.seed}.json').write_text(json.dumps(evolution_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'incremental_result_seed{args.seed}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    RESULT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'verdict': verdict, 'random': {k: v for k, v in random_stats.items() if k != 'top5'},
                      'evolution': {k: v for k, v in evolution_stats.items() if k != 'top5'},
                      'baseline': {k: v for k, v in baseline_stats.items() if k != 'top5'},
                      'delta_vs_baseline': delta_vs_baseline}, ensure_ascii=False, indent=1))
    if args.claim:
        import claim_ledger
        import review_claims
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_incremental',
                'claims': [{'claim_id': f'CLM-EXPR-INCR-{args.seed:03d}', 'type': 'experimental_result',
                            'statement': (f"增量（残差目标）表达式搜索（seed {args.seed}，search {payload['search_dates'][0]}.."
                                          f"{payload['search_dates'][1]}，holdout {payload['holdout_dates'][0]}..{payload['holdout_dates'][1]}）："
                                          f"evolution top5 holdout residual IC = {evolution_stats['top5_holdout_residual_ic']}，"
                                          f"random = {random_stats['top5_holdout_residual_ic']}，baseline（现有特征）= {baseline_top5}；"
                                          f"best generated vs baseline Δ = {delta_vs_baseline}；verdict={verdict}。"),
                            'evidence': [f'auto_research/expr_search/results/incremental_result_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/incremental_random_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/incremental_evolution_seed{args.seed}.json',
                                         'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                            'run_refs': ['auto_research/expr_search'],
                            'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        path = OUT / f'claim_spec_incremental_seed{args.seed}.json'
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
