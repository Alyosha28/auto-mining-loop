#!/usr/bin/env python3
"""Smoke tests for the offline benchmark."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parents[1] / 'auto_research/scripts'))
from bench import core  # noqa: E402
from synth_benchmark import make_panel  # noqa: E402


class BenchmarkTests(unittest.TestCase):
    def test_panel_shape_and_tasks(self):
        for task in ['planted', 'weak', 'null']:
            panel = make_panel(task, seed=1, n_symbols=50, n_weeks=20)
            self.assertEqual(len(panel), 50 * 20)
            self.assertIn('forward_return', panel.columns)
            import re
            self.assertEqual(len([c for c in panel.columns if re.fullmatch(r'f\d+', c)]), 40)

    def test_qd_budget_and_uniqueness(self):
        panel = make_panel('planted', seed=1, n_symbols=80, n_weeks=40)
        features = [f'f{i:02d}' for i in range(40)]
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        cut = int(round(len(dates) * 0.7))
        rows = core.run_qd_novelty(panel, features, dates[:cut], seed=2, budget=10)
        self.assertLessEqual(len(rows), 10)
        self.assertEqual(len({row['formula'] for row in rows}), len(rows))

    def test_gate_search(self):
        panel = make_panel('planted', seed=1, n_symbols=60, n_weeks=30)
        features = [f'f{i:02d}' for i in range(40)]
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        cut = int(round(len(dates) * 0.7))
        rows = core.run_qd_novelty(panel, features, dates[:cut], seed=1, budget=6,
                                   gate_feature='f_state', gate_prob=1.0)
        self.assertLessEqual(len(rows), 6)

    def test_regime_state3_panel(self):
        panel = core.make_task_panel('regime_state3', seed=1)
        self.assertIn('f_state', panel.columns)
        states = panel.groupby('date')['f_state'].first()
        self.assertGreater(len(set(states)), 1)

    def test_force_gate_init(self):
        panel = core.make_task_panel('regime_state', seed=1)
        features = [f'f{i:02d}' for i in range(40)] + ['f_state']
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        cut = int(round(len(dates) * 0.7))
        rows = core.run_qd_novelty(panel, features, dates[:cut], seed=1, budget=8,
                                   gate_feature='f_state', gate_prob=0.2,
                                   force_gate_init=True)
        gated = sum(1 for row in rows
                    if row['node'].get('kind') == 'binary'
                    and row['node'].get('op') == 'product'
                    and row['node'].get('left', {}).get('feature') == 'f_state')
        self.assertGreater(gated / len(rows), 0.5)

    def test_cli_outputs(self):
        cmd = [sys.executable, '-m', 'bench.run', '--tasks', 'planted', '--seeds', '1',
               '--budget', '6', '--baselines', 'random,qd', '--tag', 'test_smoke']
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        payload = json.loads(result.stdout)
        self.assertIn('summary', payload)
        self.assertEqual(payload['summary']['planted']['qd']['n'], 1)
        self.assertIn('passed_mean', payload['summary']['planted']['qd'])
        self.assertTrue((ROOT / 'results' / 'LEADERBOARD_test_smoke.md').is_file())


if __name__ == '__main__':
    unittest.main()
