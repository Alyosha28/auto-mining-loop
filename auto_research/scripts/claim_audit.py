#!/usr/bin/env python3
"""Audit claim ledger: review status, snapshot coverage, evidence presence."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
OUT = ROOT / 'auto_research/ledger/claim_audit.json'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    claims = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    reviews = {}
    if REVIEWS.is_file():
        for line in REVIEWS.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                reviews[row['claim_id']] = row
    superseded = {row['supersedes'] for row in claims if row.get('supersedes')}
    status = Counter()
    snapshot = 0
    live = 0
    legacy_backed = 0
    live_unbacked = 0
    active_unbacked = 0
    superseded_unbacked = 0
    missing = 0
    for claim in claims:
        review = reviews.get(claim['claim_id'])
        status[review.get('decision', 'unreviewed') if review else 'unreviewed'] += 1
        legacy_path = ROOT / 'auto_research/ledger/evidence_legacy' / claim['claim_id'] / 'MANIFEST.json'
        legacy = {}
        if legacy_path.is_file():
            payload = json.loads(legacy_path.read_text())
            legacy = {row['original']: row for row in payload.get('entries', [])}
        for entry in claim.get('evidence', []):
            path = ROOT / entry['path']
            if entry['path'].startswith('auto_research/ledger/evidence/'):
                snapshot += 1
            else:
                live += 1
            if not path.is_file():
                missing += 1
            elif not entry['path'].startswith('auto_research/ledger/evidence/'):
                row = legacy.get(entry['path'])
                if row and row.get('claim_sha256') == entry['sha256'] and row.get('snapshot_sha256') == entry['sha256']:
                    legacy_backed += 1
                else:
                    live_unbacked += 1
                    if claim['claim_id'] in superseded:
                        superseded_unbacked += 1
                    else:
                        active_unbacked += 1
    payload = {'claims': len(claims), 'superseded': len(superseded),
               'review_status': dict(status), 'snapshot_evidence': snapshot,
               'live_evidence': live, 'legacy_backed': legacy_backed,
               'live_unbacked': live_unbacked, 'active_unbacked': active_unbacked,
               'superseded_unbacked': superseded_unbacked, 'missing_evidence': missing}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
