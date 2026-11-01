#!/usr/bin/env python3
"""Write a compact execution and diagnostic summary for a completed continuation panel."""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def absolute(path: Path | str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def executed_positions(unit: dict) -> tuple[int, int]:
    positions = 0
    decisions = 0
    for key in ('shared_run', 'parent_run', 'diagnostic_run'):
        loop = absolute(unit[key])
        for record_path in loop.glob('decisions/*/child/step/step_record.json'):
            record = read(record_path)
            if record.get('probe_only') is True:
                decisions += 1
                positions += int(record['adopted_probe']['metrics']['n_candidates'])
    return positions, decisions


def behavior_deltas(units: list[dict], metric: str) -> list[float]:
    return [float(unit['select_metrics']['diagnostic'][metric])
            - float(unit['select_metrics']['parent'][metric]) for unit in units]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = absolute(args.result)
    panel = read(source)
    units = panel['paired_units']
    executed = [executed_positions(unit) for unit in units]
    parent_fixed = [unit['select_parent_minus_fixed'] for unit in units]
    diagnostic_fixed = [unit['select_diagnostic_minus_fixed'] for unit in units]
    shared_fixed = [unit['select_shared_minus_fixed'] for unit in units]
    concentrations = [unit['date_block_diagnostic']['largest_positive_block_share']
                      for unit in units
                      if unit['date_block_diagnostic']['largest_positive_block_share'] is not None]
    behavior = {}
    for metric in ('novelty', 'duplicate_rate', 'turnover_proxy', 'pass_rate', 'coverage'):
        values = behavior_deltas(units, metric)
        behavior[metric] = {'median': statistics.median(values),
                            'min': min(values), 'max': max(values)}
    summary = {
        'schema': 'AUTO-RESEARCH-DIAGNOSTIC-CONTINUATION-EXECUTION-SUMMARY-1',
        'source_result': str(source.relative_to(ROOT)),
        'protocol_version': panel['protocol_version'],
        'model': panel['model'],
        'evidence_status': panel['evidence_status'],
        'all_protocol_checks_passed': panel['all_protocol_checks_passed'],
        'median_paired_delta': panel['median_paired_delta'],
        'task_seed_medians': panel['task_seed_medians'],
        'leave_one_task_out_medians': panel['leave_one_task_out_medians'],
        'paired_deltas': panel['paired_deltas'],
        'delta_counts': {
            'positive': sum(item['delta'] > 0 for item in panel['paired_deltas']),
            'zero': sum(item['delta'] == 0 for item in panel['paired_deltas']),
            'negative': sum(item['delta'] < 0 for item in panel['paired_deltas']),
        },
        'auxiliary_select_deltas': {
            'parent_minus_fixed': {'median': statistics.median(parent_fixed), 'min': min(parent_fixed)},
            'diagnostic_minus_fixed': {'median': statistics.median(diagnostic_fixed),
                                       'min': min(diagnostic_fixed)},
            'shared_minus_fixed': {'median': statistics.median(shared_fixed), 'min': min(shared_fixed)},
        },
        'diagnostic_minus_parent_behavior': behavior,
        'date_block_concentration': {'median': statistics.median(concentrations),
                                     'max': max(concentrations)},
        'usage': {**panel['usage'],
                  'executed_candidate_positions': sum(item[0] for item in executed),
                  'executed_decisions': sum(item[1] for item in executed),
                  'maximum_candidate_positions': 864},
        'failed_or_invalid_decisions': panel['failed_or_invalid_decisions'],
        'recovery_units': [
            {'task': unit['task'], 'seed': unit['seed'],
             'shared_recovered_from': unit.get('shared_recovered_from'),
             'parent_recovered_from': unit.get('parent_recovered_from'),
             'diagnostic_recovered_from': unit.get('diagnostic_recovered_from')}
            for unit in units if any(unit.get(key) for key in
                                     ('shared_recovered_from', 'parent_recovered_from',
                                      'diagnostic_recovered_from'))],
        'score_window_read': False,
        'independent_real_data_oos': False,
    }
    write_new(absolute(args.output) / 'result.json', summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
