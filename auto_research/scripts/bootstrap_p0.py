#!/usr/bin/env python3
"""P0 bootstrap: backfill RSI claims, run rules-v0 reviewer, write P0 report."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import claim_ledger  # noqa: E402
import review_claims  # noqa: E402

SPEC = ROOT / 'auto_research/claims/spec_rsi_backfill.json'
REPORT = ROOT / 'auto_research/docs/P0_REPORT_CN.md'


def main() -> None:
    added = claim_ledger.add_claims(SPEC)
    reviewed = review_claims.review_all()
    claims = claim_ledger.load_jsonl(claim_ledger.LEDGER)
    decisions = {row['claim_id']: row for row in claim_ledger.load_jsonl(claim_ledger.REVIEWS)}
    approved = [cid for cid, row in decisions.items() if row['decision'] == 'approve']
    rejected = [cid for cid, row in decisions.items() if row['decision'] == 'reject']
    lines = [
        '# P0 报告：claim ledger + rules-v0 reviewer（auto-research v0）',
        '',
        f'- 生成时间：{datetime.now(timezone.utc).isoformat()}',
        f'- 回填 claims：{len(claims)}（本次新增 {len(added)}）',
        f'- reviewer 决策：approve {len(approved)} / reject {len(rejected)}',
        f'- ledger：`auto_research/ledger/claims.jsonl`、`auto_research/ledger/reviews.jsonl`（append-only）',
        '',
        '## Claims',
        '',
        '| claim_id | type | verdict | statement |',
        '|---|---|---|---|',
    ]
    for claim in claims:
        review = decisions.get(claim['claim_id'])
        verdict = review['decision'] if review else 'unreviewed'
        statement = claim['statement'].replace('|', '\\|')
        lines.append(f"| {claim['claim_id']} | {claim['type']} | {verdict} | {statement} |")
    lines += ['', '## 被 reject 的原因', '']
    rejected_any = False
    for claim in claims:
        review = decisions.get(claim['claim_id'])
        if review and review['decision'] == 'reject':
            rejected_any = True
            lines.append(f"- {claim['claim_id']}: {review['reasons']}")
    if not rejected_any:
        lines.append('- 无')
    lines += [
        '',
        '## 校验',
        '',
        '- `python auto_research/scripts/claim_ledger.py list`',
        '- `python auto_research/scripts/claim_ledger.py verify`（证据 sha256）',
        '- `python auto_research/scripts/review_claims.py run --force`（重审）',
        '',
        '## 下一步（P1）',
        '',
        '1. 从 claims/experiments 自动构建 knowledge graph（panel/family/operator/claim 节点 + supports/contradicts/tested_on 边）；',
        '2. typed retrieval（similar/contradictory/cross_domain/counterintuitive）接入 hypothesis generator；',
        '3. hypothesis generator + experiment designer 产出第一条 P1 实验并自动回填 claim。',
    ]
    REPORT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'claims': len(claims), 'added': len(added),
                      'approved': len(approved), 'rejected': len(rejected),
                      'report': str(REPORT.relative_to(ROOT))}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
