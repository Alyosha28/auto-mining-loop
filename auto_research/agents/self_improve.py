#!/usr/bin/env python3
"""Reviewer-gated self-improvement for the agent harness.

The agent may propose prompt/policy changes after a run, but a rule-based reviewer
must approve them. The verifier, windows, data and claim ledger are out of scope.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
from agents import llm, sanitize  # noqa: E402
import claim_ledger  # noqa: E402
import review_claims  # noqa: E402

OUT = ROOT / 'auto_research/agents/improvements'
LEDGER = ROOT / 'auto_research/ledger/claims.jsonl'
REVIEWS = ROOT / 'auto_research/ledger/reviews.jsonl'
ALLOWED_SURFACES = {'prompt': 'proposer/critic/reflection prompt text',
                    'policy': 'loop policy: patience, redirect strategy, generation budget allocation',
                    'ranking': 'Elo update / candidate ranking rule'}
SURFACE_ALIASES = {'prompt_text': 'prompt', 'prompt': 'prompt', 'generation_prompt': 'prompt',
                   'loop_policy': 'policy', 'policy': 'policy', 'budget_policy': 'policy',
                   'ranking_rule': 'ranking', 'ranking': 'ranking', 'elo_rule': 'ranking'}
FORBIDDEN = {'verifier', 'window', 'date range', 'data pipeline', 'claim ledger', 'reviewer rules', 'library'}

PROPOSER_SYSTEM = """You are improving an agentic factor-research harness.
Allowed change surfaces: prompt text, loop policy, ranking rule. NEVER propose changes to verifier,
data windows, claim ledger, reviewer rules, or factor libraries.
Output ONLY a JSON array of objects with keys: surface, change, rationale, evidence_claims (array of claim ids),
expected_metric, falsification. At most 3 proposals. Be concrete and falsifiable."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def approved_claim_ids() -> set[str]:
    decisions = {}
    for row in load_jsonl(REVIEWS):
        decisions[row['claim_id']] = row.get('decision')
    return {claim['claim_id'] for claim in load_jsonl(LEDGER)
            if decisions.get(claim['claim_id']) == 'approve'}


def propose(run_summary: dict, model: str | None) -> dict:
    approved = sorted(approved_claim_ids())[-12:]
    summary_text = json.dumps(run_summary, ensure_ascii=False)[:4000]
    if llm.PROVIDER != 'local':
        summary_text = sanitize.scrub(sanitize.__dict__.get('to_alias', lambda text, mapping: text)(summary_text, {})) if False else sanitize.scrub(summary_text)
    user = ('Run summary:\n' + summary_text +
            '\n\nRecently approved claim ids you may cite: ' + ', '.join(approved) +
            '\n\nPropose at most 3 improvements. Return ONLY the JSON array.')
    raw = llm.chat([{'role': 'system', 'content': PROPOSER_SYSTEM}, {'role': 'user', 'content': user}],
                   model=model, temperature=0.5, max_tokens=1200)
    try:
        items = llm.extract_json(raw)
    except ValueError as exc:
        return {'proposals': [], 'error': str(exc), 'raw': raw[:1200]}
    return {'proposals': items if isinstance(items, list) else [items], 'raw': raw}


def review(proposal: dict, approved: set[str]) -> dict:
    checks = {}
    text = json.dumps(proposal, ensure_ascii=False).lower()
    surface = SURFACE_ALIASES.get(str(proposal.get('surface', '')).lower())
    checks['R1_allowed_surface'] = surface in ALLOWED_SURFACES and not any(
        word in text for word in FORBIDDEN)
    checks['R2_evidence_claims'] = bool(proposal.get('evidence_claims')) and all(
        claim_id in approved for claim_id in proposal.get('evidence_claims', []))
    checks['R3_expected_metric'] = bool(proposal.get('expected_metric'))
    checks['R4_falsification'] = bool(proposal.get('falsification'))
    checks['R5_no_score_window'] = 'score' not in text.replace('score_window_read', '')
    return {'checks': checks, 'decision': 'approve' if all(checks.values()) else 'reject',
            'reasons': [key for key, value in checks.items() if not value]}


def review_stored(force: bool = False) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    proposals = load_jsonl(OUT / 'proposals.jsonl') if (OUT / 'proposals.jsonl').is_file() else []
    approved = approved_claim_ids()
    existing = {row['proposal_id'] for row in load_jsonl(OUT / 'reviews.jsonl')} if (OUT / 'reviews.jsonl').is_file() else set()
    rows = []
    for proposal in proposals:
        if proposal['proposal_id'] in existing and not force:
            rows.append({'proposal_id': proposal['proposal_id'], 'decision': 'existing'})
            continue
        review_record = {'schema': 'AUTO-RESEARCH-AGENT-IMPROVEMENT-REVIEW-1', 'proposal_id': proposal['proposal_id'],
                         'reviewed_at': now(), **review(proposal, approved)}
        with (OUT / 'reviews.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(review_record, ensure_ascii=False) + '\n')
        rows.append({'proposal_id': proposal['proposal_id'], 'decision': review_record['decision'],
                     'reasons': review_record['reasons']})
    adopted = [row for row in rows if row['decision'] == 'approve']
    (OUT / 'approved_policy.json').write_text(json.dumps({'approved_not_yet_adopted': adopted, 'updated_at': now()},
                                                        ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'rerun': len(rows), 'rows': rows}, ensure_ascii=False, indent=1))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', help='path to an orchestrator summary.json')
    parser.add_argument('--model', default=None)
    parser.add_argument('--review-stored', action='store_true')
    parser.add_argument('--force-rereview', action='store_true')
    args = parser.parse_args()
    if args.review_stored:
        review_stored(force=args.force_rereview)
        return
    summary = json.loads((ROOT / args.run).read_text(encoding='utf-8'))
    proposals = propose(summary, args.model).get('proposals', [])
    approved = approved_claim_ids()
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, proposal in enumerate(proposals):
        record = {'schema': 'AUTO-RESEARCH-AGENT-IMPROVEMENT-1', 'created_at': now(),
                  'proposal_id': f'IMP-{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}-{index}', **proposal,
                  'source_run': args.run, 'model': args.model}
        review_record = {'schema': 'AUTO-RESEARCH-AGENT-IMPROVEMENT-REVIEW-1', 'proposal_id': record['proposal_id'],
                         'reviewed_at': now(), **review(record, approved)}
        with (OUT / 'proposals.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + '\n')
        with (OUT / 'reviews.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(review_record, ensure_ascii=False) + '\n')
        rows.append({'proposal_id': record['proposal_id'], 'decision': review_record['decision'],
                     'reasons': review_record['reasons']})
    adopted = [row for row in rows if row['decision'] == 'approve']
    (OUT / 'approved_policy.json').write_text(json.dumps({'approved_not_yet_adopted': adopted, 'updated_at': now()},
                                                        ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'n_proposals': len(rows), 'rows': rows}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
