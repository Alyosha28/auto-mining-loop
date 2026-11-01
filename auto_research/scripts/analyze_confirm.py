#!/usr/bin/env python3
"""Analyze the preregistered H1/H2 confirmatory runs."""
from __future__ import annotations

import glob
import json
from math import comb
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/agents/experiments'
GATE = ROOT / 'auto_research/benchmark/results/gate_ablation_prereg.json'


def sign_p(values):
    n = len(values)
    wins = sum(v > 0 for v in values)
    return wins, sum(comb(n, i) for i in range(wins, n + 1)) / 2 ** n


def bootstrap_ci(values, seed=20261023):
    rng = np.random.default_rng(seed)
    boots = [np.mean(rng.choice(values, len(values), replace=True)) for _ in range(10000)]
    return float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))


def main() -> None:
    files = sorted(glob.glob(str(OUT / 'qd_opencode_deepseek-v4.1-flash_split*_seed*_b24_kg_nov_confirm.json')))
    h1 = []
    for path in files:
        d = json.loads(Path(path).read_text())
        h1.append({'split': d['split_frac'], 'seed': d['seed'],
                   'delta': d['delta_vs_random_mutation'],
                   'qd': d['qd_top5_holdout_fitness'],
                   'rand_mut': d['random_mutation_top5_holdout_fitness']})
    h1_vals = [r['delta'] for r in h1 if r['delta'] is not None]
    wins1, p1 = sign_p(h1_vals)
    lo1, hi1 = bootstrap_ci(h1_vals)

    gate = json.loads(GATE.read_text())
    h2 = []
    for row in gate['results']:
        h2.append(row['gate02']['mean'] - row['gate0']['mean'])
    wins2, p2 = sign_p(h2)
    lo2, hi2 = bootstrap_ci(h2)
    # Holm over two tests
    raw = sorted([('H1', p1), ('H2', p2)], key=lambda item: item[1])
    holm = {}
    for index, (name, p) in enumerate(raw):
        holm[name] = min(1.0, p * (len(raw) - index))

    payload = {
        'prereg': 'PREREG-20261023-NOVELTY-GATE',
        'H1': {'n': len(h1_vals), 'mean': float(np.mean(h1_vals)) if h1_vals else None,
               'wins': wins1, 'sign_p': p1, 'ci': [lo1, hi1], 'holm_p': holm['H1'],
               'success': bool(lo1 > 0 and wins1 >= 18)},
        'H2': {'n': len(h2), 'mean': float(np.mean(h2)), 'wins': wins2, 'sign_p': p2,
               'ci': [lo2, hi2], 'holm_p': holm['H2'],
               'success': bool(np.mean(h2) > 0 and wins2 >= 4)},
        'runs': h1,
    }
    path = ROOT / 'auto_research/agents/experiments/confirmatory_analysis.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
