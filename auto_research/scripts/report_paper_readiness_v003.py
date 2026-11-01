#!/usr/bin/env python3
"""Export V003 machine-readable tables for paper figures and reporting."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_paper_readiness_v003 import executed_probe_steps

ARMS = ('scalar', 'memory', 'diagnostic', 'random')
CONTRASTS = {
    'memory_minus_scalar': 'select_memory_minus_scalar',
    'diagnostic_minus_memory': 'select_diagnostic_minus_memory',
    'memory_minus_random': 'select_memory_minus_random',
    'memory_minus_fixed': 'select_memory_minus_fixed',
}


def absolute(path: Path | str) -> Path:
    value = Path(path)
    return value if value.is_absolute() else ROOT / value


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def auxiliary_execution_rows(units: list[dict]) -> list[dict]:
    requirements = {
        'memory_minus_scalar': ('scalar', 'memory'),
        'diagnostic_minus_memory': ('memory', 'diagnostic'),
        'memory_minus_random': ('random', 'memory'),
        'memory_minus_fixed': (None, 'memory'),
    }
    rows = []
    for name, key in CONTRASTS.items():
        baseline, treatment = requirements[name]
        deltas, normalized = [], []
        for unit in units:
            treatment_decisions, _ = executed_probe_steps(absolute(unit[f'{treatment}_run']))
            baseline_decisions = treatment_decisions if baseline is None else executed_probe_steps(
                absolute(unit[f'{baseline}_run']))[0]
            if treatment_decisions == 4 and baseline_decisions == 4:
                delta = float(unit[key])
                deltas.append(delta)
                normalized.append(delta / min(treatment_decisions, baseline_decisions))
        rows.append({
            'contrast': name, 'complete_units': len(deltas),
            'median_delta': statistics.median(deltas) if deltas else None,
            'mean_delta': statistics.mean(deltas) if deltas else None,
            'median_delta_per_joint_executed_decision': (
                statistics.median(normalized) if normalized else None),
        })
    return rows


def replicate_stability_rows(units: list[dict]) -> list[dict]:
    rows = []
    for name, key in CONTRASTS.items():
        differences = []
        for task in sorted({unit['task'] for unit in units}):
            for seed in sorted({unit['seed'] for unit in units}):
                pair = [unit for unit in units if unit['task'] == task and unit['seed'] == seed]
                left = next(unit for unit in pair if unit['model_replicate'] == 'A')
                right = next(unit for unit in pair if unit['model_replicate'] == 'B')
                differences.append(float(left[key]) - float(right[key]))
        rows.append({
            'contrast': name, 'paired_replicates': len(differences),
            'identical_pairs': sum(abs(value) <= 1e-15 for value in differences),
            'median_absolute_A_minus_B': statistics.median(abs(value) for value in differences),
            'max_absolute_A_minus_B': max(abs(value) for value in differences),
        })
    return rows


def date_block_rows(units: list[dict]) -> list[dict]:
    rows = []
    for key in ('date_block_memory_minus_scalar', 'date_block_diagnostic_minus_memory'):
        values = [float(unit[key]['largest_positive_block_share']) for unit in units
                  if unit[key]['largest_positive_block_share'] is not None]
        rows.append({'comparison': key, 'non_null_units': len(values),
                     'median_concentration': statistics.median(values) if values else None,
                     'max_concentration': max(values) if values else None})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('result', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = absolute(args.result)
    result = read(source)
    output = absolute(args.output)
    output.mkdir(parents=True, exist_ok=False)
    units = result['paired_units']
    unit_fields = ['task', 'seed', 'model_replicate']
    unit_fields += [f'{arm}_mean_abs_ic' for arm in (*ARMS, 'fixed')]
    unit_fields += list(CONTRASTS)
    unit_fields += [f'{arm}_executed_decisions' for arm in ARMS]
    unit_fields += [f'{arm}_candidate_positions' for arm in ARMS]
    unit_rows = []
    executed_totals = {arm: {'executed_decisions': 0, 'candidate_positions': 0} for arm in ARMS}
    for unit in units:
        row = {'task': unit['task'], 'seed': unit['seed'], 'model_replicate': unit['model_replicate']}
        for arm in (*ARMS, 'fixed'):
            row[f'{arm}_mean_abs_ic'] = unit['select_metrics'][arm]['mean_abs_ic']
        for name, key in CONTRASTS.items():
            row[name] = unit[key]
        for arm in ARMS:
            decisions, positions = executed_probe_steps(absolute(unit[f'{arm}_run']))
            row[f'{arm}_executed_decisions'] = decisions
            row[f'{arm}_candidate_positions'] = positions
            executed_totals[arm]['executed_decisions'] += decisions
            executed_totals[arm]['candidate_positions'] += positions
        unit_rows.append(row)
    write_csv(output / 'unit_effects.csv', unit_rows, unit_fields)

    contrast_fields = ['contrast', 'median', 'mean', 'minimum', 'maximum', 'ci_lower', 'ci_upper',
                       'exact_task_sign_flip_p', 'holm_adjusted_p', 'positive_tasks',
                       'positive_units', 'zero_units', 'negative_units']
    contrast_rows = []
    for name, analysis in result['contrasts'].items():
        contrast_rows.append({
            'contrast': name, 'median': analysis['median'], 'mean': analysis['mean'],
            'minimum': analysis['min'], 'maximum': analysis['max'],
            'ci_lower': analysis['task_bootstrap_95pct_interval'][0],
            'ci_upper': analysis['task_bootstrap_95pct_interval'][1],
            'exact_task_sign_flip_p': analysis['exact_task_sign_flip_p'],
            'holm_adjusted_p': analysis.get('holm_adjusted_p'),
            'positive_tasks': analysis['positive_tasks'],
            'positive_units': analysis['delta_counts']['positive'],
            'zero_units': analysis['delta_counts']['zero'],
            'negative_units': analysis['delta_counts']['negative'],
        })
    write_csv(output / 'contrast_summary.csv', contrast_rows, contrast_fields)

    task_rows = []
    for name, analysis in result['contrasts'].items():
        for task, value in analysis['task_medians'].items():
            task_rows.append({'contrast': name, 'task': task, 'median_delta': value})
    write_csv(output / 'task_effects.csv', task_rows, ['contrast', 'task', 'median_delta'])

    cost_rows = []
    for arm in ARMS:
        usage = result['usage'][arm]
        cost_rows.append({'arm': arm, **usage, **executed_totals[arm]})
    write_csv(output / 'execution_costs.csv', cost_rows,
              ['arm', 'decisions', 'model_calls', 'candidate_budget', 'input_tokens',
               'output_tokens', 'executed_decisions', 'candidate_positions'])

    behavior_rows = []
    for comparison, metrics in result['behavior_select_deltas'].items():
        for metric, analysis in metrics.items():
            behavior_rows.append({'comparison': comparison, 'metric': metric, **analysis})
    write_csv(output / 'behavior_effects.csv', behavior_rows,
              ['comparison', 'metric', 'median', 'min', 'max'])

    auxiliary_rows = auxiliary_execution_rows(units)
    write_csv(output / 'auxiliary_execution_analysis.csv', auxiliary_rows,
              ['contrast', 'complete_units', 'median_delta', 'mean_delta',
               'median_delta_per_joint_executed_decision'])
    replicate_rows = replicate_stability_rows(units)
    write_csv(output / 'replicate_stability.csv', replicate_rows,
              ['contrast', 'paired_replicates', 'identical_pairs',
               'median_absolute_A_minus_B', 'max_absolute_A_minus_B'])
    write_csv(output / 'date_block_summary.csv', date_block_rows(units),
              ['comparison', 'non_null_units', 'median_concentration', 'max_concentration'])

    manifest = {
        'schema': 'AUTO-RESEARCH-PAPER-MATERIALS-1',
        'source_result': str(source),
        'protocol_version': result['protocol_version'], 'model': result['model'],
        'evidence_status': result['evidence_status'],
        'task_count': result['task_count'], 'seeds': result['seeds'],
        'model_replicates': result['model_replicates'],
        'files': ['unit_effects.csv', 'contrast_summary.csv', 'task_effects.csv',
                  'execution_costs.csv', 'behavior_effects.csv', 'auxiliary_execution_analysis.csv',
                  'replicate_stability.csv', 'date_block_summary.csv'],
        'model_calls': sum(result['usage'][arm]['model_calls'] for arm in ARMS[:3]),
        'output_tokens': sum(result['usage'][arm]['output_tokens'] for arm in ARMS[:3]),
        'failed_or_invalid_decisions': result['failed_or_invalid_decisions'],
        'invalid_external_attempts': result['invalid_external_attempts'],
        'execution_recoveries': result['execution_recoveries'],
        'score_window_read': False, 'independent_real_data_oos': False,
    }
    (output / 'paper_manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == '__main__':
    main()
