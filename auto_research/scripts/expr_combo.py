#!/usr/bin/env python3
"""Combination search: rank-average combinations of existing panel features.

Tests whether feedback-driven search over *combinations* can beat the best single
existing factor on the dev holdout. Score window not read.
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

OUT = ROOT / 'auto_research/expr_search'
RESULTS = OUT / 'results'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_rank_matrix(panel: pd.DataFrame, feature_pool: list[str]) -> pd.DataFrame:
    ranked = panel.groupby('date')[feature_pool].rank(pct=True)
    return ranked.astype('float32').fillna(0.5)


def combo_values(ranked: pd.DataFrame, features: tuple[str, ...]) -> pd.Series:
    return ranked[list(features)].mean(axis=1)


def evaluate_values(panel: pd.DataFrame, values: pd.Series, dates) -> dict | None:
    values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5000:
        return None
    series, n_dates = es.EP.ic_series(panel, values, dates)
    if n_dates < 20 or len(series) < 20:
        return None
    mean_ic = float(series.mean())
    std = float(series.std(ddof=1))
    icir = mean_ic / std if std > 0 else float('nan')
    direction = 1.0 if mean_ic >= 0 else -1.0
    values = values * direction
    mean_ic = abs(mean_ic)
    icir_abs = abs(icir) if np.isfinite(icir) else float('nan')
    turnover = es.EP._turnover_proxy(panel, [{'values': values}], dates)
    fitness = mean_ic + 0.3 * (icir_abs if np.isfinite(icir_abs) else 0.0) - 0.1 * (turnover if np.isfinite(turnover) else 0.0)
    return {'mean_ic': round(mean_ic, 6), 'icir': round(icir_abs, 6) if np.isfinite(icir_abs) else None,
            'turnover_proxy': round(float(turnover), 6) if np.isfinite(turnover) else None,
            'fitness': round(fitness, 6), 'n_dates': int(n_dates),
            'coverage': round(n_dates / len(dates), 4)}


def eval_combo(ranked: pd.DataFrame, features: tuple[str, ...], panel, dates) -> dict | None:
    return evaluate_values(panel, combo_values(ranked, features), dates)


def random_combo(rng: random.Random, pool: list[str]) -> tuple[str, ...]:
    size = rng.choice([2, 2, 3, 3, 4, 5])
    return tuple(sorted(rng.sample(pool, size)))


def mutate_combo(combo: tuple[str, ...], rng: random.Random, pool: list[str]) -> tuple[str, ...]:
    features = list(combo)
    action = rng.choice(['add', 'remove', 'swap'])
    if action == 'add' and len(features) < 5:
        feature = rng.choice([name for name in pool if name not in features])
        features.append(feature)
    elif action == 'remove' and len(features) > 2:
        features.remove(rng.choice(features))
    else:
        features[rng.randrange(len(features))] = rng.choice(pool)
    return tuple(sorted(set(features)))


def run_mode(mode: str, seed: int, budget: int, ranked: pd.DataFrame, panel, pool: list[str],
             search_dates, generations: int = 3) -> list[dict]:
    rng = random.Random(seed)
    results: list[dict] = []
    seen: set[tuple[str, ...]] = set()

    def add(combo: tuple[str, ...]) -> None:
        if combo in seen:
            return
        seen.add(combo)
        record = eval_combo(ranked, combo, panel, search_dates)
        if record:
            results.append({'features': list(combo), **record})

    if mode == 'random':
        while len(results) < budget:
            add(random_combo(rng, pool))
            if len(seen) > budget * 20:
                break
        return results[:budget]
    population = [random_combo(rng, pool) for _ in range(min(20, budget))]
    for generation in range(generations):
        start = len(results)
        for combo in population:
            add(combo)
        scored = sorted(results[start:], key=lambda row: -row['fitness'])[:5]
        if generation == generations - 1:
            break
        children = []
        while len(children) < 15:
            parent = tuple(rng.choice(scored)['features']) if scored else random_combo(rng, pool)
            children.append(mutate_combo(parent, rng, pool))
        population = children
    while len(results) < budget:
        add(random_combo(rng, pool))
        if len(seen) > budget * 20:
            break
    return results[:budget]


def summarize(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda row: -row['fitness'])
    top = ordered[:5]
    return {'n_valid': len(rows),
            'top5_search_fitness': round(float(np.mean([row['fitness'] for row in top])), 6) if top else None,
            'top5_features': [row['features'] for row in top]}


def holdout_stats(ranked, panel, rows: list[dict], holdout_dates) -> dict:
    ordered = sorted(rows, key=lambda row: -row['fitness'])[:5]
    holdout = []
    for row in ordered:
        record = eval_combo(ranked, tuple(row['features']), panel, holdout_dates)
        if record:
            holdout.append({'features': row['features'], **record})
    return {'n': len(holdout),
            'top5_holdout_fitness': round(float(np.mean([row['fitness'] for row in holdout])), 6) if holdout else None,
            'top5_holdout_ic': round(float(np.mean([row['mean_ic'] for row in holdout])), 6) if holdout else None,
            'details': holdout}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--budget', type=int, default=60)
    parser.add_argument('--generations', type=int, default=3)
    parser.add_argument('--holdout-fraction', type=float, default=0.3)
    parser.add_argument('--claim', action='store_true')
    parser.add_argument('--holdout-start', default='',
                        help='if set, search dates < holdout-start and holdout >= holdout-start (YYYY-MM-DD)')
    args = parser.parse_args()
    tag = args.holdout_start.replace('-', '') if args.holdout_start else ''
    panel = es.build_panel()
    pool = es.features(panel)
    ranked = make_rank_matrix(panel, pool)
    all_dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    if args.holdout_start:
        boundary = pd.Timestamp(args.holdout_start)
        search_dates = all_dates[all_dates < boundary]
        holdout_dates = all_dates[all_dates >= boundary]
    else:
        cut = max(20, int(round(len(all_dates) * (1.0 - args.holdout_fraction))))
        search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    random_rows = run_mode('random', args.seed, args.budget, ranked, panel, pool, search_dates, args.generations)
    evolution_rows = run_mode('evolution', args.seed + 1000, args.budget, ranked, panel, pool, search_dates, args.generations)
    baseline_rows = []
    for feature in pool:
        record = eval_combo(ranked, (feature,), panel, search_dates)
        if record:
            baseline_rows.append({'features': [feature], **record})
    random_stats = summarize(random_rows)
    evolution_stats = summarize(evolution_rows)
    baseline_stats = summarize(baseline_rows)
    random_holdout = holdout_stats(ranked, panel, random_rows, holdout_dates)
    evolution_holdout = holdout_stats(ranked, panel, evolution_rows, holdout_dates)
    baseline_holdout = holdout_stats(ranked, panel, baseline_rows, holdout_dates)
    best_generated = max([value for value in (random_holdout['top5_holdout_fitness'],
                                               evolution_holdout['top5_holdout_fitness']) if value is not None],
                         default=None)
    delta = round(best_generated - baseline_holdout['top5_holdout_fitness'], 6) if (best_generated is not None and baseline_holdout['top5_holdout_fitness'] is not None) else None
    verdict = 'supported' if (delta is not None and delta > 0.005) else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-COMBO-1', 'created_at': now(), 'seed': args.seed,
               'budget_per_mode': args.budget,
               'search_dates': [str(search_dates[0].date()), str(search_dates[-1].date())],
               'holdout_dates': [str(holdout_dates[0].date()), str(holdout_dates[-1].date())],
               'random': random_stats, 'evolution': evolution_stats,
               'baseline_single_features': baseline_stats,
               'random_holdout': random_holdout, 'evolution_holdout': evolution_holdout,
               'baseline_holdout': baseline_holdout,
               'delta_best_generated_vs_baseline': delta, 'verdict': verdict,
               'rule': 'supported if best generated top5 holdout fitness > best-single-feature baseline top5 + 0.005',
               'score_window_read': False}
    RESULTS.mkdir(parents=True, exist_ok=True)
    prefix = f'combo_{tag}_' if tag else 'combo_'
    (RESULTS / f'{prefix}random_seed{args.seed}.json').write_text(json.dumps(random_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'{prefix}evolution_seed{args.seed}.json').write_text(json.dumps(evolution_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'{prefix}result_seed{args.seed}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (OUT / f'combo_result{("_" + tag) if tag else ""}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'verdict': verdict, 'random': random_holdout['top5_holdout_fitness'],
                      'evolution': evolution_holdout['top5_holdout_fitness'],
                      'baseline': baseline_holdout['top5_holdout_fitness'], 'delta': delta},
                     ensure_ascii=False, indent=1))
    if args.claim:
        import claim_ledger
        import review_claims
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_combo',
                'claims': [{'claim_id': f'CLM-EXPR-COMBO-{args.seed:03d}' + (f'-{tag}' if tag else ''), 'type': 'experimental_result',
                            'statement': (f"组合搜索（seed {args.seed}，search {payload['search_dates'][0]}..{payload['search_dates'][1]}，"
                                          f"holdout {payload['holdout_dates'][0]}..{payload['holdout_dates'][1]}）："
                                          f"evolution top5 holdout fitness = {evolution_holdout['top5_holdout_fitness']}，"
                                          f"random = {random_holdout['top5_holdout_fitness']}，baseline 单因子 = {baseline_holdout['top5_holdout_fitness']}；"
                                          f"best generated vs baseline Δ = {delta}；verdict={verdict}。"),
                            'evidence': [f'auto_research/expr_search/results/{prefix}result_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/{prefix}random_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/{prefix}evolution_seed{args.seed}.json',
                                         'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                            'run_refs': ['auto_research/expr_search'],
                            'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        path = OUT / f'claim_spec_combo_seed{args.seed}.json'
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
