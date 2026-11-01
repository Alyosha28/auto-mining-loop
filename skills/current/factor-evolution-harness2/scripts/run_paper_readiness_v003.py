#!/usr/bin/env python3
"""Execute and analyze the V003 paper-readiness development panel."""
from __future__ import annotations

import argparse
import copy
import itertools
import json
import math
import random
import shutil
import statistics
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from diagnostic_results import date_block_diagnostic
from run_diagnostic_continuation import (COMPARE, constraints_satisfied, ensure_loop,
                                         failed_decisions, loop_command, now, read_json,
                                         read_protocol, run_logged, validate_internal_select_excluded)
from run_memory_component_ablation import ensure_component_loop
from run_research_loop import absolute, relative, write_json_new, write_state

UNIT_SCHEMA = 'AUTO-RESEARCH-PAPER-READINESS-UNIT-1'
RESULT_SCHEMA = 'AUTO-RESEARCH-PAPER-READINESS-RESULT-1'
PROTOCOL_SCHEMA = 'AUTO-RESEARCH-PAPER-READINESS-PROTOCOL-1'
MODEL_ARMS = ('scalar', 'memory', 'diagnostic')
ARMS = (*MODEL_ARMS, 'random')
CHECKS = ('common_support_verified', 'actual_model_calls_equal', 'actual_decisions_equal',
          'random_zero_calls_verified', 'split_verified', 'initial_identity_verified',
          'memory_contract_verified', 'diagnostic_contract_verified', 'frozen_champions_verified',
          'internal_select_excluded', 'task_constraints_satisfied')


def first_generation(output: Path) -> Path:
    return output / 'decisions' / f'{output.name}_d001' / 'generation'


def second_generation(output: Path) -> Path:
    return output / 'decisions' / f'{output.name}_d002' / 'generation'


def initial_identity(inputs: dict[str, Path]) -> tuple[bool, bool, bool]:
    states = {arm: read_json(first_generation(path) / 'research_state.json')
              for arm, path in inputs.items()}
    common = [{key: value for key, value in state.items()
               if key not in ('prompt_feedback', 'shared_memory_entries')}
              for state in states.values()]
    identity = all(item == common[0] for item in common[1:])
    for name in ('editable_surface.json', 'route_history_input.json'):
        payloads = [(first_generation(path) / name).read_bytes() for path in inputs.values()]
        identity = identity and all(payload == payloads[0] for payload in payloads[1:])
    traces = {arm: read_json(first_generation(path) / 'execution_trace.json')
              for arm, path in inputs.items()}
    identity = identity and all(trace.get('proposals') == [] for trace in traces.values())
    trace_common = [{key: value for key, value in trace.items() if key != 'scope'}
                    for trace in traces.values()]
    identity = identity and all(trace == trace_common[0] for trace in trace_common[1:])
    memories = {arm: read_json(first_generation(path) / 'shared_research_memory.json')
                for arm, path in inputs.items()}
    memory_ok = (not memories['scalar']['entries'] and not memories['random']['entries']
                 and bool(memories['memory']['entries'])
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


def prepare_execution_recovery(protocol: dict, output: Path, recovery: Path) -> None:
    if (recovery / 'loop_state.json').is_file():
        return
    state = read_json(output / 'loop_state.json')
    pending = state.get('pending') or {}
    source = absolute(pending.get('run_dir', output))
    generation = source / 'generation'
    if (state.get('stop_reason') != 'ROUTE_EXECUTION_FAILED' or pending.get('phase') != 'EXECUTE'
            or not (generation / 'edits.json').is_file()
            or not (generation / 'model_usage.json').is_file()
            or read_json(source / 'run_manifest.json').get('status') != 'PREPARED'):
        raise RuntimeError(f'{output} is not a recoverable persisted-proposal execution failure')
    recovery.mkdir(parents=True, exist_ok=False)
    write_json_new(recovery / 'contract.json', read_json(output / 'contract.json'))
    recovered = copy.deepcopy(state)
    target = recovery / 'decisions' / pending['run_id']
    target.parent.mkdir(parents=True, exist_ok=False)
    shutil.copytree(generation, target / 'generation')
    shutil.copy2(source / 'run_manifest.json', target / 'run_manifest.json')
    if (source / 'ledger.jsonl').is_file():
        shutil.copy2(source / 'ledger.jsonl', target / 'ledger.jsonl')
    recovered.update({'status': 'RUNNING', 'stop_reason': None,
                      'contract': str(recovery / 'contract.json'),
                      'recovery_from': relative(output)})
    recovered['pending']['run_dir'] = str(target)
    write_state(recovery / 'loop_state.json', recovered)


def prepare_timeout_recovery(protocol: dict, output: Path, recovery: Path) -> None:
    if (recovery / 'loop_state.json').is_file():
        return
    state = read_json(output / 'loop_state.json')
    pending = state.get('pending') or {}
    source = absolute(pending.get('run_dir', output))
    generation = source / 'generation'
    if (state.get('stop_reason') not in ('PROPOSER_TIMEOUT', 'PROPOSER_OUTCOME_UNKNOWN')
            or pending.get('phase') != 'PROPOSE'
            or (generation / 'edits.json').exists()
            or (generation / 'model_usage.json').exists()
            or read_json(source / 'run_manifest.json').get('status') != 'PREPARED'):
        raise RuntimeError(f'{output} is not a recoverable no-usage proposer timeout')
    recovery.mkdir(parents=True, exist_ok=False)
    write_json_new(recovery / 'contract.json', read_json(output / 'contract.json'))
    recovered = copy.deepcopy(state)
    target = recovery / 'decisions' / pending['run_id']
    target.parent.mkdir(parents=True, exist_ok=False)
    ignored = {'proposer_attempt.json', 'proposer_stdout.log', 'proposer_stderr.log',
               '._proposer_attempt.json', '._proposer_stdout.log', '._proposer_stderr.log'}
    shutil.copytree(generation, target / 'generation',
                    ignore=lambda directory, names: ignored.intersection(names))
    shutil.copy2(source / 'run_manifest.json', target / 'run_manifest.json')
    if (source / 'ledger.jsonl').is_file():
        shutil.copy2(source / 'ledger.jsonl', target / 'ledger.jsonl')
    recovered.update({'status': 'RUNNING', 'stop_reason': None,
                      'contract': str(recovery / 'contract.json'),
                      'recovery_from': relative(output)})
    recovered['pending']['run_dir'] = str(target)
    write_state(recovery / 'loop_state.json', recovered)


def ensure_random_loop(protocol: dict, task: dict, output: Path) -> tuple[Path, dict]:
    result_path = output / 'result.json'
    if not result_path.is_file():
        resume = (output / 'loop_state.json').is_file()
        command = loop_command(protocol, task, output, feedback='parent', resume=resume)
        environment = dict(protocol.get('proposer_environment') or {})
        environment['AUTO_RESEARCH_RANDOM_SEED'] = str(protocol['random_seed'])
        run_logged(command, output.parent, output.name, environment)
    result = read_json(result_path)
    if (result.get('stop_reason') != 'DECISION_BUDGET_EXHAUSTED'
            or result['usage']['decisions'] != protocol['shared_decisions']
            or result['usage']['model_calls'] != 0):
        raise RuntimeError(f'{output} did not complete the frozen random-search budget')
    return output, result


def ensure_arm(protocol: dict, task: dict, output: Path, arm: str) -> tuple[Path, dict, str | None, str | None]:
    feedback = 'diagnostic' if arm == 'diagnostic' else 'parent'
    try:
        if arm == 'random':
            actual, result = ensure_random_loop(protocol, task, output)
            return actual, result, None, None
        actual, result, channel = ensure_component_loop(protocol, task, output, feedback=feedback)
        return actual, result, channel, None
    except RuntimeError:
        result_path = output / 'result.json'
        state_path = output / 'loop_state.json'
        if not result_path.is_file() and not state_path.is_file():
            raise
        result = read_json(result_path if result_path.is_file() else state_path)
        if result.get('stop_reason') not in ('ROUTE_EXECUTION_FAILED', 'PROPOSER_TIMEOUT',
                                             'PROPOSER_OUTCOME_UNKNOWN'):
            raise
        reason = ('PROPOSER_TIMEOUT' if result['stop_reason'] in
                  ('PROPOSER_TIMEOUT', 'PROPOSER_OUTCOME_UNKNOWN') else result['stop_reason'])
    if reason == 'PROPOSER_TIMEOUT':
        recovery = output.with_name(output.name + '_TIMEOUT_RECOVERY_001')
        prepare_timeout_recovery(protocol, output, recovery)
    else:
        recovery = output.with_name(output.name + '_EXECUTION_RECOVERY_001')
        prepare_execution_recovery(protocol, output, recovery)
    if arm == 'random':
        actual, result = ensure_random_loop(protocol, task, recovery)
        return actual, result, None, relative(output)
    actual, result, channel = ensure_component_loop(protocol, task, recovery, feedback=feedback)
    return actual, result, channel, relative(output)


def infrastructure_reason(source: str) -> str:
    root = absolute(source)
    result_path = root / 'result.json'
    result = read_json(result_path if result_path.is_file() else root / 'loop_state.json')
    if result.get('stop_reason') in ('PROPOSER_TIMEOUT', 'PROPOSER_OUTCOME_UNKNOWN'):
        return 'retried the same prepared input after a no-usage timeout'
    return 'replayed the persisted proposal without a new model call'


def run_unit(protocol_path: Path, task_name: str, seed: int, replicate: str) -> dict:
    protocol = read_protocol(protocol_path)
    task = copy.deepcopy(next(item for item in protocol['tasks'] if item['task'] == task_name))
    if seed not in protocol['seeds'] or replicate not in protocol['model_replicates']:
        raise ValueError('seed or model replicate is outside the frozen panel')
    task['seed'] = seed
    if isinstance(task.get('initial_safe_trace'), dict):
        task['initial_safe_trace'] = task['initial_safe_trace'][str(seed)]
    version = protocol['protocol_version']
    output = absolute(protocol['run_root']) / f'PAPER_READINESS_{task_name}_S{seed}_R{replicate}_{version}'
    result_path = output / 'result.json'
    if result_path.is_file():
        return read_json(result_path)
    output.mkdir(parents=True, exist_ok=True)
    config = {'schema': UNIT_SCHEMA, 'protocol': relative(protocol_path), 'protocol_version': version,
              'task': task, 'seed': seed, 'model_replicate': replicate}
    config_path = output / 'config.json'
    if config_path.is_file():
        existing = read_json(config_path)
        if {key: value for key, value in existing.items() if key != 'created_at'} != config:
            raise ValueError(f'unit config differs from frozen protocol: {output}')
    else:
        write_json_new(config_path, {**config, 'created_at': now()})
    results, outputs, inputs = {}, {}, {}
    channel_recoveries, execution_recoveries = {}, {}
    for arm in ARMS:
        arm_protocol = copy.deepcopy(protocol)
        arm_task = copy.deepcopy(task)
        if arm in ('scalar', 'random'):
            arm_task['shared_memory'] = None
        if arm == 'random':
            arm_protocol['proposer'] = protocol['random_proposer']
            arm_protocol['model_id'] = protocol['random_model_id']
        initial = output / f'{task_name}_S{seed}_R{replicate}_{arm.upper()}'
        actual, result, channel, execution = ensure_arm(arm_protocol, arm_task, initial, arm)
        inputs[arm], outputs[arm], results[arm] = initial, actual, result
        if channel:
            channel_recoveries[arm] = channel
        if execution:
            execution_recoveries[arm] = execution
    initial_ok, memory_ok, diagnostic_ok = initial_identity(inputs)
    states = {arm: read_json(path / 'loop_state.json') for arm, path in outputs.items()}
    comparison_output = output / 'common_comparison'
    if comparison_output.exists() and not (comparison_output / 'result.json').is_file() and any(comparison_output.iterdir()):
        comparison_output = output / 'common_comparison_RECOVERY_001'
    if not (comparison_output / 'result.json').is_file():
        command = [sys.executable, str(COMPARE), '--output', str(comparison_output),
                   '--contract', str(absolute(protocol['continuation_contract'])),
                   '--seed', str(seed), '--budget', str(protocol['candidate_budget']),
                   '--dev-split', protocol['dev_split']]
        for arm in ARMS:
            command += ['--arm', f'{arm}={absolute(results[arm]["champion_harness"])}']
        command += ['--arm', f'fixed={absolute(task["initial_harness"])}']
        if task.get('features'):
            command += ['--features', ','.join(task['features'])]
        if protocol['coverage_log_only']:
            command.append('--coverage-log-only')
        if protocol['demo']:
            command.append('--demo')
        run_logged(command, output, 'common_comparison')
    comparison = read_json(comparison_output / 'result.json')
    deltas = {name: comparison['comparisons'][name]['select']['primary_delta']
              for name in ('memory', 'diagnostic', 'random', 'fixed')}
    split_ok = bool(comparison['dev_split']['seed'] == seed
                    and comparison['dev_split']['mode'] == protocol['dev_split']
                    and all(state['seed'] == seed and state['dev_split'] == protocol['dev_split']
                            for state in states.values()))
    checks = {
        'common_support_verified': comparison['common_support_verified'] is True,
        'actual_model_calls_equal': len({result['usage']['model_calls'] for result in
                                         [results[arm] for arm in MODEL_ARMS]}) == 1,
        'actual_decisions_equal': len({result['usage']['decisions'] for result in results.values()}) == 1,
        'random_zero_calls_verified': results['random']['usage']['model_calls'] == 0,
        'split_verified': split_ok,
        'initial_identity_verified': initial_ok,
        'memory_contract_verified': memory_ok,
        'diagnostic_contract_verified': diagnostic_ok,
        'frozen_champions_verified': all(read_json(absolute(result['champion_harness'])).get('schema') == 'EVO-HARNESS-1'
                                        for result in results.values()),
        'internal_select_excluded': validate_internal_select_excluded(list(outputs.values())),
        'task_constraints_satisfied': constraints_satisfied(task, comparison),
    }
    scalar_select = read_json(comparison_output / 'select/scalar.json')
    memory_select = read_json(comparison_output / 'select/memory.json')
    diagnostic_select = read_json(comparison_output / 'select/diagnostic.json')
    unit = {
        'schema': UNIT_SCHEMA, 'protocol_version': version, 'task': task_name, 'seed': seed,
        'model_replicate': replicate,
        'status': 'COMPLETED' if all(checks.values()) else 'PROTOCOL_INVALID',
        **{f'{arm}_run': relative(outputs[arm]) for arm in ARMS},
        'invalid_external_attempts': [
            {'arm': arm, 'source_run': source, 'reason': 'model identity differed from frozen Kimi'}
            for arm, source in channel_recoveries.items()],
        'execution_recoveries': [
            {'arm': arm, 'source_run': source, 'reason': infrastructure_reason(source)}
            for arm, source in execution_recoveries.items()],
        'comparison_run': relative(comparison_output),
        **{f'{arm}_champion': results[arm]['champion_harness'] for arm in ARMS},
        'select_memory_minus_scalar': deltas['memory'],
        'select_diagnostic_minus_memory': deltas['diagnostic'] - deltas['memory'],
        'select_memory_minus_random': deltas['memory'] - deltas['random'],
        'select_memory_minus_fixed': deltas['memory'] - deltas['fixed'],
        'select_scalar_minus_random': -deltas['random'],
        'select_scalar_minus_fixed': -deltas['fixed'],
        'select_metrics': {name: arm['select_metrics'] for name, arm in comparison['arms'].items()},
        'date_block_memory_minus_scalar': date_block_diagnostic(scalar_select, memory_select),
        'date_block_diagnostic_minus_memory': date_block_diagnostic(memory_select, diagnostic_select),
        'usage': {arm: results[arm]['usage'] for arm in ARMS},
        'failed_or_invalid_decisions': sum(
            failed_decisions(path) + int(states[arm].get('recovery_from') is not None)
            + int(arm in channel_recoveries) for arm, path in outputs.items()),
        **checks, 'score_window_read': False, 'independent_real_data_oos': False, 'finished_at': now(),
    }
    write_json_new(result_path, unit)
    return unit


def executed_probe_steps(loop: Path) -> tuple[int, int]:
    state = read_json(loop / 'loop_state.json')
    paths = {
        absolute(entry['child_run']) / 'step_record.json'
        for entry in state.get('history', [])
        if entry.get('status') == 'COMPLETED' and entry.get('child_run')
    }
    records = [read_json(path) for path in sorted(paths)]
    records = [record for record in records if record.get('probe_only') is True]
    return len(records), sum(int(record['adopted_probe']['metrics']['n_candidates']) for record in records)


def executed_positions(unit: dict) -> tuple[int, int, int]:
    positions = {arm: 0 for arm in ARMS}
    decisions = {arm: 0 for arm in ARMS}
    for arm in ARMS:
        decisions[arm], positions[arm] = executed_probe_steps(absolute(unit[f'{arm}_run']))
    return sum(positions[arm] for arm in MODEL_ARMS), positions['random'], sum(decisions.values())


def quantile(values: list[float], probability: float) -> float:
    values = sorted(values)
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return values[lower] * (1 - fraction) + values[upper] * fraction


def exact_sign_flip_p(groups: dict[str, list[float]], observed: float) -> float:
    tasks = sorted(groups)
    statistics_values = []
    for bits in itertools.product((-1.0, 1.0), repeat=len(tasks)):
        values = [sign * value for task, sign in zip(tasks, bits) for value in groups[task]]
        statistics_values.append(statistics.median(values))
    return sum(abs(value) >= abs(observed) - 1e-15 for value in statistics_values) / len(statistics_values)


def holm(p_values: dict[str, float]) -> dict[str, float]:
    ordered = sorted(p_values, key=p_values.get)
    adjusted, running = {}, 0.0
    for rank, key in enumerate(ordered):
        running = max(running, min(1.0, (len(ordered) - rank) * p_values[key]))
        adjusted[key] = running
    return adjusted


def contrast_analysis(units: list[dict], tasks: list[str], key: str,
                      bootstrap_indices: list[tuple[int, ...]]) -> dict:
    values = [float(unit[key]) for unit in units]
    groups = {task: [float(unit[key]) for unit in units if unit['task'] == task] for task in tasks}
    observed = statistics.median(values)
    bootstrap = []
    for indices in bootstrap_indices:
        sample = [value for index in indices for value in groups[tasks[index]]]
        bootstrap.append(statistics.median(sample))
    return {
        'median': observed, 'mean': statistics.mean(values), 'min': min(values), 'max': max(values),
        'task_medians': {task: statistics.median(group) for task, group in groups.items()},
        'positive_tasks': sum(statistics.median(group) > 0 for group in groups.values()),
        'task_bootstrap_95pct_interval': [quantile(bootstrap, 0.025), quantile(bootstrap, 0.975)],
        'exact_task_sign_flip_p': exact_sign_flip_p(groups, observed),
        'delta_counts': {'positive': sum(value > 0 for value in values),
                         'zero': sum(value == 0 for value in values),
                         'negative': sum(value < 0 for value in values)},
    }


def aggregate_units(units: list[dict], protocol: dict) -> dict:
    tasks = [task['task'] for task in protocol['tasks']]
    expected = {(task, seed, replicate) for task in tasks for seed in protocol['seeds']
                for replicate in protocol['model_replicates']}
    observed = {(unit['task'], unit['seed'], unit['model_replicate']) for unit in units}
    if len(units) != len(expected) or observed != expected:
        raise ValueError('V003 requires exactly 24 task-split-replicate units')
    valid = all(unit['status'] == 'COMPLETED' and all(unit.get(key) is True for key in CHECKS)
                for unit in units)
    rng = random.Random(protocol['analysis']['bootstrap_seed'])
    bootstrap_indices = [tuple(rng.randrange(len(tasks)) for _ in tasks)
                         for _ in range(protocol['analysis']['task_bootstrap_samples'])]
    keys = {'memory_minus_scalar': 'select_memory_minus_scalar',
            'diagnostic_minus_memory': 'select_diagnostic_minus_memory',
            'memory_minus_random': 'select_memory_minus_random',
            'memory_minus_fixed': 'select_memory_minus_fixed'}
    contrasts = {name: contrast_analysis(units, tasks, key, bootstrap_indices)
                 for name, key in keys.items()}
    component_p = {name: contrasts[name]['exact_task_sign_flip_p']
                   for name in ('memory_minus_scalar', 'diagnostic_minus_memory')}
    adjusted = holm(component_p)
    for name, value in adjusted.items():
        contrasts[name]['holm_adjusted_p'] = value
    usage = {arm: {'decisions': 0, 'model_calls': 0, 'candidate_budget': 0,
                   'input_tokens': 0, 'output_tokens': 0} for arm in ARMS}
    for unit in units:
        for arm in ARMS:
            for key in usage[arm]:
                usage[arm][key] += int(unit['usage'][arm].get(key, 0))
    executed = [executed_positions(unit) for unit in units]
    behavior = {}
    for left, right in (('memory', 'scalar'), ('diagnostic', 'memory'), ('memory', 'random')):
        comparison = {}
        for metric in ('coverage', 'pass_rate', 'novelty', 'duplicate_rate', 'turnover_proxy'):
            values = [float(unit['select_metrics'][left][metric])
                      - float(unit['select_metrics'][right][metric]) for unit in units]
            comparison[metric] = {'median': statistics.median(values), 'min': min(values), 'max': max(values)}
        behavior[f'{left}_minus_{right}'] = comparison
    return {
        'paired_units': units, 'all_protocol_checks_passed': valid,
        'evidence_status': 'development-panel complete' if valid else 'protocol-invalid',
        'contrasts': contrasts, 'behavior_select_deltas': behavior,
        'usage': {**usage, 'model_candidate_positions': sum(item[0] for item in executed),
                  'random_candidate_positions': sum(item[1] for item in executed),
                  'executed_decisions': sum(item[2] for item in executed),
                  'maximum_model_calls': protocol['maximum_model_calls'],
                  'maximum_model_candidate_positions': protocol['maximum_model_candidate_positions'],
                  'maximum_random_candidate_positions': protocol['maximum_random_candidate_positions']},
        'failed_or_invalid_decisions': sum(unit['failed_or_invalid_decisions'] for unit in units),
        'invalid_external_attempts': [dict(attempt, task=unit['task'], seed=unit['seed'],
                                           model_replicate=unit['model_replicate'])
                                      for unit in units for attempt in unit.get('invalid_external_attempts', [])],
        'execution_recoveries': [dict(attempt, task=unit['task'], seed=unit['seed'],
                                      model_replicate=unit['model_replicate'])
                                 for unit in units for attempt in unit.get('execution_recoveries', [])],
        'score_window_read': False, 'independent_real_data_oos': False,
    }


def protocol_record(protocol_path: Path) -> None:
    protocol = read_protocol(protocol_path)
    if protocol.get('schema') != PROTOCOL_SCHEMA:
        raise ValueError(f'protocol must use {PROTOCOL_SCHEMA}')
    output = absolute(protocol['run_root']) / f'PAPER_READINESS_PROTOCOL_{protocol["protocol_version"]}'
    record = {'protocol_file': relative(protocol_path), 'protocol': protocol}
    path = output / 'config.json'
    if path.is_file():
        if read_json(path) != record:
            raise ValueError('frozen V003 protocol record differs from supplied protocol')
        return
    write_json_new(path, record)


def aggregate(protocol_path: Path, *, read_only: bool = False) -> dict:
    protocol = read_protocol(protocol_path)
    root = absolute(protocol['run_root'])
    if read_only:
        units = [read_json(root / f'PAPER_READINESS_{task["task"]}_S{seed}_R{replicate}_{protocol["protocol_version"]}'
                           / 'result.json')
                 for task in protocol['tasks'] for seed in protocol['seeds']
                 for replicate in protocol['model_replicates']]
    else:
        units = [run_unit(protocol_path, task['task'], seed, replicate)
                 for task in protocol['tasks'] for seed in protocol['seeds']
                 for replicate in protocol['model_replicates']]
    result = {'schema': RESULT_SCHEMA, 'protocol_version': protocol['protocol_version'],
              'model': protocol['model_id'], 'task_count': len(protocol['tasks']),
              'seeds': protocol['seeds'], 'model_replicates': protocol['model_replicates'],
              **aggregate_units(units, protocol), 'finished_at': now()}
    version = protocol['protocol_version']
    write_json_new(root / f'PAPER_READINESS_RESULT_{version}/result.json', result)
    summary = {key: value for key, value in result.items() if key != 'paired_units'}
    write_json_new(root / f'PAPER_READINESS_RESULT_{version}_EXECUTION_SUMMARY_001/result.json', summary)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    parser.add_argument('--task')
    parser.add_argument('--seed', type=int)
    parser.add_argument('--replicate')
    parser.add_argument('--aggregate-only', action='store_true')
    args = parser.parse_args()
    protocol_record(args.protocol)
    if args.aggregate_only:
        result = aggregate(args.protocol, read_only=True)
    elif args.task or args.seed is not None or args.replicate:
        if not args.task or args.seed is None or not args.replicate:
            parser.error('--task, --seed and --replicate must be supplied together')
        result = run_unit(args.protocol, args.task, args.seed, args.replicate)
    else:
        result = aggregate(args.protocol)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
