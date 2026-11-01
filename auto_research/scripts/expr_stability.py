#!/usr/bin/env python3
"""Stability-regularised expression search (third paradigm).

Selection objective: year-block consistency of the expression's dev-window IC
(mean |year IC| - 0.5 * std |year IC|), which targets the overfitting seen in v2.
Final comparison: standard dev fitness on the holdout window vs random search and
against existing panel features. Score window not read.
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


def year_consistency(panel: pd.DataFrame, values: pd.Series, search_dates: pd.DatetimeIndex) -> dict | None:
    series, n_dates = es.EP.ic_series(panel, values, search_dates)
    if n_dates < 20 or len(series) < 20:
        return None
    years = sorted(set(series.index.year))
    if len(years) < 2:
        return None
    yearly = []
    for year in years:
        block = series[series.index.year == year]
        if len(block) >= 8:
            yearly.append(abs(float(block.mean())))
    if len(yearly) < 2:
        return None
    mean_year = float(np.mean(yearly))
    std_year = float(np.std(yearly, ddof=1))
    return {'stability': mean_year - 0.5 * std_year, 'mean_abs_year_ic': mean_year,
            'std_year_ic': std_year, 'n_years': len(yearly), 'signed_mean': float(series.mean())}


def eval_candidate(expr: dict, panel: pd.DataFrame, search_dates, holdout_dates) -> dict | None:
    values = es.eval_tree(expr, panel)
    if values is None:
        return None
    values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5000:
        return None
    consistency = year_consistency(panel, values, search_dates)
    if consistency is None:
        return None
    # orient by signed search mean before holdout evaluation
    if consistency['signed_mean'] < 0:
        values = -values
    holdout_record = es.evaluate_expr(expr_or_leaf(expr), panel, holdout_dates)
    if holdout_record is None:
        return None
    return {'formula': es.canonical(expr), 'stability': round(consistency['stability'], 6),
            'mean_abs_year_ic': round(consistency['mean_abs_year_ic'], 6),
            'std_year_ic': round(consistency['std_year_ic'], 6), 'n_years': consistency['n_years'],
            'holdout_fitness': holdout_record['fitness'], 'holdout_mean_ic': holdout_record['mean_ic'],
            'holdout_pass': holdout_record['passed']}


def expr_or_leaf(expr: dict) -> dict:
    # evaluate_expr re-evaluates the tree; reuse directly
    return expr


def run_mode(mode: str, seed: int, budget: int, panel: pd.DataFrame, feature_pool: list[str],
             search_dates, holdout_dates, generations: int = 3) -> list[dict]:
    rng = random.Random(seed)
    results: list[dict] = []
    seen: set[str] = set()

    def add(expr: dict) -> None:
        formula = es.register(expr)
        if formula in seen:
            return
        seen.add(formula)
        record = eval_candidate(expr, panel, search_dates, holdout_dates)
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
        scored = sorted(results[start:], key=lambda row: -row['stability'])[:5]
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


def summarize(rows: list[dict]) -> dict:
    ordered = sorted(rows, key=lambda row: -row['stability'])
    deduped, keys = [], set()
    for row in ordered:
        key = (round(row['stability'], 6), round(row['holdout_mean_ic'], 6))
        if key in keys:
            continue
        keys.add(key)
        deduped.append(row)
    top = deduped[:5]
    return {'n_valid': len(rows),
            'top5_stability': round(float(np.mean([row['stability'] for row in top])), 6) if top else None,
            'top5_holdout_fitness': round(float(np.mean([row['holdout_fitness'] for row in top])), 6) if top else None,
            'top5_holdout_ic': round(float(np.mean([row['holdout_mean_ic'] for row in top])), 6) if top else None,
            'top5': [{'formula': row['formula'], 'stability': row['stability'],
                      'holdout_fitness': row['holdout_fitness'], 'holdout_ic': row['holdout_mean_ic']} for row in top]}


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
    all_dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * (1.0 - args.holdout_fraction))))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    random_rows = run_mode('random', args.seed, args.budget, panel, feature_pool, search_dates, holdout_dates, args.generations)
    evolution_rows = run_mode('evolution', args.seed + 1000, args.budget, panel, feature_pool, search_dates, holdout_dates, args.generations)
    baseline_rows = []
    for feature in feature_pool:
        record = eval_candidate({'kind': 'leaf', 'feature': feature}, panel, search_dates, holdout_dates)
        if record:
            baseline_rows.append(record)
    random_stats = summarize(random_rows)
    evolution_stats = summarize(evolution_rows)
    baseline_stats = summarize(baseline_rows)
    best_generated = max([value for value in (random_stats['top5_holdout_fitness'],
                                               evolution_stats['top5_holdout_fitness']) if value is not None],
                         default=None)
    delta_vs_baseline = round(best_generated - baseline_stats['top5_holdout_fitness'], 6) if (best_generated is not None and baseline_stats['top5_holdout_fitness'] is not None) else None
    verdict = 'supported' if (delta_vs_baseline is not None and delta_vs_baseline > 0.005) else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-STABILITY-1', 'created_at': now(),
               'seed': args.seed, 'budget_per_mode': args.budget,
               'search_dates': [str(search_dates[0].date()), str(search_dates[-1].date())],
               'holdout_dates': [str(holdout_dates[0].date()), str(holdout_dates[-1].date())],
               'random': random_stats, 'evolution': evolution_stats,
               'baseline_existing_features': baseline_stats,
               'delta_best_generated_vs_baseline': delta_vs_baseline,
               'verdict': verdict,
               'rule': 'supported if best generated top5 holdout fitness > existing-feature baseline top5 + 0.005',
               'score_window_read': False}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f'stability_random_seed{args.seed}.json').write_text(json.dumps(random_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'stability_evolution_seed{args.seed}.json').write_text(json.dumps(evolution_rows, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (RESULTS / f'stability_result_seed{args.seed}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (OUT / 'stability_result.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'verdict': verdict, 'random': {k: v for k, v in random_stats.items() if k != 'top5'},
                      'evolution': {k: v for k, v in evolution_stats.items() if k != 'top5'},
                      'baseline': {k: v for k, v in baseline_stats.items() if k != 'top5'},
                      'delta_vs_baseline': delta_vs_baseline}, ensure_ascii=False, indent=1))
    if args.claim:
        import claim_ledger
        import review_claims
        spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_stability',
                'claims': [{'claim_id': f'CLM-EXPR-STAB-{args.seed:03d}', 'type': 'experimental_result',
                            'statement': (f"稳定性正则化表达式搜索（seed {args.seed}，search {payload['search_dates'][0]}.."
                                          f"{payload['search_dates'][1]}，holdout {payload['holdout_dates'][0]}..{payload['holdout_dates'][1]}）："
                                          f"evolution top5 holdout fitness = {evolution_stats['top5_holdout_fitness']}，"
                                          f"random = {random_stats['top5_holdout_fitness']}，baseline（现有特征）= {baseline_stats['top5_holdout_fitness']}；"
                                          f"best generated vs baseline Δ = {delta_vs_baseline}；verdict={verdict}。"),
                            'evidence': [f'auto_research/expr_search/results/stability_result_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/stability_random_seed{args.seed}.json',
                                         f'auto_research/expr_search/results/stability_evolution_seed{args.seed}.json',
                                         'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                            'run_refs': ['auto_research/expr_search'],
                            'preregistration': 'auto_research/PREREGISTRATION.json',
                            'score_window_read': False}]}
        path = OUT / f'claim_spec_stability_seed{args.seed}.json'
        path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
