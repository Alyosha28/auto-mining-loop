#!/usr/bin/env python3
"""Pilot: one proposer + critic pass, then real dev-window evaluation."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from agents import knowledge, roles  # noqa: E402

OUT = ROOT / 'auto_research/agents/runs'


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=None, help='local model id; defaults to AUTO_RESEARCH_LLM_MODEL')
    parser.add_argument('--n', type=int, default=6)
    args = parser.parse_args()
    model = args.model or __import__('agents.llm', fromlist=['MODEL']).MODEL
    panel = es.build_panel()
    features = es.features(panel)
    all_dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * 0.7)))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    brief = ('Find factor expressions on the provided panel features that improve dev-window '
             'cross-sectional rank IC / ICIR / turnover fitness beyond single-feature baselines. '
             'Known failures: memory and meta-level transfer did not work; combination beats single factors.')
    proposal = roles.propose(brief, features, n=args.n, model=model)
    critique = roles.critique(brief, [row for row in proposal['proposals'] if row.get('status') == 'PARSED'], model=model)
    decisions = [row for row in (critique.get('decisions') or []) if isinstance(row, dict)]
    accepted_index = {row.get('index') for row in decisions if str(row.get('decision', '')).lower().startswith('accept')}
    evaluations = []
    for index, row in enumerate(proposal['proposals']):
        if row.get('status') != 'PARSED':
            evaluations.append({'index': index, 'expression': row['expression'], 'status': 'PARSE_REJECTED',
                                'error': row.get('error')})
            continue
        search = es.evaluate_expr(row['node'], panel, search_dates)
        holdout = es.evaluate_expr(row['node'], panel, holdout_dates)
        evaluations.append({'index': index, 'expression': row['expression'],
                            'critic_decision': 'accept' if index in accepted_index else 'reject',
                            'search': search, 'holdout': holdout, 'status': 'EVALUATED'})
    OUT.mkdir(parents=True, exist_ok=True)
    run_dir = OUT / f'pilot_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}'
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / 'proposal.json').write_text(json.dumps(proposal, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (run_dir / 'critique.json').write_text(json.dumps(critique, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (run_dir / 'evaluations.json').write_text(json.dumps(evaluations, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    summary = {'run': str(run_dir.relative_to(ROOT)), 'model': model,
               'n_proposals': len(proposal['proposals']),
               'n_parsed': sum(1 for row in proposal['proposals'] if row.get('status') == 'PARSED'),
               'n_accepted': len(accepted_index),
               'n_evaluated': sum(1 for row in evaluations if row['status'] == 'EVALUATED')}
    (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for row in evaluations:
        if row['status'] == 'EVALUATED':
            print(json.dumps({'expr': row['expression'], 'critic': row['critic_decision'],
                              'search_fitness': row['search']['fitness'] if row['search'] else None,
                              'holdout_fitness': row['holdout']['fitness'] if row['holdout'] else None,
                              'holdout_ic': row['holdout']['mean_ic'] if row['holdout'] else None},
                             ensure_ascii=False))


if __name__ == '__main__':
    main()
