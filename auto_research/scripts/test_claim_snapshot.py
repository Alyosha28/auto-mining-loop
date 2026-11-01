#!/usr/bin/env python3
"""Unit test: legacy evidence snapshot protects against silent file drift."""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger as cl  # noqa: E402


class LegacySnapshotTest(unittest.TestCase):
    def test_snapshot_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / 'evidence.txt'
            evidence.write_text('original')
            digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
            claims = [{'claim_id': 'CLM-TEST', 'project': 'test', 'type': 'structural',
                       'statement': 'test claim', 'evidence': [{'path': 'evidence.txt', 'sha256': digest}],
                       'supersedes': None}]
            ledger = root / 'claims.jsonl'
            ledger.write_text('\n'.join(json.dumps(c) for c in claims) + '\n')
            old_root, old_ledger, old_legacy = cl.ROOT, cl.LEDGER, cl.LEGACY
            cl.ROOT, cl.LEDGER, cl.LEGACY = root, ledger, root / 'legacy'
            try:
                result = cl.snapshot_legacy(claims)
                self.assertEqual(result['created'], 1)
                evidence.write_text('externally changed')
                args = type('Args', (), {'project': [], 'exclude_project': []})()
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cl.cmd_verify(args)
                self.assertEqual(code, 0)
                self.assertIn('snapshot_ok=1', buffer.getvalue())
            finally:
                cl.ROOT, cl.LEDGER, cl.LEGACY = old_root, old_ledger, old_legacy


if __name__ == '__main__':
    unittest.main()
