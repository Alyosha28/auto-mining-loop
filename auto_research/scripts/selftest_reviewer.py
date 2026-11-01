#!/usr/bin/env python3
"""Self-test for rules-v0 reviewer (no ledger writes): valid claim passes, bad claims reject."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import review_claims  # noqa: E402


def main() -> int:
    good = {
        'claim_id': 'SELFTEST-OK', 'type': 'experimental_result',
        'statement': '测试结论：utility 从 0.0193 提升到 0.0635（+0.0281），阈值通过。',
        'evidence': [{'path': 'evo_rsif/PREREGISTRATION.json', 'sha256': review_claims.sha256(ROOT / 'evo_rsif/PREREGISTRATION.json')},
                     {'path': 'evo_rsif/runs/rsif_e1_s7/chain_result.json',
                      'sha256': review_claims.sha256(ROOT / 'evo_rsif/runs/rsif_e1_s7/chain_result.json')}],
        'preregistration': 'evo_rsif/PREREGISTRATION.json', 'score_window_read': False,
    }
    bad = [
        {'claim_id': 'SELFTEST-VAGUE', 'type': 'experimental_result', 'statement': '有效',
         'evidence': [], 'preregistration': None, 'score_window_read': False},
        {'claim_id': 'SELFTEST-OVERCLAIM', 'type': 'methodological',
         'statement': '我们实现了 autonomous research 与 RSI，模型会自己无限改进。',
         'evidence': [{'path': 'evo_rsif/PREREGISTRATION.json',
                       'sha256': review_claims.sha256(ROOT / 'evo_rsif/PREREGISTRATION.json')}],
         'preregistration': 'evo_rsif/PREREGISTRATION.json', 'score_window_read': False},
    ]
    checks, reasons = review_claims.check_claim(good)
    assert all(checks.values()), f'valid claim rejected: {reasons}'
    for claim in bad:
        checks, _ = review_claims.check_claim(claim)
        assert not all(checks.values()), f"{claim['claim_id']} should be rejected"
    print(json.dumps({'good': 'approve', 'bad': ['SELFTEST-VAGUE reject', 'SELFTEST-OVERCLAIM reject'],
                      'status': 'SELFTEST_OK'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
