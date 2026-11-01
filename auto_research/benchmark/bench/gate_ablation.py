#!/usr/bin/env python3
"""Pre-registered H2: gate-biased mutation on regime_state, fresh seeds."""
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
    seeds = [29, 30, 31, 32, 33]
    arms = {'gate0': None, 'gate02': 0.2, 'gate10': 1.0}
    out = []
    for seed in seeds:
        panel = core.make_task_panel('regime_state', seed)
        features = [f'f{i:02d}' for i in range(40)] + ['f_state']
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        cut = int(round(len(dates) * 0.7))
        search_dates, holdout_dates = dates[:cut], dates[cut:]
        row = {'seed': seed}
        for arm, prob in arms.items():
            kwargs = {} if prob is None else {'gate_feature': 'f_state', 'gate_prob': prob}
            rows = core.run_qd_novelty(panel, features, search_dates, seed, 24, **kwargs)
            stats = core.top_stats(rows, panel, holdout_dates)
            row[arm] = stats
        out.append(row)
    path = ROOT / 'auto_research/benchmark/results/gate_ablation_prereg.json'
    path.write_text(json.dumps({'prereg': 'PREREG-20261023-NOVELTY-GATE', 'results': out}, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
