#!/usr/bin/env python3
"""Execute or aggregate the frozen shared-exploration diagnostic-continuation panel."""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from diagnostic_results import aggregate_units, date_block_diagnostic
from evo_harness import harness_hash
from research_contract import load_contract
from run_research_loop import absolute, file_hash, relative, write_json_new, write_state

LOOP = SCRIPTS / 'run_research_loop.py'
COMPARE = SCRIPTS / 'compare_harnesses.py'
UNIT_SCHEMA = 'AUTO-RESEARCH-DIAGNOSTIC-CONTINUATION-UNIT-1'
RESULT_SCHEMA = 'AUTO-RESEARCH-DIAGNOSTIC-CONTINUATION-RESULT-1'
PROTOCOL_SCHEMA = 'AUTO-RESEARCH-DIAGNOSTIC-CONTINUATION-PROTOCOL-1'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path | str) -> dict:
    return json.loads(absolute(path).read_text(encoding='utf-8'))


def read_protocol(path: Path | str) -> dict:
    protocol = read_json(path)
    if protocol.get('task_files'):
        protocol['tasks'] = [read_json(absolute(path).parent / filename)
                             for filename in protocol['task_files']]
    return protocol


def run_logged(command: list[str], output: Path, name: str,
               proposer_environment: dict | None = None) -> None:
    environment = os.environ.copy()
    environment.pop('AUTO_RESEARCH_CLAUDE_CLI_MODEL', None)
    environment.update(proposer_environment or {})
    completed = subprocess.run(command, capture_output=True, text=True, env=environment)
    log_dir = output / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    (log_dir / f'{name}_{suffix}.stdout.log').write_text(completed.stdout or '', encoding='utf-8')
    (log_dir / f'{name}_{suffix}.stderr.log').write_text(completed.stderr or '', encoding='utf-8')
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or '')[-2000:]
        raise RuntimeError(f'{name} failed with exit {completed.returncode}: {tail}')


def loop_command(protocol: dict, task: dict, output: Path, *, feedback: str,
                 branchpoint: Path | None = None, resume: bool = False) -> list[str]:
    command = [sys.executable, str(LOOP), '--output', str(output)]
    if resume:
        return command + ['--resume']
    contract = absolute(protocol['continuation_contract'] if branchpoint
                        else protocol['shared_contract'])
    command += [
        '--contract', str(contract), '--proposer', str(absolute(protocol['proposer'])),
        '--model-id', protocol['model_id'], '--fixed-decisions', '--prompt-feedback', feedback,
        '--proposer-timeout-seconds', str(protocol['proposer_timeout_seconds']),
        '--execution-timeout-seconds', str(protocol['execution_timeout_seconds']),
    ]
    if branchpoint is not None:
        command += ['--branchpoint', str(branchpoint)]
    else:
        command += [
            '--harness', str(absolute(task['initial_harness'])),
            '--trace-file', str(absolute(task['initial_safe_trace'])),
            '--research-question', task['research_question'],
            '--seed', str(task['seed']), '--budget', str(protocol['candidate_budget']),
            '--dev-split', protocol['dev_split'],
        ]
    if task.get('shared_memory'):
        command += ['--shared-memory', str(absolute(task['shared_memory']))]
    if task.get('features'):
        command += ['--features', ','.join(task['features'])]
    if protocol['coverage_log_only']:
        command.append('--coverage-log-only')
    if protocol['demo']:
        command.append('--demo')
    return command


def ensure_budget_recovery(protocol: dict, task: dict, output: Path, *, feedback: str,
                           branchpoint: Path | None, expected_calls: int) -> tuple[Path, dict]:
    failed = read_json(output / 'result.json')
    state = read_json(output / 'loop_state.json')
    pending = state.get('pending') or {}
    usage = failed.get('usage') or {}
    marker = absolute(pending.get('run_dir', output)) / 'generation/per_call_budget_exceeded.json'
    if (failed.get('stop_reason') != 'MODEL_BUDGET_EXCEEDED'
            or not marker.is_file() or not pending
            or usage.get('decisions') != 1 or usage.get('model_calls') != 1
            or expected_calls != 2):
        raise RuntimeError(f'{output} is not a recoverable first-call output-budget failure')
    recovery = output.with_name(output.name + '_BUDGET_RECOVERY_001')
    result_path = recovery / 'result.json'
    if not result_path.is_file():
        if not (recovery / 'loop_state.json').is_file():
            recovery.mkdir(parents=True, exist_ok=False)
            contract = read_json(output / 'contract.json')
            budget = contract['budget']
            if (usage['output_tokens'] >= budget['max_output_tokens']
                    or usage['input_tokens'] >= budget['max_input_tokens']):
                raise RuntimeError(f'{output} has no remaining token budget for recovery')
            write_json_new(recovery / 'contract.json', contract)
            recovered = copy.deepcopy(state)
            parent = next(node for node in recovered['nodes']
                          if node['id'] == recovered['current_node_id'])
            failed_node = {
                'id': f"node_{len(recovered['nodes']):04d}", 'parent_id': parent['id'],
                'run_id': pending['run_id'], 'status': 'FAILED', 'supported': False,
                'probe_score': None, 'children_count': 0,
                'harness': pending['harness'], 'trace_file': pending['trace_file'],
                'research_question': pending['research_question'], 'contract': None,
            }
            parent['children_count'] += 1
            recovered['nodes'].append(failed_node)
            recovered['history'].append({
                'run_id': pending['run_id'], 'mode': recovered['mode'],
                'action': pending['local_action'],
                'rationale': 'per-call output budget failure preserved for budget recovery',
                'evidence_refs': [],
                'base_harness_hash': harness_hash(read_json(absolute(pending['harness']))),
                'status': 'FAILED',
            })
            recovered.update({
                'status': 'RUNNING', 'stop_reason': None, 'pending': None,
                'contract': str(recovery / 'contract.json'),
                'repair_parent_id': parent['id'], 'repair_depth': 1,
                'recovery_from': relative(output),
            })
            write_state(recovery / 'loop_state.json', recovered)
        command = loop_command(protocol, task, recovery, feedback=feedback,
                               branchpoint=branchpoint, resume=True)
        environment = dict(protocol.get('proposer_environment') or {})
        remaining = 16000 - int(usage['output_tokens'])
        environment['AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS'] = str(min(8000, remaining))
        run_logged(command, output.parent, recovery.name, environment)
    result = read_json(result_path)
    if (result.get('stop_reason') != 'DECISION_BUDGET_EXHAUSTED'
            or result['usage']['decisions'] != expected_calls
            or result['usage']['model_calls'] != expected_calls):
        raise RuntimeError(f'{recovery} could not complete the remaining frozen budget')
    return recovery, result


def ensure_loop(protocol: dict, task: dict, output: Path, *, feedback: str,
                branchpoint: Path | None = None) -> tuple[Path, dict]:
    result_path = output / 'result.json'
    if not result_path.is_file():
        resume = (output / 'loop_state.json').is_file()
        command = loop_command(protocol, task, output, feedback=feedback,
                               branchpoint=branchpoint, resume=resume)
        run_logged(command, output.parent, output.name, protocol.get('proposer_environment'))
    result = read_json(result_path)
    expected_calls = int(protocol['continuation_decisions'] if branchpoint
                         else protocol['shared_decisions'])
    if result.get('stop_reason') == 'MODEL_BUDGET_EXCEEDED':
        return ensure_budget_recovery(protocol, task, output, feedback=feedback,
                                      branchpoint=branchpoint, expected_calls=expected_calls)
    if result.get('stop_reason') != 'DECISION_BUDGET_EXHAUSTED':
        raise RuntimeError(f'{output} did not exhaust its frozen decision budget: {result.get("stop_reason")}')
    if (result['usage']['decisions'] != expected_calls
            or result['usage']['model_calls'] != expected_calls):
        raise RuntimeError(f'{output} did not execute the frozen decision/model-call counts')
    return output, result


def decision_records(output: Path) -> list[dict]:
    records = []
    decisions = output / 'decisions'
    if not decisions.is_dir():
        return records
    for child in sorted(decisions.iterdir()):
        record_path = child / 'child/step/step_record.json'
        if record_path.is_file():
            records.append(read_json(record_path))
    return records


def validate_internal_select_excluded(outputs: list[Path]) -> bool:
    records = [record for output in outputs for record in decision_records(output)]
    return all(record.get('probe_only') is True
               and record.get('adopted_select') is None
               and (record.get('reference') or {}).get('select') is None
               for record in records)


def first_decision_pair_check(parent: Path, diagnostic: Path) -> tuple[bool, bool]:
    left = parent / 'decisions' / f'{parent.name}_d001' / 'generation'
    right = diagnostic / 'decisions' / f'{diagnostic.name}_d001' / 'generation'
    if not left.is_dir() or not right.is_dir():
        return False, False
    for name in ('shared_research_memory.json', 'route_history_input.json', 'editable_surface.json'):
        if (left / name).read_bytes() != (right / name).read_bytes():
            return False, False
    left_state = read_json(left / 'research_state.json')
    right_state = read_json(right / 'research_state.json')
    left_common = {key: value for key, value in left_state.items() if key != 'prompt_feedback'}
    right_common = {key: value for key, value in right_state.items() if key != 'prompt_feedback'}
    if left_common != right_common:
        return False, False
    left_trace = read_json(left / 'execution_trace.json')
    right_trace = read_json(right / 'execution_trace.json')
    if left_trace.get('proposals') != []:
        return False, False
    left_common_trace = {key: value for key, value in left_trace.items()
                         if key not in ('scope', 'proposals')}
    right_common_trace = {key: value for key, value in right_trace.items()
                          if key not in ('scope', 'proposals')}
    inputs_match = left_common_trace == right_common_trace
    memory_match = left_state.get('shared_memory_entries') == right_state.get('shared_memory_entries')
    return inputs_match, memory_match


def failed_decisions(output: Path) -> int:
    history = read_json(output / 'route_history.json')['entries']
    prefix = output.name + '_'
    return sum(entry['run_id'].startswith(prefix) and entry.get('status') == 'FAILED'
               for entry in history)


def constraints_satisfied(task: dict, comparison: dict) -> bool:
    constraints = task.get('task_constraints') or {}
    minimum_coverage = float(constraints.get('min_coverage', 0.0))
    for arm in comparison['arms'].values():
        metrics = arm['select_metrics']
        if not math.isfinite(float(metrics['mean_abs_ic'])):
            return False
        if float(metrics['coverage']) < minimum_coverage:
            return False
        if str(metrics['max_date_read']) > '2020-11-30':
            return False
    return True


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
    output = root / f'DIAGNOSTIC_CONTINUATION_{task_name}_S{seed}_{version}'
    result_path = output / 'result.json'
    if result_path.is_file():
        return read_json(result_path)
    output.mkdir(parents=True, exist_ok=True)
    config = {
        'schema': UNIT_SCHEMA, 'protocol': relative(protocol_path),
        'protocol_version': version, 'task': task, 'seed': seed,
    }
    config_path = output / 'config.json'
    if config_path.is_file():
        existing = read_json(config_path)
        if {key: value for key, value in existing.items() if key != 'created_at'} != config:
            raise ValueError(f'unit config differs from the supplied frozen protocol: {output}')
    else:
        write_json_new(config_path, {**config, 'created_at': now()})
    shared_output = output / f'{task_name}_S{seed}_SHARED'
    parent_output = output / f'{task_name}_S{seed}_PARENT'
    diagnostic_output = output / f'{task_name}_S{seed}_DIAGNOSTIC'
    shared_output, shared = ensure_loop(protocol, task, shared_output, feedback='parent')
    branchpoint = shared_output / 'branchpoint.json'
    parent_input = parent_output
    parent_output, parent = ensure_loop(protocol, task, parent_output,
                                        feedback='parent', branchpoint=branchpoint)
    diagnostic_input = diagnostic_output
    diagnostic_output, diagnostic = ensure_loop(protocol, task, diagnostic_output,
                                                feedback='diagnostic', branchpoint=branchpoint)
    parent_state = read_json(parent_output / 'loop_state.json')
    diagnostic_state = read_json(diagnostic_output / 'loop_state.json')
    branchpoint_hash = file_hash(branchpoint)
    branchpoint_identity = bool(
        parent_state.get('branchpoint_sha256') == branchpoint_hash
        and diagnostic_state.get('branchpoint_sha256') == branchpoint_hash
        and parent_state.get('branchpoint_source') == diagnostic_state.get('branchpoint_source'))
    inputs_match, memory_match = first_decision_pair_check(parent_input, diagnostic_input)
    internal_select_excluded = validate_internal_select_excluded(
        [shared_output, parent_output, diagnostic_output])
    comparison_output = output / 'common_comparison'
    if not (comparison_output / 'result.json').is_file():
        command = [
            sys.executable, str(COMPARE), '--output', str(comparison_output),
            '--contract', str(absolute(protocol['continuation_contract'])),
            '--seed', str(seed), '--budget', str(protocol['candidate_budget']),
            '--dev-split', protocol['dev_split'],
            '--arm', f'parent={absolute(parent["champion_harness"])}',
            '--arm', f'diagnostic={absolute(diagnostic["champion_harness"])}',
            '--arm', f'fixed={absolute(task["initial_harness"])}',
            '--arm', f'shared={absolute(shared["champion_harness"])}',
        ]
        if task.get('features'):
            command += ['--features', ','.join(task['features'])]
        if protocol['coverage_log_only']:
            command.append('--coverage-log-only')
        if protocol['demo']:
            command.append('--demo')
        run_logged(command, output, 'common_comparison')
    comparison = read_json(comparison_output / 'result.json')
    split_verified = bool(
        comparison['dev_split']['seed'] == seed
        and comparison['dev_split']['mode'] == protocol['dev_split']
        and parent_state['seed'] == diagnostic_state['seed'] == seed
        and parent_state['dev_split'] == diagnostic_state['dev_split'] == protocol['dev_split'])
    frozen_champions = all(read_json(path).get('schema') == 'EVO-HARNESS-1' for path in (
        absolute(parent['champion_harness']), absolute(diagnostic['champion_harness'])))
    checks = {
        'common_support_verified': comparison['common_support_verified'] is True,
        'actual_model_calls_equal': parent['usage']['model_calls'] == diagnostic['usage']['model_calls'],
        'actual_decisions_equal': parent['usage']['decisions'] == diagnostic['usage']['decisions'],
        'split_verified': split_verified,
        'branchpoint_identity_verified': branchpoint_identity and inputs_match,
        'shared_memory_verified': memory_match,
        'frozen_champions_verified': frozen_champions,
        'internal_select_excluded': internal_select_excluded,
        'task_constraints_satisfied': constraints_satisfied(task, comparison),
    }
    parent_select = read_json(comparison_output / 'select/parent.json')
    diagnostic_select = read_json(comparison_output / 'select/diagnostic.json')
    unit = {
        'schema': UNIT_SCHEMA,
        'protocol_version': version,
        'task': task_name,
        'seed': seed,
        'status': 'COMPLETED' if all(checks.values()) else 'PROTOCOL_INVALID',
        'shared_run': relative(shared_output),
        'parent_run': relative(parent_output),
        'diagnostic_run': relative(diagnostic_output),
        'shared_recovered_from': read_json(shared_output / 'loop_state.json').get('recovery_from'),
        'parent_recovered_from': parent_state.get('recovery_from'),
        'diagnostic_recovered_from': diagnostic_state.get('recovery_from'),
        'comparison_run': relative(comparison_output),
        'parent_champion': parent['champion_harness'],
        'diagnostic_champion': diagnostic['champion_harness'],
        'select_diagnostic_minus_parent': comparison['comparisons']['diagnostic']['select']['primary_delta'],
        'select_parent_minus_fixed': comparison['comparisons']['fixed']['select']['primary_delta'] * -1,
        'select_diagnostic_minus_fixed': comparison['comparisons']['diagnostic']['select']['primary_delta']
                                         - comparison['comparisons']['fixed']['select']['primary_delta'],
        'select_shared_minus_fixed': comparison['comparisons']['fixed']['select']['primary_delta'] * -1
                                     + comparison['comparisons']['shared']['select']['primary_delta'],
        'select_metrics': {name: arm['select_metrics'] for name, arm in comparison['arms'].items()},
        'date_block_diagnostic': date_block_diagnostic(parent_select, diagnostic_select),
        'usage': {'shared': shared['usage'], 'parent': parent['usage'], 'diagnostic': diagnostic['usage']},
        'failed_or_invalid_decisions': sum(
            failed_decisions(path) + int(read_json(path / 'loop_state.json').get('recovery_from') is not None)
            for path in (shared_output, parent_output, diagnostic_output)),
        **checks,
        'score_window_read': False,
        'independent_real_data_oos': False,
        'finished_at': now(),
    }
    write_json_new(result_path, unit)
    return unit


def protocol_record(protocol_path: Path) -> dict:
    protocol = read_protocol(protocol_path)
    if protocol.get('schema') != PROTOCOL_SCHEMA:
        raise ValueError(f'protocol must use {PROTOCOL_SCHEMA}')
    record = {
        'protocol_file': relative(protocol_path),
        'protocol': protocol,
        'task_contracts': {task['task']: task for task in protocol['tasks']},
        'shared_contract': load_contract(absolute(protocol['shared_contract'])),
        'continuation_contract': load_contract(absolute(protocol['continuation_contract'])),
    }
    output = absolute(protocol['run_root']) / f'DIAGNOSTIC_CONTINUATION_PROTOCOL_{protocol["protocol_version"]}'
    config_path = output / 'config.json'
    if config_path.is_file():
        if read_json(config_path) != record:
            raise ValueError('frozen protocol record differs from the supplied protocol')
    else:
        write_json_new(config_path, record)
    return record


def aggregate(protocol_path: Path, *, read_only: bool = False) -> dict:
    protocol = read_protocol(protocol_path)
    tasks = [task['task'] for task in protocol['tasks']]
    root = absolute(protocol['run_root'])
    if read_only:
        units = [read_json(root / f'DIAGNOSTIC_CONTINUATION_{task}_S{seed}_{protocol["protocol_version"]}'
                           / 'result.json') for task in tasks for seed in protocol['seeds']]
    else:
        units = [run_unit(protocol_path, task, seed) for task in tasks for seed in protocol['seeds']]
    summary = aggregate_units(units, tasks, protocol['seeds'])
    result = {
        'schema': RESULT_SCHEMA,
        'protocol_version': protocol['protocol_version'],
        'model': protocol['model_id'],
        'task_count': len(tasks),
        'seeds': protocol['seeds'],
        **summary,
        'finished_at': now(),
    }
    output = root / f'DIAGNOSTIC_CONTINUATION_RESULT_{protocol["protocol_version"]}'
    write_json_new(output / 'result.json', result)
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
