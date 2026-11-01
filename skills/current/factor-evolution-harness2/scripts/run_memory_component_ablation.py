#!/usr/bin/env python3
"""Execute and aggregate the V002 fresh-task memory component ablation."""
from __future__ import annotations

import argparse
import copy
import json
import math
import statistics
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from diagnostic_results import date_block_diagnostic
from run_diagnostic_continuation import (COMPARE, constraints_satisfied, ensure_loop,
                                         failed_decisions, now, read_json, read_protocol,
                                         run_logged, validate_internal_select_excluded)
from run_research_loop import absolute, relative, write_json_new

UNIT_SCHEMA = 'AUTO-RESEARCH-MEMORY-COMPONENT-UNIT-1'
RESULT_SCHEMA = 'AUTO-RESEARCH-MEMORY-COMPONENT-RESULT-1'
PROTOCOL_SCHEMA = 'AUTO-RESEARCH-MEMORY-COMPONENT-PROTOCOL-1'
ARMS = ('scalar', 'memory', 'diagnostic')
CHECKS = ('common_support_verified', 'actual_model_calls_equal', 'actual_decisions_equal',
          'split_verified', 'initial_identity_verified', 'memory_contract_verified',
          'diagnostic_contract_verified', 'frozen_champions_verified',
          'internal_select_excluded', 'task_constraints_satisfied')


def first_generation(output: Path) -> Path:
    return output / 'decisions' / f'{output.name}_d001' / 'generation'


def second_generation(output: Path) -> Path:
    return output / 'decisions' / f'{output.name}_d002' / 'generation'


def initial_identity(inputs: dict[str, Path]) -> tuple[bool, bool, bool]:
    states = {arm: read_json(first_generation(path) / 'research_state.json')
              for arm, path in inputs.items()}
    common = []
    for arm in ARMS:
        common.append({key: value for key, value in states[arm].items()
                       if key not in ('prompt_feedback', 'shared_memory_entries')})
    identity = common[0] == common[1] == common[2]
    for name in ('editable_surface.json', 'route_history_input.json'):
        payloads = [(first_generation(path) / name).read_bytes() for path in inputs.values()]
        identity = identity and payloads[0] == payloads[1] == payloads[2]
    traces = {arm: read_json(first_generation(path) / 'execution_trace.json')
              for arm, path in inputs.items()}
    identity = identity and (first_generation(inputs['scalar']) / 'execution_trace.json').read_bytes() == \
        (first_generation(inputs['memory']) / 'execution_trace.json').read_bytes()
    identity = identity and all(trace.get('proposals') == [] for trace in traces.values())
    trace_common = [{key: value for key, value in trace.items() if key != 'scope'}
                    for trace in traces.values()]
    identity = identity and trace_common[0] == trace_common[1] == trace_common[2]
    memories = {arm: read_json(first_generation(path) / 'shared_research_memory.json')
                for arm, path in inputs.items()}
    memory_ok = (not memories['scalar']['entries'] and bool(memories['memory']['entries'])
                 and (first_generation(inputs['memory']) / 'shared_research_memory.json').read_bytes()
                 == (first_generation(inputs['diagnostic']) / 'shared_research_memory.json').read_bytes())
    diagnostic_ok = True
    for arm, path in inputs.items():
        generation = second_generation(path)
        if not generation.is_dir():
            continue
        state = read_json(generation / 'research_state.json')
        trace = read_json(generation / 'execution_trace.json')
        expected = 'diagnostic' if arm == 'diagnostic' else 'parent'
        diagnostic_ok = diagnostic_ok and state['prompt_feedback'] == expected
        if arm != 'diagnostic':
            diagnostic_ok = diagnostic_ok and trace.get('proposals') == []
    return identity, memory_ok, diagnostic_ok


def ensure_component_loop(protocol: dict, task: dict, output: Path, *, feedback: str):
    state_path = output / 'loop_state.json'
    if state_path.is_file() and not (output / 'result.json').is_file():
        state = read_json(state_path)
        if state.get('stop_reason') == 'PROPOSER_COMMAND_FAILED':
            pending = state.get('pending') or {}
            generation = absolute(pending.get('run_dir', output)) / 'generation'
            log_path = generation / 'proposer_stderr.log'
            details = log_path.read_text(encoding='utf-8') if log_path.is_file() else ''
            wrong_model = ('model identity is missing or differs' in details
                           or ('modelUsage' in details and protocol['model_id'] not in details))
            if wrong_model:
                recovery = output.with_name(output.name + '_CHANNEL_RECOVERY_001')
                actual, result = ensure_loop(protocol, task, recovery, feedback=feedback)
                return actual, result, relative(output)
    actual, result = ensure_loop(protocol, task, output, feedback=feedback)
    return actual, result, None


def comparison_directory(output: Path) -> Path:
    base = output / 'common_comparison'
    for index in range(100):
        candidate = base if index == 0 else output / f'common_comparison_RECOVERY_{index:03d}'
        if (candidate / 'result.json').is_file() or not candidate.exists() or not any(candidate.iterdir()):
            return candidate
    raise RuntimeError(f'no new comparison recovery directory available under {output}')


def run_unit(protocol_path: Path, task_name: str, seed: int) -> dict:
    protocol = read_protocol(protocol_path)
    task = copy.deepcopy(next(item for item in protocol['tasks'] if item['task'] == task_name))
    if seed not in protocol['seeds']:
        raise ValueError(f'seed {seed} is outside the frozen panel')
    task['seed'] = seed
    if isinstance(task.get('initial_safe_trace'), dict):
        task['initial_safe_trace'] = task['initial_safe_trace'][str(seed)]
    version = protocol['protocol_version']
    root = absolute(protocol['run_root'])
    output = root / f'MEMORY_COMPONENT_{task_name}_S{seed}_{version}'
    result_path = output / 'result.json'
    if result_path.is_file():
        return read_json(result_path)
    output.mkdir(parents=True, exist_ok=True)
    config = {'schema': UNIT_SCHEMA, 'protocol': relative(protocol_path),
              'protocol_version': version, 'task': task, 'seed': seed}
    config_path = output / 'config.json'
    if config_path.is_file():
        existing = read_json(config_path)
        if {key: value for key, value in existing.items() if key != 'created_at'} != config:
            raise ValueError(f'unit config differs from frozen protocol: {output}')
    else:
        write_json_new(config_path, {**config, 'created_at': now()})
    results, outputs, inputs = {}, {}, {}
    channel_recoveries = {}
    for arm in ARMS:
        arm_task = copy.deepcopy(task)
        if arm == 'scalar':
            arm_task['shared_memory'] = None
        feedback = 'diagnostic' if arm == 'diagnostic' else 'parent'
        initial = output / f'{task_name}_S{seed}_{arm.upper()}'
        actual, result, channel_source = ensure_component_loop(protocol, arm_task, initial, feedback=feedback)
        inputs[arm] = initial
        outputs[arm] = actual
        results[arm] = result
        if channel_source:
            channel_recoveries[arm] = channel_source
    initial_ok, memory_ok, diagnostic_ok = initial_identity(inputs)
    states = {arm: read_json(path / 'loop_state.json') for arm, path in outputs.items()}
    comparison_output = comparison_directory(output)
    if not (comparison_output / 'result.json').is_file():
        command = [
            sys.executable, str(COMPARE), '--output', str(comparison_output),
            '--contract', str(absolute(protocol['continuation_contract'])),
            '--seed', str(seed), '--budget', str(protocol['candidate_budget']),
            '--dev-split', protocol['dev_split'],
            '--arm', f'scalar={absolute(results["scalar"]["champion_harness"])}',
            '--arm', f'memory={absolute(results["memory"]["champion_harness"])}',
            '--arm', f'diagnostic={absolute(results["diagnostic"]["champion_harness"])}',
            '--arm', f'fixed={absolute(task["initial_harness"])}',
        ]
        if task.get('features'):
            command += ['--features', ','.join(task['features'])]
        if protocol['coverage_log_only']:
            command.append('--coverage-log-only')
        if protocol['demo']:
            command.append('--demo')
        run_logged(command, output, 'common_comparison')
    comparison = read_json(comparison_output / 'result.json')
    deltas = {name: comparison['comparisons'][name]['select']['primary_delta']
              for name in ('memory', 'diagnostic', 'fixed')}
    split_ok = bool(comparison['dev_split']['seed'] == seed
                    and comparison['dev_split']['mode'] == protocol['dev_split']
                    and all(state['seed'] == seed and state['dev_split'] == protocol['dev_split']
                            for state in states.values()))
    checks = {
        'common_support_verified': comparison['common_support_verified'] is True,
        'actual_model_calls_equal': len({result['usage']['model_calls'] for result in results.values()}) == 1,
        'actual_decisions_equal': len({result['usage']['decisions'] for result in results.values()}) == 1,
        'split_verified': split_ok,
        'initial_identity_verified': initial_ok,
        'memory_contract_verified': memory_ok,
        'diagnostic_contract_verified': diagnostic_ok,
        'frozen_champions_verified': all(
            read_json(absolute(result['champion_harness'])).get('schema') == 'EVO-HARNESS-1'
            for result in results.values()),
        'internal_select_excluded': validate_internal_select_excluded(list(outputs.values())),
        'task_constraints_satisfied': constraints_satisfied(task, comparison),
    }
    scalar_select = read_json(comparison_output / 'select/scalar.json')
    memory_select = read_json(comparison_output / 'select/memory.json')
    diagnostic_select = read_json(comparison_output / 'select/diagnostic.json')
    unit = {
        'schema': UNIT_SCHEMA, 'protocol_version': version, 'task': task_name, 'seed': seed,
        'status': 'COMPLETED' if all(checks.values()) else 'PROTOCOL_INVALID',
        'scalar_run': relative(outputs['scalar']), 'memory_run': relative(outputs['memory']),
        'diagnostic_run': relative(outputs['diagnostic']),
        'scalar_recovered_from': states['scalar'].get('recovery_from'),
        'memory_recovered_from': states['memory'].get('recovery_from'),
        'diagnostic_recovered_from': states['diagnostic'].get('recovery_from'),
        'invalid_external_attempts': [
            {'arm': arm, 'source_run': source, 'reason': 'model identity differed from frozen Kimi'}
            for arm, source in channel_recoveries.items()],
        'comparison_run': relative(comparison_output),
        'scalar_champion': results['scalar']['champion_harness'],
        'memory_champion': results['memory']['champion_harness'],
        'diagnostic_champion': results['diagnostic']['champion_harness'],
        'select_memory_minus_scalar': deltas['memory'],
        'select_diagnostic_minus_memory': deltas['diagnostic'] - deltas['memory'],
        'select_memory_minus_fixed': deltas['memory'] - deltas['fixed'],
        'select_scalar_minus_fixed': -deltas['fixed'],
        'select_diagnostic_minus_scalar': deltas['diagnostic'],
        'select_diagnostic_minus_fixed': deltas['diagnostic'] - deltas['fixed'],
        'select_metrics': {name: arm['select_metrics'] for name, arm in comparison['arms'].items()},
        'date_block_memory_minus_scalar': date_block_diagnostic(scalar_select, memory_select),
        'date_block_diagnostic_minus_memory': date_block_diagnostic(memory_select, diagnostic_select),
        'usage': {arm: results[arm]['usage'] for arm in ARMS},
        'failed_or_invalid_decisions': sum(
            failed_decisions(path) + int(states[arm].get('recovery_from') is not None)
            + int(arm in channel_recoveries)
            for arm, path in outputs.items()),
        **checks,
        'score_window_read': False, 'independent_real_data_oos': False, 'finished_at': now(),
    }
    write_json_new(result_path, unit)
    return unit


def without_channel_override(record: dict) -> dict:
    neutral = copy.deepcopy(record)
    protocol = neutral['protocol']
    protocol.get('proposer_environment', {}).pop('AUTO_RESEARCH_CLAUDE_CLI_MODEL', None)
    if isinstance(protocol.get('generation'), dict):
        protocol['generation']['cli_model_override'] = None
    return neutral


def protocol_record(protocol_path: Path) -> dict:
    protocol = read_protocol(protocol_path)
    if protocol.get('schema') != PROTOCOL_SCHEMA:
        raise ValueError(f'protocol must use {PROTOCOL_SCHEMA}')
    record = {'protocol_file': relative(protocol_path), 'protocol': protocol,
              'shared_contract': read_json(absolute(protocol['shared_contract'])),
              'continuation_contract': read_json(absolute(protocol['continuation_contract']))}
    output = absolute(protocol['run_root']) / f'MEMORY_COMPONENT_PROTOCOL_{protocol["protocol_version"]}'
    path = output / 'config.json'
    if path.is_file():
        existing = read_json(path)
        if existing != record:
            if without_channel_override(existing) != without_channel_override(record):
                raise ValueError('frozen V002 protocol record differs from supplied protocol')
            pin = {
                'schema': 'AUTO-RESEARCH-MEMORY-CHANNEL-PIN-1',
                'model_id': protocol['model_id'],
                'cli_model_override': protocol['generation']['cli_model_override'],
                'base_protocol_record': relative(path),
                'registered_at': now(),
            }
            pin_path = output / 'channel_pin.json'
            if pin_path.is_file():
                existing_pin = read_json(pin_path)
                if (existing_pin['model_id'] != pin['model_id']
                        or existing_pin['cli_model_override'] != pin['cli_model_override']):
                    raise ValueError('V002 channel pin differs from the registered model')
            else:
                write_json_new(pin_path, pin)
    else:
        write_json_new(path, record)
    return record


def executed_positions(unit: dict) -> tuple[int, int]:
    positions, decisions = 0, 0
    for key in ('scalar_run', 'memory_run', 'diagnostic_run'):
        for path in absolute(unit[key]).glob('decisions/*/child/step/step_record.json'):
            record = read_json(path)
            if record.get('probe_only') is True:
                decisions += 1
                positions += int(record['adopted_probe']['metrics']['n_candidates'])
    return positions, decisions


def aggregate_units(units: list[dict], tasks: list[str], seeds: list[int]) -> dict:
    expected = {(task, seed) for task in tasks for seed in seeds}
    if len(units) != len(expected) or {(unit['task'], unit['seed']) for unit in units} != expected:
        raise ValueError('V002 requires exactly twelve task-seed units')
    valid = all(unit['status'] == 'COMPLETED' and all(unit.get(key) is True for key in CHECKS)
                for unit in units)
    deltas = [{'task': unit['task'], 'seed': unit['seed'],
               'delta': float(unit['select_memory_minus_scalar'])}
              for unit in sorted(units, key=lambda item: (item['task'], item['seed']))]
    if any(not math.isfinite(item['delta']) for item in deltas):
        raise ValueError('memory-minus-scalar deltas must be finite')
    median = statistics.median(item['delta'] for item in deltas)
    task_medians = {task: statistics.median(item['delta'] for item in deltas if item['task'] == task)
                    for task in tasks}
    leave_one_out = {excluded: statistics.median(
        item['delta'] for item in deltas if item['task'] != excluded) for excluded in tasks}
    positive = sum(value > 0 for value in task_medians.values())
    supported = bool(valid and median > 0 and positive >= 3
                     and all(value > 0 for value in leave_one_out.values()))
    usage = {'decisions': 0, 'model_calls': 0, 'candidate_budget': 0,
             'input_tokens': 0, 'output_tokens': 0}
    for unit in units:
        for arm in ARMS:
            for key in usage:
                usage[key] += int(unit['usage'][arm].get(key, 0))
    executed = [executed_positions(unit) for unit in units]
    auxiliary = {}
    for key in ('select_memory_minus_fixed', 'select_scalar_minus_fixed',
                'select_diagnostic_minus_memory', 'select_diagnostic_minus_fixed'):
        values = [float(unit[key]) for unit in units]
        auxiliary[key] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
    behavior = {}
    for left, right in (('memory', 'scalar'), ('diagnostic', 'memory')):
        comparison = {}
        for metric in ('coverage', 'pass_rate', 'novelty', 'duplicate_rate', 'turnover_proxy'):
            values = [float(unit['select_metrics'][left][metric])
                      - float(unit['select_metrics'][right][metric]) for unit in units]
            comparison[metric] = {'median': statistics.median(values),
                                  'min': min(values), 'max': max(values)}
        behavior[f'{left}_minus_{right}'] = comparison
    concentration = {}
    for key in ('date_block_memory_minus_scalar', 'date_block_diagnostic_minus_memory'):
        values = [float(unit[key]['largest_positive_block_share']) for unit in units
                  if unit[key]['largest_positive_block_share'] is not None]
        concentration[key] = {'median': statistics.median(values) if values else None,
                              'max': max(values) if values else None,
                              'non_null_units': len(values)}
    return {
        'paired_units': units, 'paired_deltas': deltas, 'median_paired_delta': median,
        'task_seed_medians': task_medians, 'tasks_with_positive_seed_median': positive,
        'leave_one_task_out_medians': leave_one_out,
        'all_protocol_checks_passed': valid,
        'evidence_status': ('development-panel supported' if supported else
                            'development-panel null' if valid else 'protocol-invalid'),
        'auxiliary_select_deltas': auxiliary,
        'delta_counts': {
            'positive': sum(item['delta'] > 0 for item in deltas),
            'zero': sum(item['delta'] == 0 for item in deltas),
            'negative': sum(item['delta'] < 0 for item in deltas),
        },
        'behavior_select_deltas': behavior,
        'date_block_concentration': concentration,
        'recovery_units': [
            {'task': unit['task'], 'seed': unit['seed'],
             'scalar_recovered_from': unit.get('scalar_recovered_from'),
             'memory_recovered_from': unit.get('memory_recovered_from'),
             'diagnostic_recovered_from': unit.get('diagnostic_recovered_from')}
            for unit in units if any(unit.get(key) for key in
                                     ('scalar_recovered_from', 'memory_recovered_from',
                                      'diagnostic_recovered_from'))],
        'comparison_recoveries': [
            {'task': unit['task'], 'seed': unit['seed'], 'comparison_run': unit['comparison_run']}
            for unit in units if 'RECOVERY' in unit.get('comparison_run', '')],
        'usage': {**usage, 'executed_candidate_positions': sum(item[0] for item in executed),
                  'executed_decisions': sum(item[1] for item in executed),
                  'maximum_candidate_positions': 864},
        'failed_or_invalid_decisions': sum(unit['failed_or_invalid_decisions'] for unit in units),
        'invalid_external_attempts': [dict(attempt, task=unit['task'], seed=unit['seed'])
                                      for unit in units
                                      for attempt in unit.get('invalid_external_attempts', [])],
        'score_window_read': False, 'independent_real_data_oos': False,
    }


def aggregate(protocol_path: Path, *, read_only: bool = False) -> dict:
    protocol = read_protocol(protocol_path)
    tasks = [task['task'] for task in protocol['tasks']]
    root = absolute(protocol['run_root'])
    if read_only:
        units = [read_json(root / f'MEMORY_COMPONENT_{task}_S{seed}_{protocol["protocol_version"]}'
                           / 'result.json') for task in tasks for seed in protocol['seeds']]
    else:
        units = [run_unit(protocol_path, task, seed) for task in tasks for seed in protocol['seeds']]
    result = {'schema': RESULT_SCHEMA, 'protocol_version': protocol['protocol_version'],
              'model': protocol['model_id'], 'task_count': len(tasks), 'seeds': protocol['seeds'],
              **aggregate_units(units, tasks, protocol['seeds']), 'finished_at': now()}
    version = protocol['protocol_version']
    write_json_new(root / f'MEMORY_COMPONENT_RESULT_{version}/result.json', result)
    summary = {key: value for key, value in result.items() if key != 'paired_units'}
    write_json_new(root / f'MEMORY_COMPONENT_RESULT_{version}_EXECUTION_SUMMARY_001/result.json', summary)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--task')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--aggregate-only', action='store_true')
    args = parser.parse_args()
    protocol_record(args.protocol)
    if args.aggregate_only:
        result = aggregate(args.protocol, read_only=True)
    elif args.task or args.seed is not None:
        if not args.task or args.seed is None:
            parser.error('--task and --seed must be supplied together')
        result = run_unit(args.protocol, args.task, args.seed)
    else:
        result = aggregate(args.protocol)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
