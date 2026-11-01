#!/usr/bin/env python3
"""Library-seeded proposal arm: mutate known good expressions from expr_library and re-evaluate."""
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
from agents.expr_grammar import parse, validate  # noqa: E402

OUT = ROOT / 'auto_research/agents/experiments'
LIB = ROOT / 'auto_research/expr_library/top_expressions_seed1.json'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--parents', type=int, default=3)
    parser.add_argument('--mutations', type=int, default=12)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--exclude-parents', action='store_true')
    parser.add_argument('--split-frac', type=float, default=0.7)
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * args.split_frac)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    library = json.loads(LIB.read_text(encoding='utf-8'))
    parents = []
    for row in sorted(library, key=lambda item: -item.get('fitness', -9))[:args.parents]:
        try:
            node = parse(row['formula'], set(features)); validate(node, set(features))
        except Exception:  # noqa: BLE001
            continue
        parents.append({'formula': row['formula'], 'node': node})
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    parent_text = '\n'.join(f"- {sanitize.to_alias(row['formula'], mapping) if mapping else row['formula']}" for row in parents)
    prompt = (f'You are a factor researcher. Propose exactly {args.mutations} NEW expressions that are '
              'meaningful mutations/crossovers of the following high-quality expressions (change one operator, '
              'horizon, or combine two structures). Use the same grammar. Do not repeat parents.\n'
              f'Parents:\n{parent_text}\nExperiment seed: {args.seed}. Output ONLY a JSON array of objects with keys expression, rationale.')
    raw = llm.chat([{'role': 'user', 'content': prompt}], model=args.model, temperature=0.4, max_tokens=4000)
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
            node = parse(expression, set(features)); validate(node, set(features))
        except Exception:  # noqa: BLE001
            continue
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            mutated.append({'formula': es.canonical(node), 'node': node, 'search_fitness': result['fitness']})
        if len(mutated) >= args.mutations:
            break
    # also evaluate the parents themselves as part of the arm
    parent_rows = []
    for row in parents:
        result = es.evaluate_expr(row['node'], panel, search_dates)
        if result:
            parent_rows.append({'formula': row['formula'], 'node': row['node'], 'search_fitness': result['fitness']})
    base_rows = [] if args.exclude_parents else parent_rows
    all_rows = sorted(base_rows + mutated, key=lambda row: -row['search_fitness'])
    holdout = []
    for row in all_rows[:5]:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            holdout.append({'fitness': result['fitness'], 'ic': result['mean_ic']})
    lib_top5 = float(np.mean([row['fitness'] for row in holdout])) if holdout else None
    random_rows = es.run_search('random', args.seed, args.mutations, panel, features, search_dates)
    random_holdout = []
    for row in sorted(random_rows, key=lambda item: -item['fitness'])[:5]:
        node = es.expr_lookup(row['formula'])
        result = es.evaluate_expr(node, panel, holdout_dates) if node else None
        if result:
            random_holdout.append({'fitness': result['fitness']})
    random_top5 = float(np.mean([row['fitness'] for row in random_holdout])) if random_holdout else None
    rng2 = random.Random(args.seed * 17 + 5)
    random_mut = []
    for _ in range(args.mutations):
        parent = random.choice(parents)
        node = es.mutate(parent['node'], rng2, features)
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            random_mut.append({'node': node, 'search_fitness': result['fitness']})
    random_mut.sort(key=lambda row: -row['search_fitness'])
    mut_holdout = []
    for row in random_mut[:5]:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            mut_holdout.append(result['fitness'])
    random_mutation_top5 = float(np.mean(mut_holdout)) if mut_holdout else None
    payload = {'schema': 'AUTO-RESEARCH-AGENT-LIBRARY-1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'provider': llm.PROVIDER, 'model': args.model, 'seed': args.seed,
               'n_parents': len(parents), 'n_mutated': len(mutated), 'exclude_parents': args.exclude_parents, 'split_frac': args.split_frac,
               'library_top5_holdout_fitness': lib_top5, 'random_top5_holdout_fitness': random_top5,
               'delta_vs_random': (lib_top5 - random_top5) if (lib_top5 is not None and random_top5 is not None) else None,
               'random_mutation_top5_holdout_fitness': random_mutation_top5,
               'delta_vs_random_mutation': (lib_top5 - random_mutation_top5) if (lib_top5 is not None and random_mutation_top5 is not None) else None,
               'parents': [row['formula'] for row in parents], 'mutated': [row['formula'] for row in mutated],
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'library_{llm.PROVIDER}_{args.model.replace("/", "_")}_split{args.split_frac}_seed{args.seed}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('model', 'seed', 'n_parents', 'n_mutated',
                                                    'library_top5_holdout_fitness', 'random_top5_holdout_fitness',
                                                    'delta_vs_random')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
