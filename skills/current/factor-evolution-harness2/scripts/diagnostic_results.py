#!/usr/bin/env python3
"""Pure aggregation helpers for paired diagnostic-continuation results."""
from __future__ import annotations

import math
import statistics


def daily_absolute_ic(probe: dict) -> dict[str, float]:
    rows = probe.get('candidates') or []
    if not rows or any(not isinstance(row.get('ic_by_date'), dict) for row in rows):
        raise ValueError('date-block diagnostics require ic_by_date for every candidate')
    dates = set.intersection(*(set(row['ic_by_date']) for row in rows))
    if not dates:
        raise ValueError('date-block diagnostics have no common candidate dates')
    return {
        date: sum(abs(float(row['ic_by_date'][date])) for row in rows) / len(rows)
        for date in dates
    }


def date_block_diagnostic(parent_probe: dict, diagnostic_probe: dict,
                          block_size: int = 4) -> dict:
    parent = daily_absolute_ic(parent_probe)
    diagnostic = daily_absolute_ic(diagnostic_probe)
    if set(parent) != set(diagnostic):
        raise ValueError('paired date-block diagnostics require identical dates')
    rows = [{'date': date, 'mean_daily_abs_ic_delta': diagnostic[date] - parent[date]}
            for date in sorted(parent)]
    blocks = []
    for start in range(0, len(rows), block_size):
        group = rows[start:start + block_size]
        blocks.append({
            'start': group[0]['date'], 'end': group[-1]['date'], 'n_dates': len(group),
            'mean_daily_abs_ic_delta': sum(
                row['mean_daily_abs_ic_delta'] for row in group) / len(group),
        })
    positive = sum(max(block['mean_daily_abs_ic_delta'], 0.0) for block in blocks)
    concentration = (max(max(block['mean_daily_abs_ic_delta'], 0.0) for block in blocks) / positive
                     if positive > 0 else None)
    return {'metric': 'mean_daily_abs_ic_delta', 'block_size_dates': block_size,
            'dates': rows, 'blocks': blocks, 'largest_positive_block_share': concentration}


def aggregate_units(units: list[dict], tasks: list[str], seeds: list[int]) -> dict:
    expected = {(task, seed) for task in tasks for seed in seeds}
    observed = {(unit.get('task'), unit.get('seed')) for unit in units}
    if len(units) != len(expected) or observed != expected:
        raise ValueError(f'paired panel must contain exactly {sorted(expected)}')
    protocol_checks = {
        'common_support_verified', 'actual_model_calls_equal', 'actual_decisions_equal',
        'split_verified', 'branchpoint_identity_verified', 'shared_memory_verified',
        'frozen_champions_verified', 'internal_select_excluded', 'task_constraints_satisfied',
    }
    protocol_valid = all(unit.get('status') == 'COMPLETED'
                         and all(unit.get(key) is True for key in protocol_checks)
                         for unit in units)
    deltas = []
    for unit in sorted(units, key=lambda item: (item['task'], item['seed'])):
        value = float(unit['select_diagnostic_minus_parent'])
        if not math.isfinite(value):
            raise ValueError('paired select delta must be finite')
        deltas.append({'task': unit['task'], 'seed': unit['seed'], 'delta': value})
    median_delta = statistics.median(item['delta'] for item in deltas)
    task_medians = {}
    for task in tasks:
        task_medians[task] = statistics.median(
            item['delta'] for item in deltas if item['task'] == task)
    leave_one_out = {}
    for excluded in tasks:
        leave_one_out[excluded] = statistics.median(
            item['delta'] for item in deltas if item['task'] != excluded)
    positive_tasks = sum(value > 0 for value in task_medians.values())
    supported = bool(protocol_valid and median_delta > 0 and positive_tasks >= 3
                     and all(value > 0 for value in leave_one_out.values()))
    evidence_status = ('development-panel supported' if supported else
                       'development-panel null' if protocol_valid else 'protocol-invalid')
    usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0,
             'candidate_budget': 0, 'decisions': 0}
    for unit in units:
        for arm in ('shared', 'parent', 'diagnostic'):
            for key in usage:
                usage[key] += int(unit['usage'][arm].get(key, 0))
    return {
        'paired_units': units,
        'paired_deltas': deltas,
        'median_paired_delta': median_delta,
        'task_seed_medians': task_medians,
        'tasks_with_positive_seed_median': positive_tasks,
        'leave_one_task_out_medians': leave_one_out,
        'all_protocol_checks_passed': protocol_valid,
        'evidence_status': evidence_status,
        'usage': usage,
        'failed_or_invalid_decisions': sum(
            int(unit.get('failed_or_invalid_decisions', 0)) for unit in units),
        'score_window_read': False,
        'independent_real_data_oos': False,
    }
