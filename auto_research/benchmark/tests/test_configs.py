#!/usr/bin/env python3
"""Validate benchmark configs and OOD task smoke run."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_configs(self):
        for path in (ROOT / 'configs').glob('*.json'):
            if path.name.startswith('._'):
                continue
            cfg = json.loads(path.read_text())
            for key in ('tasks', 'seeds', 'budget', 'baselines', 'tag'):
                self.assertIn(key, cfg, path.name)
            self.assertGreater(cfg['budget'], 0)
            self.assertTrue(cfg['tasks'])

    def test_ood_smoke(self):
        cmd = [sys.executable, '-m', 'bench.run', '--tasks', 'regime', '--seeds', '1',
               '--budget', '6', '--baselines', 'random,qd', '--tag', 'ood_smoke']
        result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        payload = json.loads(result.stdout)
        self.assertIn('regime', payload['summary'])


if __name__ == '__main__':
    unittest.main()
