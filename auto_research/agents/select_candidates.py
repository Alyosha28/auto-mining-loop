#!/usr/bin/env python3
"""LLM final-stage selector: rank evaluated candidates by research judgment, not raw fitness."""
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
    parser.add_argument('--pool', type=int, default=12)
    parser.add_argument('--pick', type=int, default=5)
    parser.add_argument('--seed', type=int, default=7)
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * 0.7)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    rng = random.Random(args.seed)
    rows = []
    for _ in range(args.pool):
        node = es.random_expr(rng, features)
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            rows.append({'formula': es.canonical(node), 'node': node, 'search': result})
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    lines = []
    for index, row in enumerate(rows):
        result = row['search']
        formula = sanitize.to_alias(row['formula'], mapping) if mapping else row['formula']
        lines.append(f"{index}: {formula} | IC={result['mean_ic']:.4f} ICIR={result['icir']:.3f} "
                     f"turnover={(result['turnover_proxy'] or 0):.3f} coverage={(result['coverage'] or 0):.3f}")
    prompt = ('You are a skeptical factor reviewer. From the evaluated candidates below, pick the '
              f'{args.pick} most likely to generalize out-of-sample. Prefer moderate IC with stable ICIR, '
              'lower turnover, and economically plausible mechanisms; penalize extreme/unstable IC. '
              'Output ONLY a JSON array of integer indices.\n\n' + '\n'.join(lines))
    raw = llm.chat([{'role': 'user', 'content': prompt}], model=args.model, temperature=0.0, max_tokens=3000)
    indices = []
    for value in llm.extract_all_json(raw):
        if isinstance(value, list):
            indices.extend(value)
        elif isinstance(value, dict):
            for key in ('indices', 'selected', 'selection', 'picks'):
                if isinstance(value.get(key), list):
                    indices.extend(value[key])
    indices = [int(i) for i in indices if isinstance(i, (int, float)) and 0 <= int(i) < len(rows)]
    indices = list(dict.fromkeys(indices))[:args.pick]
    source = 'llm'
    if len(indices) < args.pick:
        source = 'fallback'
        indices = sorted(range(len(rows)), key=lambda i: -rows[i]['search']['fitness'])[:args.pick]
    def holdout(indices):
        values = []
        for index in indices:
            result = es.evaluate_expr(rows[index]['node'], panel, holdout_dates)
            if result:
                values.append(result['fitness'])
        return float(np.mean(values)) if values else None
    llm_top5 = holdout(indices)
    fitness_top5 = holdout(sorted(range(len(rows)), key=lambda i: -rows[i]['search']['fitness'])[:args.pick])
    payload = {'schema': 'AUTO-RESEARCH-AGENT-SELECT-1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'provider': llm.PROVIDER, 'model': args.model, 'seed': args.seed, 'pool': len(rows),
               'selection_source': source, 'llm_top5_holdout_fitness': llm_top5,
               'fitness_top5_holdout_fitness': fitness_top5,
               'delta_vs_fitness': (llm_top5 - fitness_top5) if (llm_top5 is not None and fitness_top5 is not None) else None,
               'picked': [rows[i]['formula'] for i in indices], 'raw_response': raw[:600],
               'score_window_read': False}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'select_{llm.PROVIDER}_{args.model.replace("/", "_")}_seed{args.seed}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('model', 'seed', 'selection_source',
                                                    'llm_top5_holdout_fitness', 'fitness_top5_holdout_fitness',
                                                    'delta_vs_fitness')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
