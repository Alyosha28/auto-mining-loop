#!/usr/bin/env python3
"""Feedback-driven factor expression search vs random search (dev-window A/B).

Builds expressions over local panel primitives, evaluates them on the dev window
(2017-01-01..2020-11-30) with the same probe primitives used by the evolution
harness, and compares a 3-generation feedback loop against random search at an
equal evaluation budget. The score window is never loaded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/expr_search'
CACHE = OUT / 'cache/dev_panel.parquet'
LIB = ROOT / 'auto_research/expr_library'
BASE_PANEL = ROOT / 'data_processed/local_quote_library_2017_2025_v1'
PV_PANEL = ROOT / 'data_processed/price_volume_features_2016_2025_v1'
DEV_START, DEV_END = '2017-01-01', '2020-11-30'
UNARY_OPS = ['rank', 'zscore', 'winsorize', 'vol_scaled', 'tanh', 'sign',
             'residual_size', 'delta4', 'delta13', 'delta_w', 'ema5', 'lag5', 'neg']
BINARY_OPS = ['ratio', 'diff', 'rank_minus', 'product']
META_COLUMNS = {'date', 'symbol', 'research_mask', 'forward_return', 'log_market_cap', 'universe_reason',
                'label_reason', 'label_entry_date', 'label_exit_date', 'status_source_date',
                'observed_listing_age'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_evo_probe():
    path = ROOT / 'skills/current/factor-evolution-harness2/scripts/evo_probe.py'
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('evo_probe_expr', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EP = load_evo_probe()


def panel_columns(panel_dir: Path) -> list[str]:
    files = EP._panel_files(panel_dir)
    import pyarrow.parquet as pq
    return pq.read_schema(files[0]).names


def build_panel(force: bool = False) -> pd.DataFrame:
    if CACHE.is_file() and not force:
        return pd.read_parquet(CACHE)
    base_columns = panel_columns(BASE_PANEL)
    local = EP.load_panel(BASE_PANEL, base_columns, DEV_START, DEV_END)
    pv_columns = [name for name in panel_columns(PV_PANEL) if name.startswith('pv_')]
    pv_columns = sorted(pv_columns)[::4][:56]  # deterministic spread over the pv library
    pv = EP.load_panel(PV_PANEL, pv_columns, DEV_START, DEV_END)
    merged = local.merge(pv, on=['date', 'symbol'], how='left')
    merged = merged.drop_duplicates(['date', 'symbol']).sort_values(['date', 'symbol']).reset_index(drop=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(CACHE, index=False)
    return merged


def features(panel: pd.DataFrame) -> list[str]:
    return [name for name in panel.columns if name not in META_COLUMNS]


# ------------------------------------------------------------------ expressions
# An expression is a dict: {'kind': 'leaf', 'feature': str} or
# {'kind': 'unary', 'op': str, 'arg': expr} or
# {'kind': 'binary', 'op': str, 'left': expr, 'right': expr}

def canonical(expr: dict) -> str:
    kind = expr['kind']
    if kind == 'leaf':
        return expr['feature']
    if kind == 'unary':
        return f"{expr['op']}({canonical(expr['arg'])})"
    return f"{expr['op']}({canonical(expr['left'])},{canonical(expr['right'])})"


def random_expr(rng: random.Random, feature_pool: list[str]) -> dict:
    def unary(feature: str) -> dict:
        expr = {'kind': 'leaf', 'feature': feature}
        for _ in range(rng.choice([1, 1, 2])):
            expr = {'kind': 'unary', 'op': rng.choice(UNARY_OPS), 'arg': expr}
        return expr

    if rng.random() < 0.55:
        left = unary(rng.choice(feature_pool))
        right = unary(rng.choice(feature_pool))
        expr = {'kind': 'binary', 'op': rng.choice(BINARY_OPS), 'left': left, 'right': right}
        if rng.random() < 0.5:
            expr = {'kind': 'unary', 'op': rng.choice(UNARY_OPS), 'arg': expr}
        return expr
    return unary(rng.choice(feature_pool))


def mutate(expr: dict, rng: random.Random, feature_pool: list[str]) -> dict:
    action = rng.choice(['wrap', 'replace_feature', 'change_op', 'leaf'])
    if action == 'wrap':
        return {'kind': 'unary', 'op': rng.choice(UNARY_OPS), 'arg': expr}
    if action == 'leaf' and expr['kind'] == 'leaf':
        return {'kind': 'leaf', 'feature': rng.choice(feature_pool)}
    if action == 'change_op' and expr['kind'] == 'unary':
        return {'kind': 'unary', 'op': rng.choice(UNARY_OPS), 'arg': expr['arg']}
    if action == 'change_op' and expr['kind'] == 'binary':
        return {'kind': 'binary', 'op': rng.choice(BINARY_OPS), 'left': expr['left'], 'right': expr['right']}
    # replace a random leaf inside the tree
    if expr['kind'] == 'leaf':
        return {'kind': 'leaf', 'feature': rng.choice(feature_pool)}
    return expr


def crossover(left: dict, right: dict, rng: random.Random) -> dict:
    op = rng.choice(['rank_minus', 'ratio'])
    return {'kind': 'binary', 'op': op, 'left': left, 'right': right}


def evaluate_expr(expr: dict, panel: pd.DataFrame, eval_dates) -> dict | None:
    values = eval_tree(expr, panel)
    if values is None:
        return None
    values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 5000:
        return None
    series, n_dates = EP.ic_series(panel, values, eval_dates)
    if n_dates < 20 or len(series) < 20:
        return None
    mean_ic = float(series.mean())
    icir = float(series.mean() / series.std(ddof=1)) if series.std(ddof=1) > 0 else float('nan')
    direction = 1.0 if mean_ic >= 0 else -1.0
    values = values * direction
    mean_ic = abs(mean_ic)
    icir_abs = abs(icir) if np.isfinite(icir) else float('nan')
    passed = bool(mean_ic >= EP.PASS_MIN_IC and np.isfinite(icir_abs) and icir_abs >= EP.PASS_MIN_ICIR)
    turnover = EP._turnover_proxy(panel, [{'values': values}], eval_dates)
    fitness = mean_ic + 0.3 * (icir_abs if np.isfinite(icir_abs) else 0.0) - 0.1 * (turnover if np.isfinite(turnover) else 0.0)
    return {'formula': canonical(expr), 'mean_ic': round(mean_ic, 6), 'icir': round(icir_abs, 6),
            'turnover_proxy': round(float(turnover), 6) if np.isfinite(turnover) else None,
            'fitness': round(fitness, 6), 'passed': passed,
            'n_dates': int(n_dates), 'coverage': round(n_dates / len(eval_dates), 4),
            'direction': 'raw' if direction > 0 else 'negated',
            'signature': hashlib.sha256(canonical(expr).encode()).hexdigest()[:16]}


def eval_tree(expr: dict, panel: pd.DataFrame) -> pd.Series | None:
    kind = expr['kind']
    if kind == 'leaf':
        feature = expr['feature']
        return pd.to_numeric(panel[feature], errors='coerce') if feature in panel.columns else None
    if kind == 'unary':
        arg = eval_tree(expr['arg'], panel)
        if arg is None:
            return None
        return EP.apply_operator(panel, arg, expr['op'])
    left = eval_tree(expr['left'], panel)
    right = eval_tree(expr['right'], panel)
    if left is None or right is None:
        return None
    if expr['op'] == 'ratio':
        return left / right.replace(0, np.nan)
    if expr['op'] == 'diff':
        return left - right
    if expr['op'] == 'product':
        return left * right
    if expr['op'] == 'rank_minus':
        return EP._rank(left, panel['date']) - EP._rank(right, panel['date'])
    return None


def run_search(mode: str, seed: int, budget: int, panel: pd.DataFrame, feature_pool: list[str],
               eval_dates, generations: int = 3) -> list[dict]:
    rng = random.Random(seed)
    results: list[dict] = []
    seen: set[str] = set()

    def add(expr: dict) -> None:
        formula = register(expr)
        if formula in seen:
            return
        seen.add(formula)
        record = evaluate_expr(expr, panel, eval_dates)
        if record:
            results.append(record)

    if mode == 'random':
        while len(results) < budget:
            add(random_expr(rng, feature_pool))
            if len(seen) > budget * 10:
                break
        return results

    # evolution
    if mode == 'evolution':
        population = [random_expr(rng, feature_pool) for _ in range(min(20, budget))]
        for generation in range(generations):
            start = len(results)
            for expr in population:
                add(expr)
            scored = results[start:]
            if not scored:
                break
            scored_sorted = sorted(scored, key=lambda row: -row['fitness'])[:5]
            elites = [expr_lookup(row['formula']) for row in scored_sorted]
            elites = [expr for expr in elites if expr is not None]
            if generation == generations - 1:
                break
            children = []
            while len(children) < 15:
                roll = rng.random()
                if roll < 0.5 and elites:
                    children.append(mutate(rng.choice(elites), rng, feature_pool))
                elif len(elites) >= 2:
                    children.append(crossover(rng.choice(elites), rng.choice(elites), rng))
                else:
                    children.append(random_expr(rng, feature_pool))
            population = children
        while len(results) < budget:
            add(random_expr(rng, feature_pool))
            if len(seen) > budget * 20:
                break
        return results[:budget]


# expression objects are needed for mutation; keep a parsed registry
_REGISTRY: dict[str, dict] = {}


def register(expr: dict) -> str:
    formula = canonical(expr)
    _REGISTRY[formula] = expr
    return formula


def expr_lookup(formula: str) -> dict | None:
    return _REGISTRY.get(formula)




def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=1)
    parser.add_argument('--budget', type=int, default=60)
    parser.add_argument('--generations', type=int, default=3)
    parser.add_argument('--holdout-fraction', type=float, default=0.3)
    parser.add_argument('--claim', action='store_true')
    args = parser.parse_args()
    panel = build_panel()
    feature_pool = features(panel)
    all_dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * (1.0 - args.holdout_fraction))))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    random_results = run_search('random', args.seed, args.budget, panel, feature_pool, search_dates)
    evolution_results = run_search('evolution', args.seed + 1000, args.budget, panel, feature_pool,
                                   search_dates, args.generations)

    def holdout_stats(rows: list[dict]) -> dict:
        ordered = sorted(rows, key=lambda row: -row['fitness'])[:5]
        holdout = []
        for row in ordered:
            expr = expr_lookup(row['formula'])
            record = evaluate_expr(expr, panel, holdout_dates) if expr is not None else None
            if record:
                holdout.append(record)
        return {'n': len(holdout),
                'top5_mean_fitness': round(float(np.mean([row['fitness'] for row in holdout])), 6) if holdout else None,
                'details': holdout}

    def stats(rows: list[dict]) -> dict:
        ordered = sorted(rows, key=lambda row: -row['fitness'])
        deduped, keys = [], set()
        for row in ordered:
            key = (round(row['mean_ic'], 6), round(row['icir'], 6))
            if key in keys:
                continue
            keys.add(key)
            deduped.append(row)
        ordered = deduped
        top5 = ordered[:5]
        return {'n_valid': len(rows), 'top1_fitness': ordered[0]['fitness'] if ordered else None,
                'top5_mean_fitness': round(float(np.mean([row['fitness'] for row in top5])), 6) if top5 else None,
                'top5_mean_ic': round(float(np.mean([row['mean_ic'] for row in top5])), 6) if top5 else None,
                'pass_count': sum(1 for row in rows if row['passed']),
                'coverage_mean': round(float(np.mean([row['coverage'] for row in rows])), 4) if rows else None,
                'top5': [{'formula': row['formula'], 'fitness': row['fitness'], 'mean_ic': row['mean_ic'],
                          'icir': row['icir'], 'turnover': row['turnover_proxy']} for row in top5]}

    random_stats = stats(random_results)
    evolution_stats = stats(evolution_results)
    random_holdout = holdout_stats(random_results)
    evolution_holdout = holdout_stats(evolution_results)
    delta_holdout = round((evolution_holdout['top5_mean_fitness'] or 0.0) - (random_holdout['top5_mean_fitness'] or 0.0), 6)
    delta_top5 = round(evolution_stats['top5_mean_fitness'] - random_stats['top5_mean_fitness'], 6)
    supported = bool(delta_holdout >= 0.005 and evolution_stats['pass_count'] >= random_stats['pass_count'])
    verdict = 'supported' if supported else 'not_supported'
    payload = {'schema': 'AUTO-RESEARCH-EXPR-SEARCH-RESULT-1', 'created_at': now(),
               'seed': args.seed, 'budget_per_mode': args.budget, 'generations': args.generations,
               'dev_window': [DEV_START, DEV_END], 'n_features': len(feature_pool),
               'search_dates': [str(search_dates[0].date()), str(search_dates[-1].date())],
               'holdout_dates': [str(holdout_dates[0].date()), str(holdout_dates[-1].date())],
               'random': random_stats, 'evolution': evolution_stats,
               'random_holdout': random_holdout, 'evolution_holdout': evolution_holdout,
               'delta_top5_fitness': delta_top5, 'delta_holdout_fitness': delta_holdout,
               'verdict': verdict,
               'rule': 'supported if evolution top5 mean HOLDOUT fitness - random top5 mean HOLDOUT fitness >= 0.005 AND pass_count(evolution) >= pass_count(random)',
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    results_dir = OUT / 'results'
    results_dir.mkdir(exist_ok=True)
    (results_dir / f'random_seed{args.seed}.json').write_text(json.dumps(random_results, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (results_dir / f'evolution_seed{args.seed}.json').write_text(json.dumps(evolution_results, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (OUT / 'expr_search_result.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (results_dir / f'expr_search_result_seed{args.seed}.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    # independent expression library: top expressions from both modes
    LIB.mkdir(parents=True, exist_ok=True)
    top_entries = []
    for mode, rows in (('evolution', evolution_results), ('random', random_results)):
        for row in sorted(rows, key=lambda item: -item['fitness'])[:10]:
            top_entries.append({'mode': mode, 'seed': args.seed, **row})
    (LIB / f'top_expressions_seed{args.seed}.json').write_text(json.dumps(top_entries, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'verdict': verdict, 'random': {k: v for k, v in random_stats.items() if k != 'top5'},
                      'evolution': {k: v for k, v in evolution_stats.items() if k != 'top5'},
                      'delta_top5': delta_top5}, ensure_ascii=False, indent=1))
    if args.claim:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import claim_ledger
        import review_claims
        claim_spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'expr_search',
                      'claims': [{'claim_id': f'CLM-EXPR-SEARCH-{args.seed:03d}-HOLDOUT', 'type': 'experimental_result',
                                  'statement': (f"dev 窗表达式搜索 A/B（search {search_dates[0].date()}..{search_dates[-1].date()}，"
                                                f"holdout {holdout_dates[0].date()}..{holdout_dates[-1].date()}，budget {args.budget}/模式，seed {args.seed}）："
                                                f"进化的 top5 holdout 平均 fitness = {evolution_holdout['top5_mean_fitness']} "
                                                f"vs 随机 {random_holdout['top5_mean_fitness']}（Δ {delta_holdout}）；"
                                                f"pass_count {evolution_stats['pass_count']} vs {random_stats['pass_count']}；verdict={verdict}。"),
                                  'evidence': ['auto_research/docs/EXPR_SEARCH_CN.md',
                                               'auto_research/expr_search/expr_search_result.json',
                                               'auto_research/expr_search/PREREG_EXPR_SEARCH.json'],
                                  'run_refs': ['auto_research/expr_search'], 'preregistration': 'auto_research/PREREGISTRATION.json',
                                  'score_window_read': False}]}
        claim_path = OUT / f'claim_spec_seed{args.seed}.json'
        claim_path.write_text(json.dumps(claim_spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        added = claim_ledger.add_claims(claim_path)
        reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
        print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                          'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
