#!/usr/bin/env python3
"""Unit test: --snapshot actually copies evidence and protects against drift."""
from __future__ import annotations

import argparse
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


class SnapshotAddTest(unittest.TestCase):
    def test_cmd_add_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / 'evidence.txt'
            evidence.write_text('original')
            spec = root / 'spec.json'
            spec.write_text(json.dumps({'project': 'test', 'claims': [{
                'claim_id': 'CLM-SNAP', 'type': 'structural',
                'statement': 'snapshot test claim', 'evidence': ['evidence.txt']}]}))
            ledger = root / 'claims.jsonl'
            old_root, old_ledger, old_legacy = cl.ROOT, cl.LEDGER, cl.LEGACY
            cl.ROOT, cl.LEDGER, cl.LEGACY = root, ledger, root / 'legacy'
            try:
                args = argparse.Namespace(spec=str(spec), snapshot=True)
                cl.cmd_add(args)
                rows = [json.loads(line) for line in ledger.read_text().splitlines()]
                self.assertEqual(len(rows), 1)
                path = rows[0]['evidence'][0]['path']
                self.assertTrue(path.startswith('auto_research/ledger/evidence/CLM-SNAP/'))
                evidence.write_text('externally changed')
                verify_args = argparse.Namespace(project=[], exclude_project=[])
                buffer = io.StringIO()
                with redirect_stdout(buffer):
                    code = cl.cmd_verify(verify_args)
                self.assertEqual(code, 0)
                self.assertIn('VERIFY_OK', buffer.getvalue())
            finally:
                cl.ROOT, cl.LEDGER, cl.LEGACY = old_root, old_ledger, old_legacy


if __name__ == '__main__':
    unittest.main()
