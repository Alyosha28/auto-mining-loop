#!/usr/bin/env python3
"""Statistical summary of the synthetic benchmark (Wilson CIs + exact McNemar)."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'auto_research/synth_benchmark'


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    # two-sided exact binomial under p=0.5
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return round(min(1.0, 2 * tail), 6)


def main() -> None:
    data = json.loads((OUT / 'synth_result.json').read_text(encoding='utf-8'))
    summary = {}
    for task in ('planted', 'weak', 'null'):
        rows = [row for row in data['rows'] if row['task'] == task and row['status'] == 'COMPLETED']
        n = len(rows)
        entry = {'n': n}
        for policy, key in (('naive', 'naive_declares'), ('governed', 'governed_declares'), ('strict', 'strict_declares')):
            k = sum(1 for row in rows if row.get(key))
            lo, hi = wilson(k, n)
            entry[policy] = {'k': k, 'rate': round(k / n, 4), 'wilson95': [lo, hi]}
        # paired naive vs strict on the same seeds
        naive_only = sum(1 for row in rows if row.get('naive_declares') and not row.get('strict_declares'))
        strict_only = sum(1 for row in rows if row.get('strict_declares') and not row.get('naive_declares'))
        entry['mcnemar_naive_vs_strict'] = {'discordant_naive_only': naive_only,
                                            'discordant_strict_only': strict_only,
                                            'exact_p': mcnemar_exact(naive_only, strict_only)}
        summary[task] = entry
    payload = {'schema': 'AUTO-RESEARCH-SYNTH-STATS-1', 'summary': summary,
               'note': 'Wilson 95% intervals; exact McNemar on paired seeds (same seed set)',
               'score_window_read': False}
    path = OUT / 'synth_result_stats.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    # claim
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import claim_ledger
    import review_claims
    null = summary['null']
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'synth_benchmark',
            'claims': [{'claim_id': 'CLM-SYNTH-BENCH-003', 'type': 'experimental_result',
                        'statement': (f"合成 benchmark 统计（20 seeds）：null 任务 naive 声明率 {null['naive']['rate']} "
                                      f"(Wilson {null['naive']['wilson95']})、governed {null['governed']['rate']} "
                                      f"({null['governed']['wilson95']})、strict {null['strict']['rate']} "
                                      f"({null['strict']['wilson95']})；naive vs strict 配对 McNemar exact p="
                                      f"{null['mcnemar_naive_vs_strict']['exact_p']}；planted 检测率 "
                                      f"{summary['planted']['naive']['rate']}/{summary['planted']['governed']['rate']}/{summary['planted']['strict']['rate']}；"
                                      f"weak {summary['weak']['naive']['rate']}/{summary['weak']['governed']['rate']}/{summary['weak']['strict']['rate']}。"),
                        'evidence': ['auto_research/synth_benchmark/synth_result_stats.json',
                                     'auto_research/synth_benchmark/synth_result.json',
                                     'auto_research/paper/PLAN_snapshot_20261021.md'],
                        'run_refs': ['auto_research/synth_benchmark'],
                        'preregistration': 'auto_research/PREREGISTRATION.json',
                        'score_window_read': False}]}
    claim_path = OUT / 'claim_spec_stats.json'
    claim_path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(claim_path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
