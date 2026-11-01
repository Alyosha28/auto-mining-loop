#!/usr/bin/env python3
"""Example: add a custom baseline to the benchmark in ~20 lines.

Run from auto_research/benchmark:
    ../../.venv/bin/python examples/custom_baseline.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / 'auto_research/scripts'))
from bench import core  # noqa: E402
from synth_benchmark import make_panel  # noqa: E402


def run_single_feature_baseline(panel, features, search_dates, seed=0, budget=1):
    """A trivial baseline: evaluate the first N raw features as factors."""
    rows = []
    for feature in features[:budget]:
        node = {'kind': 'leaf', 'feature': feature}
        result = core.es.evaluate_expr(node, panel, search_dates)
        if result:
            rows.append({'node': node, 'formula': result['formula'], 'fitness': result['fitness']})
    return rows


def main() -> None:
    panel = make_panel('planted', seed=1)
    features = [f'f{i:02d}' for i in range(40)]
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = int(round(len(dates) * 0.7))
    rows = run_single_feature_baseline(panel, features, dates[:cut], budget=5)
    stats = core.top_stats(rows, panel, dates[cut:])
    print({'baseline': 'single_feature', 'n': len(rows), 'top5': stats})


if __name__ == '__main__':
    main()
