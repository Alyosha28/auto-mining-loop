#!/usr/bin/env python3
"""Append-only claim ledger for the auto-research project.

Commands:
  add --spec <json>   add claims from a spec file (skip existing claim_id)
  list                list claims with latest review decision
  verify              verify evidence paths and sha256 hashes
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
LEGACY = ROOT / 'auto_research/ledger/evidence_legacy'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def enrich_evidence(paths: list[str], snapshot: bool = False, claim_id: str | None = None) -> list[dict]:
    evidence = []
    missing = []
    for raw in paths:
        path = ROOT / raw
        if not path.is_file():
            missing.append(raw)
            continue
        if snapshot:
            if not claim_id:
                raise SystemExit('snapshot requires claim_id')
            target_dir = ROOT / 'auto_research/ledger/evidence' / claim_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / raw.replace('/', '__')
            target.write_bytes(path.read_bytes())
            evidence.append({'path': str(target.relative_to(ROOT)), 'sha256': sha256(target)})
        else:
            evidence.append({'path': raw, 'sha256': sha256(path)})
    if missing:
        raise SystemExit(f'evidence files not found: {missing}')
    return evidence


def add_claims(spec_path: Path, snapshot: bool = False) -> list[dict]:
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    existing = {row['claim_id'] for row in load_jsonl(LEDGER)}
    added = []
    for item in spec.get('claims', []):
        claim_id = item['claim_id']
        if claim_id in existing:
            continue
        claim = {
            'schema': 'AUTO-RESEARCH-CLAIM-1',
            'claim_id': claim_id,
            'project': spec.get('project', 'auto_research'),
            'type': item.get('type', 'experimental_result'),
            'statement': item['statement'],
            'evidence': enrich_evidence(item.get('evidence', []), snapshot=snapshot, claim_id=claim_id),
            'run_refs': item.get('run_refs', []),
            'preregistration': item.get('preregistration'),
            'score_window_read': bool(item.get('score_window_read', False)),
            'supersedes': item.get('supersedes'),
            'created_at': now(),
            'status': 'unverified',
        }
        append_jsonl(LEDGER, claim)
        existing.add(claim_id)
        added.append(claim)
    return added


def latest_reviews() -> dict:
    decisions = {}
    for review in load_jsonl(REVIEWS):
        decisions[review['claim_id']] = review
    return decisions


def cmd_add(args) -> int:
    added = add_claims(Path(args.spec), snapshot=args.snapshot)
    print(f'added {len(added)} claims to {LEDGER.relative_to(ROOT)}')
    for claim in added:
        print(f"  {claim['claim_id']}: {claim['statement'][:60]}...")
    return 0


def cmd_list(args) -> int:
    claims = load_jsonl(LEDGER)
    decisions = latest_reviews()
    print(f'claims={len(claims)} reviews={len(load_jsonl(REVIEWS))}')
    for claim in claims:
        review = decisions.get(claim['claim_id'])
        verdict = review['decision'] if review else 'unreviewed'
        print(f"  {claim['claim_id']} [{claim['type']}] {verdict:8s} | {claim['statement'][:80]}")
    return 0


def legacy_manifest(claim_id: str) -> dict:
    path = LEGACY / claim_id / 'MANIFEST.json'
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding='utf-8'))
    return {entry['original']: entry for entry in payload.get('entries', [])}


def snapshot_legacy(claims: list[dict]) -> dict:
    created = 0
    skipped = 0
    for claim in claims:
        claim_id = claim['claim_id']
        target_dir = LEGACY / claim_id
        if target_dir.exists():
            skipped += 1
            continue
        target_dir.mkdir(parents=True)
        entries = []
        for entry in claim.get('evidence', []):
            source = ROOT / entry['path']
            if not source.is_file():
                continue
            target = target_dir / entry['path'].replace('/', '__')
            target.write_bytes(source.read_bytes())
            entries.append({'original': entry['path'], 'claim_sha256': entry['sha256'],
                            'snapshot': str(target.relative_to(ROOT)),
                            'snapshot_sha256': sha256(target)})
        (target_dir / 'MANIFEST.json').write_text(
            json.dumps({'claim_id': claim_id, 'created_at': now(), 'entries': entries},
                       ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        created += 1
    return {'created': created, 'skipped': skipped}


def cmd_verify(args) -> int:
    claims = load_jsonl(LEDGER)
    if args.project:
        claims = [c for c in claims if c.get('project') in args.project]
    if args.exclude_project:
        claims = [c for c in claims if c.get('project') not in args.exclude_project]
    superseded = {claim.get('supersedes') for claim in claims if claim.get('supersedes')}
    skipped = sum(1 for claim in claims if claim['claim_id'] in superseded)
    claims = [claim for claim in claims if claim['claim_id'] not in superseded]
    problems = []
    snapshot_ok = 0
    for claim in claims:
        manifest = legacy_manifest(claim['claim_id'])
        for entry in claim['evidence']:
            path = ROOT / entry['path']
            current = sha256(path) if path.is_file() else None
            if current == entry['sha256']:
                continue
            legacy = manifest.get(entry['path'])
            if legacy and legacy.get('claim_sha256') == entry['sha256'] and legacy.get('snapshot_sha256') == entry['sha256']:
                snapshot_ok += 1
                continue
            if current is None:
                problems.append(f"{claim['claim_id']}: missing {entry['path']}")
            else:
                problems.append(f"{claim['claim_id']}: sha256 mismatch {entry['path']}")
    if problems:
        print('VERIFY_FAILED')
        for problem in problems:
            print('  ' + problem)
        return 1
    print(f'VERIFY_OK claims={len(claims)} evidence_files={sum(len(c["evidence"]) for c in claims)} snapshot_ok={snapshot_ok} '
          f'superseded_skipped={skipped}')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add')
    add.add_argument('--spec', required=True)
    add.add_argument('--snapshot', action='store_true')
    sub.add_parser('list')
    verify = sub.add_parser('verify')
    verify.add_argument('--project', action='append', default=[])
    verify.add_argument('--exclude-project', action='append', default=[])
    sub.add_parser('snapshot-legacy')
    args = parser.parse_args()
    if args.command == 'add':
        return cmd_add(args)
    if args.command == 'list':
        return cmd_list(args)
    if args.command == 'snapshot-legacy':
        claims = load_jsonl(LEDGER)
        superseded = {row['supersedes'] for row in claims if row.get('supersedes')}
        result = snapshot_legacy([c for c in claims if c['claim_id'] not in superseded])
        print(json.dumps(result, ensure_ascii=False))
        return 0
    return cmd_verify(args)


if __name__ == '__main__':
    raise SystemExit(main())
