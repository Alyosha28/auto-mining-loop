#!/usr/bin/env python3
"""Proof-of-concept: does a hand-built regime-gated expression recover H3?"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from bench import core  # noqa: E402


def leaf(name):
    return {'kind': 'leaf', 'feature': name}


def product(left, right):
    return {'kind': 'binary', 'op': 'product', 'left': left, 'right': right}


def diff(left, right):
    return {'kind': 'binary', 'op': 'diff', 'left': left, 'right': right}


def main() -> None:
    panel = core.make_task_panel('regime_state', 1)
    features = [f'f{i:02d}' for i in range(40)] + ['f_state']
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = int(round(len(dates) * 0.7))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    candidates = {
        'gate_f00': product(leaf('f_state'), leaf('f00')),
        'gate_f01': product(leaf('f_state'), leaf('f01')),
        'gate_diff_f00_f01': product(leaf('f_state'), diff(leaf('f00'), leaf('f01'))),
    }
    rows = {}
    for name, node in candidates.items():
        search = es.evaluate_expr(node, panel, search_dates)
        holdout = es.evaluate_expr(node, panel, holdout_dates)
        rows[name] = {'search': search, 'holdout': holdout}
    payload = {'schema': 'AUTO-RESEARCH-REGIME-PROBE-1', 'results': rows, 'score_window_read': False}
    out = ROOT / 'auto_research/benchmark/results/regime_probe.json'
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({name: {'search_ic': rows[name]['search']['mean_ic'] if rows[name]['search'] else None,
                             'holdout_ic': rows[name]['holdout']['mean_ic'] if rows[name]['holdout'] else None,
                             'holdout_fitness': rows[name]['holdout']['fitness'] if rows[name]['holdout'] else None}
                      for name in rows}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
