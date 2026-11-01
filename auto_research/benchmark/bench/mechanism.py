#!/usr/bin/env python3
"""Mechanism probe: QD novelty stats vs random/evolution on synthetic tasks."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from bench import core

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))



def main() -> None:
    tasks = ['planted', 'weak', 'null']
    out = {}
    for task in tasks:
        out[task] = []
        for seed in [1, 2, 3]:
            panel = core.make_task_panel(task, seed)
            features = [f'f{i:02d}' for i in range(40)]
            if 'f_state' in panel.columns:
                features.append('f_state')
            dates = __import__('pandas').DatetimeIndex(sorted(panel['date'].unique()))
            cut = int(round(len(dates) * 0.7))
            search_dates, holdout_dates = dates[:cut], dates[cut:]
            rows, stats = core.run_qd_novelty(panel, features, search_dates, seed, 24, return_stats=True)
            qd = core.top_stats(rows, panel, holdout_dates)
            rnd = core.top_stats(core.run_random(panel, features, search_dates, seed, 24), panel, holdout_dates)
            out[task].append({'seed': seed, 'stats': stats, 'qd': qd, 'random': rnd,
                              'delta': (qd['mean'] - rnd['mean']) if (qd['mean'] is not None and rnd['mean'] is not None) else None})
    path = ROOT / 'auto_research/benchmark/results/mechanism.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
