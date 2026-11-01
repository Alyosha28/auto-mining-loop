#!/usr/bin/env python3
"""Reviewer-gated harness self-improvement (P2).

A proposal must: (1) be a legal edit on the harness editable surface, (2) cite
approved claims as evidence, (3) state expected metric + threshold + falsification,
(4) not read the score window. An independent reviewer (rules-v0.1) decides; only
approved proposals are recorded as adoptable. Adoption here writes a proposal
artifact, it does NOT mutate the frozen evo_rsif evidence or verifier.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
OUT = ROOT / 'auto_research/harness_improvements'
PROPOSALS = OUT / 'proposals.jsonl'
REVIEW_LOG = OUT / 'reviews.jsonl'
ADOPTED = OUT / 'adopted_harness.json'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def evo_harness():
    import sys
    path = ROOT / 'skills/current/factor-evolution-harness2/scripts/evo_harness.py'
    if str(path.parent) not in sys.path:
        sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location('evo_harness_review', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def review(proposal: dict) -> tuple[dict, list[str]]:
    harness = evo_harness()
    checks: dict[str, bool] = {}
    reasons: list[str] = []
    edit = proposal.get('edit', {})
    # R1 legal edit on editable surface
    try:
        validated = harness.validate_proposal({'id': proposal['proposal_id'], **edit})
        checks['R1_legal_edit'] = True
    except Exception as exc:  # noqa: BLE001
        checks['R1_legal_edit'] = False
        reasons.append(f'illegal edit: {exc}')
    # R2 cited claims exist and are approved
    claims = {row['claim_id']: row for row in load_jsonl(LEDGER)}
    decisions = {row['claim_id']: row for row in load_jsonl(REVIEWS)}
    evidence_claims = proposal.get('evidence_claims', [])
    checks['R2_evidence_claims'] = bool(evidence_claims) and all(
        claim_id in claims and decisions.get(claim_id, {}).get('decision') == 'approve'
        for claim_id in evidence_claims)
    if not checks['R2_evidence_claims']:
        reasons.append('missing approved evidence claims')
    # R3 expected metric / threshold / falsification
    expected = proposal.get('expected_metric', {})
    checks['R3_expectation'] = bool(expected.get('metric') and expected.get('threshold')
                                    and proposal.get('falsification'))
    if not checks['R3_expectation']:
        reasons.append('expected_metric/threshold/falsification incomplete')
    # R4 no score-window read
    checks['R4_no_score_window'] = proposal.get('score_window_read') is False
    if not checks['R4_no_score_window']:
        reasons.append('score window read is not allowed in loop')
    # R5 intent present
    checks['R5_intent'] = bool(str(edit.get('intent', '')).strip())
    if not checks['R5_intent']:
        reasons.append('edit intent missing')
    return checks, reasons


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', required=True)
    args = parser.parse_args()
    proposal = json.loads((ROOT / args.proposal).read_text(encoding='utf-8'))
    checks, reasons = review(proposal)
    decision = 'approve' if all(checks.values()) else 'reject'
    record = {'schema': 'AUTO-RESEARCH-HARNESS-REVIEW-1', 'proposal_id': proposal['proposal_id'],
              'reviewer': 'rules-v0.1', 'decision': decision, 'checks': checks, 'reasons': reasons,
              'reviewed_at': now()}
    append_jsonl(PROPOSALS, {'schema': 'AUTO-RESEARCH-HARNESS-PROPOSAL-1', 'created_at': now(), **proposal})
    append_jsonl(REVIEW_LOG, record)
    if decision == 'approve':
        ADOPTED.write_text(json.dumps({'schema': 'AUTO-RESEARCH-HARNESS-ADOPTION-1',
                                       'proposal_id': proposal['proposal_id'],
                                       'edit': proposal['edit'], 'adopted_at': now(),
                                       'note': 'record-only adoption; frozen verifier untouched'}, ensure_ascii=False, indent=1) + '\n',
                           encoding='utf-8')
    print(json.dumps(record, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
