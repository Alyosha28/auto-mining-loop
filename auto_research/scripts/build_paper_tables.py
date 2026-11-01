#!/usr/bin/env python3
"""Freeze paper table/figure data as CSVs (no paper text is written)."""
from __future__ import annotations

import csv
import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROOTP = ROOT / 'auto_research'
ASSETS = ROOTP / 'paper_assets'


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    claims = [json.loads(line) for line in (ROOTP / 'ledger/claims.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    reviews = {json.loads(line)['claim_id']: json.loads(line) for line in (ROOTP / 'ledger/reviews.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()}
    write_csv(ASSETS / 'table_claims.csv', ['claim_id', 'type', 'decision', 'evidence_count', 'statement'],
              [{'claim_id': c['claim_id'], 'type': c['type'],
                'decision': reviews.get(c['claim_id'], {}).get('decision', 'unreviewed'),
                'evidence_count': len(c.get('evidence', [])), 'statement': c['statement']} for c in claims])
    # Table 2: panel yield
    table = json.loads((ROOTP / 'scheduler/panel_table.json').read_text(encoding='utf-8'))
    write_csv(ASSETS / 'table_panel_yield.csv', ['panel', 'evaluated', 'passed', 'added', 'added_per_1000'],
              table.get('rows', []))
    # Table 3: replication (if available)
    replication = ROOTP / 'scheduler/replication_result.json'
    if replication.is_file():
        rows = json.loads(replication.read_text(encoding='utf-8')).get('rows', [])
        write_csv(ASSETS / 'table_replication.csv', ['panel', 'seed', 'arm', 'evaluated', 'added', 'reward_per_1000', 'status'], rows)
    # Fig 1 data: E1 seed trajectories
    trajectory = []
    for path in sorted(glob.glob(str(ROOT / 'evo_rsif/runs/rsif_e1*/chain_result.json'))):
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        for index, utility in enumerate(data.get('utilities', []), 1):
            trajectory.append({'chain': data.get('chain_id'), 'seed': data.get('seed'),
                               'round': index, 'utility': utility,
                               'memory': data.get('memory'), 'regularize': data.get('regularize')})
    write_csv(ASSETS / 'fig1_seed_trajectory.csv', ['chain', 'seed', 'round', 'utility', 'memory', 'regularize'], trajectory)
    # Fig 3 data: online scheduling rewards
    online_rows = []
    for path in sorted((ROOTP / 'scheduler').glob('online_result*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        for row in data.get('rows', []):
            if row.get('status') == 'COMPLETED':
                online_rows.append({'source': path.name, 'seed': row.get('seed'), 'policy': row.get('policy'),
                                    'round': row.get('round'), 'panel': row.get('panel'),
                                    'reward_per_1000': row.get('reward_per_1000')})
    write_csv(ASSETS / 'fig3_online_rewards.csv', ['source', 'seed', 'policy', 'round', 'panel', 'reward_per_1000'], online_rows)
    # KG summary
    graph = json.loads((ROOTP / 'knowledge/graph.json').read_text(encoding='utf-8'))
    (ASSETS / 'kg_summary.json').write_text(json.dumps(graph['counts'], ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'assets': str(ASSETS.relative_to(ROOT)),
                      'claims': len(claims), 'trajectory_rows': len(trajectory),
                      'online_rows': len(online_rows)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
