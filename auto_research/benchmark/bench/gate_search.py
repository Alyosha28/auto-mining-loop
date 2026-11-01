#!/usr/bin/env python3
"""Explicit gate operator search: every mutation wrapped as product(f_state, .)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
from bench import core  # noqa: E402


def main() -> None:
    tasks = ['regime_state']
    out = {}
    for task in tasks:
        rows_out = []
        for seed in [1, 2, 3, 4, 5]:
            panel = core.make_task_panel(task, seed)
            features = [f'f{i:02d}' for i in range(40)] + ['f_state']
            dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
            cut = int(round(len(dates) * 0.7))
            search_dates, holdout_dates = dates[:cut], dates[cut:]
            rows = core.run_qd_novelty(panel, features, search_dates, seed, 24,
                                       gate_feature='f_state', gate_prob=1.0)
            stats = core.top_stats(rows, panel, holdout_dates)
            rows_out.append({'seed': seed, **stats})
        out[task] = rows_out
    path = ROOT / 'auto_research/benchmark/results/gate_search.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
