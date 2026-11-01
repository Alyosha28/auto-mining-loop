#!/usr/bin/env python3
"""rules-v0 independent reviewer for auto-research claims (append-only decisions)."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
RULES = ROOT / 'auto_research/reviewers/rules.json'

DOC_EXT = {'.md', '.json', '.txt', '.tex'}
DATA_HINTS = ('chain_result', 'step_record', 'summary', 'manifest', 'fair_result',
              'admission', 'version_manifest', 'panel_table', 'scheduler_result', 'replication_result', 'online_result', 'expr_search_result', 'incremental', 'stability', 'combo', 'oos_result', 'synth_result', 'matrix_', 'library_', 'qd_', 'bo_', 'bench_', 'real_eval', 'regime_probe', 'gate_search', 'confirm', 'gate_ablation', 'structure_diagnostics', 'pooled_ci', 'homogeneous_ci', 'gate_dose_stats', 'bench_force_n48', 'realdev_b12')
DEV_HINTS = ('chain_result', 'step_record', 'summary', 'admission', 'probe', 'select')
VERDICT_RE = re.compile(r'(\d|支持|不成立|成立|mixed|weak|not supported|Δ|%)', re.IGNORECASE)


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
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def check_claim(claim: dict) -> tuple[dict, list[str]]:
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    evidence = claim.get('evidence', [])
    statement = claim.get('statement', '')
    paths = [entry['path'] for entry in evidence]

    # R1 evidence exists and hashes match
    r1_failures = []
    for entry in evidence:
        path = ROOT / entry['path']
        if not path.is_file():
            r1_failures.append(f"missing {entry['path']}")
        elif sha256(path) != entry['sha256']:
            r1_failures.append(f"sha256 mismatch {entry['path']}")
    checks['R1_evidence_exists'] = not r1_failures
    reasons.extend(r1_failures)

    # R2 statement specificity
    checks['R2_statement_specificity'] = len(statement) >= 20 and bool(VERDICT_RE.search(statement))
    if not checks['R2_statement_specificity']:
        reasons.append('statement too vague or missing metric/verdict')

    # R3 evidence composition for experimental claims
    if claim.get('type') == 'experimental_result':
        docs = [path for path in paths if Path(path).suffix in DOC_EXT]
        data = [path for path in paths if any(hint in path for hint in DATA_HINTS)]
        checks['R3_evidence_composition'] = len(evidence) >= 2 and bool(docs) and bool(data)
        if not checks['R3_evidence_composition']:
            reasons.append('experimental claim needs >=2 evidence incl. doc + data artifact')
    else:
        checks['R3_evidence_composition'] = True

    # R4 score-window leakage
    score_files = [path for path in paths if 'scores.parquet' in path]
    if score_files and not any(any(hint in path for hint in DEV_HINTS) for path in paths):
        checks['R4_score_window_leakage'] = False
        reasons.append('score-window evidence without dev-side companion')
    elif claim.get('score_window_read') and 'loop' in statement.lower():
        checks['R4_score_window_leakage'] = False
        reasons.append('claims loop optimization while score window was read')
    else:
        checks['R4_score_window_leakage'] = True

    # R5 prereg reference for experimental claims
    if claim.get('type') == 'experimental_result':
        prereg = claim.get('preregistration')
        checks['R5_prereg_reference'] = bool(prereg) and (ROOT / prereg).is_file()
        if not checks['R5_prereg_reference']:
            reasons.append('missing or non-existent preregistration reference')
    else:
        checks['R5_prereg_reference'] = True

    # R7 evidence immutability (append-only logs change; require snapshot files)
    mutable_names = {'RESULTS_CN.md', 'SUBMISSION_READY.md'}
    mutable = [path for path in paths if path.endswith(('.jsonl', '.log'))
               or Path(path).name in mutable_names
               or Path(path).name.startswith(('README', 'CHANGELOG'))]
    checks['R7_evidence_immutability'] = not mutable
    if mutable:
        reasons.append(f'mutable document used as evidence (hash unstable): {mutable}')

    # R6 overclaim guard
    if re.search(r'\bRSI\b', statement, re.IGNORECASE) or 'autonomous research' in statement.lower():
        qualifier = re.search(r'bounded|not supported|no evidence|weak|limited|不成立|无证据', statement, re.IGNORECASE)
        checks['R6_overclaim_guard'] = bool(qualifier)
        if not qualifier:
            reasons.append('mentions RSI/autonomous research without bounded/negative qualifier')
    else:
        checks['R6_overclaim_guard'] = True

    return checks, reasons


def review_all(force: bool = False, only: set | None = None) -> list[dict]:
    claims = load_jsonl(LEDGER)
    existing = {}
    for review in load_jsonl(REVIEWS):
        existing[review['claim_id']] = review
    results = []
    for claim in claims:
        if only is not None and claim['claim_id'] not in only:
            continue
        if claim['claim_id'] in existing and not force:
            continue
        checks, reasons = check_claim(claim)
        decision = 'approve' if all(checks.values()) else 'reject'
        record = {
            'schema': 'AUTO-RESEARCH-REVIEW-1',
            'claim_id': claim['claim_id'],
            'reviewer': 'rules-v0',
            'decision': decision,
            'checks': checks,
            'reasons': reasons,
            'reviewed_at': now(),
        }
        append_jsonl(REVIEWS, record)
        results.append(record)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run')
    run.add_argument('--force', action='store_true')
    run.add_argument('--claim-id', action='append', default=None)
    sub.add_parser('list')
    args = parser.parse_args()
    if args.command == 'run':
        only = set(args.claim_id) if args.claim_id else None
        results = review_all(force=args.force, only=only)
        print(f'reviewed {len(results)} claims')
        for record in results:
            print(f"  {record['claim_id']}: {record['decision']} {record['reasons']}")
        return 0
    reviews = load_jsonl(REVIEWS)
    for record in reviews:
        print(f"  {record['claim_id']}: {record['decision']} {record['reasons']}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
