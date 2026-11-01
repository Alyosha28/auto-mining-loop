#!/usr/bin/env python3
"""Hybrid arm: evaluate a few random expressions, then ask the LLM to mutate the best ones."""
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--random-k', type=int, default=6)
    parser.add_argument('--mutations', type=int, default=6)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * 0.7)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    total = args.random_k + args.mutations
    rng = random.Random(args.seed)
    seed_rows = []
    for _ in range(args.random_k):
        node = es.random_expr(rng, features)
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            seed_rows.append({'formula': es.canonical(node), 'node': node, 'search_fitness': result['fitness']})
    seed_rows.sort(key=lambda row: -row['search_fitness'])
    parents = seed_rows[:3]
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    parent_text = '\n'.join(f"- {sanitize.to_alias(row['formula'], mapping) if mapping else row['formula']} (fitness {row['search_fitness']:.4f})" for row in parents)
    prompt = (f'You are a factor researcher. Improve the following expressions by mutation/crossover using the same grammar. '
              f'Propose exactly {args.mutations} NEW expressions, each a meaningful variation of one parent '
              '(change operator, horizon, or combine two parents); do not repeat parents.\nParents:\n' + parent_text +
              '\nOutput ONLY a JSON array of objects with keys expression, rationale.')
    raw = llm.chat([{'role': 'user', 'content': prompt}], model=args.model, temperature=0.3, max_tokens=4000)
    items = []
    for value in llm.extract_all_json(raw):
        if isinstance(value, list):
            items.extend(value)
        elif isinstance(value, dict):
            inner = value.get('hypotheses') or value.get('proposals')
            items.extend(inner if isinstance(inner, list) else [value])
    mutated = []
    for item in items:
        if not isinstance(item, dict):
            continue
        expression = str(item.get('expression', ''))
        if mapping:
            expression = sanitize.from_alias(expression, mapping)
        try:
            node = es.parse_expression(expression) if hasattr(es, 'parse_expression') else None
        except Exception:  # noqa: BLE001
            node = None
        if node is None:
            try:
                from agents.expr_grammar import parse, validate
                node = parse(expression, set(features)); validate(node, set(features))
            except Exception:  # noqa: BLE001
                continue
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            mutated.append({'formula': es.canonical(node), 'node': node, 'search_fitness': result['fitness']})
        if len(mutated) >= args.mutations:
            break
    all_rows = seed_rows + mutated
    all_rows.sort(key=lambda row: -row['search_fitness'])
    holdout = []
    for row in all_rows[:5]:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            holdout.append({'fitness': result['fitness'], 'ic': result['mean_ic']})
    hybrid_top5 = float(np.mean([row['fitness'] for row in holdout])) if holdout else None
    random_rows = es.run_search('random', args.seed, total, panel, features, search_dates)
    random_holdout = []
    for row in sorted(random_rows, key=lambda item: -item['fitness'])[:5]:
        node = es.expr_lookup(row['formula'])
        result = es.evaluate_expr(node, panel, holdout_dates) if node else None
        if result:
            random_holdout.append({'fitness': result['fitness']})
    random_top5 = float(np.mean([row['fitness'] for row in random_holdout])) if random_holdout else None
    payload = {'schema': 'AUTO-RESEARCH-AGENT-MUTATE-1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'provider': llm.PROVIDER, 'model': args.model, 'seed': args.seed,
               'random_k': args.random_k, 'mutations': args.mutations,
               'n_seed': len(seed_rows), 'n_mutated': len(mutated),
               'hybrid_top5_holdout_fitness': hybrid_top5, 'random_top5_holdout_fitness': random_top5,
               'delta_vs_random': (hybrid_top5 - random_top5) if (hybrid_top5 is not None and random_top5 is not None) else None,
               'parents': [row['formula'] for row in parents], 'mutated': [row['formula'] for row in mutated],
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'mutate_{llm.PROVIDER}_{args.model.replace("/", "_")}_seed{args.seed}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('model', 'seed', 'n_seed', 'n_mutated',
                                                    'hybrid_top5_holdout_fitness', 'random_top5_holdout_fitness',
                                                    'delta_vs_random')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
