#!/usr/bin/env python3
"""LLM reranker arm: build a random expression pool, let the LLM select a subset,
then evaluate the selected expressions under the same budget as random search."""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from agents import llm, sanitize  # noqa: E402

OUT = ROOT / 'auto_research/agents/experiments'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--pool', type=int, default=48)
    parser.add_argument('--select', type=int, default=12)
    parser.add_argument('--mix-random', type=int, default=0, help='evaluate K random candidates plus select-K LLM picks')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--claim', action='store_true')
    parser.add_argument('--claim-id', default=None)
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * 0.7)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    rng = random.Random(args.seed)
    pool = []
    seen = set()
    while len(pool) < args.pool:
        node = es.random_expr(rng, features)
        formula = es.canonical(node)
        if formula in seen:
            continue
        seen.add(formula)
        pool.append({'formula': formula, 'node': node})
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    lines = []
    for index, row in enumerate(pool):
        formula = sanitize.to_alias(row['formula'], mapping) if mapping else row['formula']
        lines.append(f'{index}: {formula}')
    prompt = ('You are a cross-sectional factor researcher. From the candidate expressions below, '
              f'select exactly {args.select - args.mix_random} that are most likely to have high dev-window rank IC/ICIR '
              'and low redundancy. Prefer economically motivated structures and diversity across families. '
              'Output ONLY a JSON array of integer indices.\n\nCandidates:\n' + '\n'.join(lines))
    raw = llm.chat([{'role': 'user', 'content': prompt}], model=args.model, temperature=0.0, max_tokens=4000)
    values = llm.extract_all_json(raw)
    indices = []
    for value in values:
        if isinstance(value, list):
            indices.extend(value)
        elif isinstance(value, dict):
            for key in ('indices', 'selection', 'selected', 'items', 'selected_indices', 'ranking', 'choices'):
                if isinstance(value.get(key), list):
                    indices.extend(value[key])
    indices = [int(item) for item in indices if isinstance(item, (int, float)) and 0 <= int(item) < len(pool)]
    indices = list(dict.fromkeys(indices))[:max(0, args.select - args.mix_random)]
    selection_source = 'llm'
    if len(indices) < max(0, args.select - args.mix_random):
        # diverse deterministic fallback; do NOT reuse the random arm's first N
        selection_source = 'fallback'
        step = max(1, len(pool) // max(1, args.select))
        for index in range(0, len(pool), step):
            if index not in indices:
                indices.append(index)
            if len(indices) >= max(0, args.select - args.mix_random):
                break
    selected = []
    if args.mix_random:
        for row in pool[:args.mix_random]:
            result = es.evaluate_expr(row['node'], panel, search_dates)
            if result:
                selected.append({**row, 'search_fitness': result['fitness'], 'source': 'random'})
    for index in indices:
        row = pool[index]
        result = es.evaluate_expr(row['node'], panel, search_dates)
        if result:
            selected.append({**row, 'search_fitness': result['fitness'], 'source': 'llm'})
    selected.sort(key=lambda row: -row['search_fitness'])
    top = selected[:5]
    holdout = []
    for row in top:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            holdout.append({'formula': row['formula'], 'fitness': result['fitness'], 'ic': result['mean_ic']})
    rerank_top5 = float(np.mean([row['fitness'] for row in holdout])) if holdout else None
    random_rows = es.run_search('random', args.seed, args.select, panel, features, search_dates)
    random_holdout = []
    for row in sorted(random_rows, key=lambda item: -item['fitness'])[:5]:
        node = es.expr_lookup(row['formula'])
        result = es.evaluate_expr(node, panel, holdout_dates) if node else None
        if result:
            random_holdout.append({'fitness': result['fitness'], 'ic': result['mean_ic']})
    random_top5 = float(np.mean([row['fitness'] for row in random_holdout])) if random_holdout else None
    payload = {'schema': 'AUTO-RESEARCH-AGENT-RERANK-1', 'created_at': now(),
               'provider': llm.PROVIDER, 'model': args.model, 'seed': args.seed,
               'pool': args.pool, 'select': args.select,
               'n_selected': len(indices), 'n_evaluated': len(selected), 'mix_random': args.mix_random,
               'selection_source': selection_source, 'raw_response': raw[:800],
               'rerank_top5_holdout_fitness': rerank_top5,
               'random_top5_holdout_fitness': random_top5,
               'delta_vs_random': (rerank_top5 - random_top5) if (rerank_top5 is not None and random_top5 is not None) else None,
               'selected_formulas': [row['formula'] for row in selected],
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'rerank_{llm.PROVIDER}_{args.model.replace("/", "_")}_seed{args.seed}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('model', 'seed', 'n_selected', 'n_evaluated',
                                                    'rerank_top5_holdout_fitness', 'random_top5_holdout_fitness',
                                                    'delta_vs_random')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
