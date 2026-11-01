#!/usr/bin/env python3
"""Drive a bounded sequence of route steps through an external proposer command.

The proposer reads only the prepared generation directory and writes the requested
proposal plus a model-usage record. Market evaluation stays in run_route_step.py
and run_evo_step.py; this driver consumes probe evidence only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_harness import harness_hash
from evo_probe import GuardrailError
from research_contract import (canonical_hash, evaluate_adoption, load_contract, validate_usage)
from run_route_step import HISTORY_SCHEMA, load_history, process_text
from run_evo_step import _load_execution_trace

ROUTE_STEP = SCRIPTS / 'run_route_step.py'
STATE_SCHEMA = 'AUTO-RESEARCH-LOOP-STATE-1'
RESULT_SCHEMA = 'AUTO-RESEARCH-LOOP-RESULT-1'
BRANCHPOINT_SCHEMA = 'AUTO-RESEARCH-BRANCHPOINT-1'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def absolute(path: Path | str) -> Path:
    value = Path(path)
    return value.absolute() if value.is_absolute() else (ROOT / value).absolute()


def relative(path: Path | str) -> str:
    value = absolute(path)
    try:
        return str(value.relative_to(ROOT))
    except ValueError:
        return str(value)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_json_new(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def write_state(path: Path, state: dict) -> None:
    state['updated_at'] = now()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    temporary.replace(path)
    history = {'schema': HISTORY_SCHEMA, 'entries': state['history']}
    temporary = path.parent / 'route_history.tmp'
    temporary.write_text(json.dumps(history, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    temporary.replace(path.parent / 'route_history.json')


def terminal_result(output: Path, state: dict) -> dict:
    current = current_node(state)
    champion = state['champion']
    return {
        'schema': RESULT_SCHEMA,
        'status': state['status'],
        'stop_reason': state['stop_reason'],
        'mode': state['mode'],
        'prompt_feedback': state['prompt_feedback'],
        'model_id': state['model_id'],
        'usage': state['usage'],
        'current_node_id': state['current_node_id'],
        'current_harness': current['harness'],
        'current_trace': current['trace_file'],
        'active_branch': {
            'node_id': current['id'], 'harness': current['harness'],
            'trace_file': current['trace_file'], 'probe_score': current['probe_score'],
            'supported': current['supported'],
        },
        'champion_node_id': champion['node_id'],
        'champion_harness': champion['harness'],
        'champion_trace': champion['trace_file'],
        'champion': champion,
        'branchpoint': (relative(output / 'branchpoint.json')
                        if (output / 'branchpoint.json').is_file() else None),
        'research_question': state['current_question'],
        'decisions': [entry['run_id'] for entry in state['history']
                      if entry['run_id'].startswith(output.name + '_')],
        'nodes': len(state['nodes']),
        'score_window_read': False,
        'independent_real_data_oos': False,
        'finished_at': now(),
    }


def write_branchpoint(output: Path, state: dict) -> None:
    active = current_node(state)
    payload = {
        'schema': BRANCHPOINT_SCHEMA,
        'source_run': output.name,
        'created_at': now(),
        'model_id': state['model_id'],
        'seed': state['seed'],
        'budget_candidates': state['budget_candidates'],
        'dev_split': state['dev_split'],
        'features': state['features'],
        'coverage_log_only': state['coverage_log_only'],
        'demo': state['demo'],
        'research_question': active['research_question'],
        'active': {
            'node_id': active['id'], 'harness': active['harness'],
            'trace_file': active['trace_file'], 'probe_score': active['probe_score'],
            'supported': active['supported'],
        },
        'champion': state['champion'],
        'route_history': relative(output / 'route_history.json'),
        'loop_state': relative(output / 'loop_state.json'),
        'shared_memory': state['shared_memory'],
        'usage': state['usage'],
        'score_window_read': False,
        'independent_real_data_oos': False,
    }
    write_json_new(output / 'branchpoint.json', payload)


def load_branchpoint(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    required = {'schema', 'source_run', 'model_id', 'seed', 'budget_candidates', 'dev_split',
                'features', 'coverage_log_only', 'demo', 'research_question', 'active', 'champion',
                'route_history', 'shared_memory'}
    if not isinstance(payload, dict) or payload.get('schema') != BRANCHPOINT_SCHEMA:
        raise GuardrailError(f'branchpoint must use {BRANCHPOINT_SCHEMA}')
    if not required.issubset(payload):
        raise GuardrailError(f'branchpoint is missing fields: {sorted(required - set(payload))}')
    for key in ('active', 'champion'):
        item = payload[key]
        if not isinstance(item, dict) or not all(isinstance(item.get(field), str) and item[field]
                                                for field in ('harness', 'trace_file')):
            raise GuardrailError(f'branchpoint {key} needs harness and trace_file')
    if not isinstance(payload['research_question'], str) or not payload['research_question'].strip():
        raise GuardrailError('branchpoint research_question must be nonempty')
    return payload


def stop(output: Path, state: dict, reason: str, status: str = 'STOPPED') -> int:
    state['status'] = status
    state['stop_reason'] = reason
    state['pending'] = None if reason != 'MODEL_BUDGET_EXCEEDED' else state['pending']
    write_state(output / 'loop_state.json', state)
    if state.get('fixed_decisions') and reason == 'DECISION_BUDGET_EXHAUSTED':
        write_branchpoint(output, state)
    result = terminal_result(output, state)
    if (output / 'result.json').is_file():
        raise GuardrailError(f'loop result already exists: {output / "result.json"}')
    write_json_new(output / 'result.json', result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if status == 'STOPPED' else 2


def recoverable_failure(output: Path, state: dict, reason: str) -> int:
    state['status'] = 'FAILED'
    state['stop_reason'] = reason
    write_state(output / 'loop_state.json', state)
    payload = {'schema': RESULT_SCHEMA, 'status': 'FAILED', 'stop_reason': reason,
               'pending': state['pending']}
    pending = state.get('pending') or {}
    if pending.get('run_dir'):
        generation = absolute(pending['run_dir']) / 'generation'
        for key, filename in (('proposer_stdout_tail', 'proposer_stdout.log'),
                              ('proposer_stderr_tail', 'proposer_stderr.log')):
            path = generation / filename
            if path.is_file():
                payload[key] = path.read_text(encoding='utf-8')[-2000:]
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return 2


def current_node(state: dict) -> dict:
    matches = [node for node in state['nodes'] if node['id'] == state['current_node_id']]
    if len(matches) != 1:
        raise GuardrailError('current loop node is missing or duplicated')
    return matches[0]


def select_local_parent(state: dict) -> dict:
    parent = current_node(state)
    if parent['status'] not in ('COMPLETED', 'HANDOFF'):
        raise GuardrailError('local-only current node is not an executable research parent')
    return parent


def initial_state(args: argparse.Namespace, output: Path) -> dict:
    if (output / 'loop_state.json').exists():
        raise GuardrailError('loop already exists; use --resume')
    if output.exists() and any(output.iterdir()):
        raise GuardrailError('refusing to start a loop in a non-empty directory')
    proposer = absolute(args.proposer)
    if not proposer.is_file() or not os.access(proposer, os.X_OK):
        raise GuardrailError('proposer must be an executable file')
    fixed_decisions = bool(getattr(args, 'fixed_decisions', False))
    if fixed_decisions and args.mode != 'local-only':
        raise GuardrailError('fixed-decisions core exploration requires local-only mode')
    output.mkdir(parents=True, exist_ok=True)
    contract = load_contract(args.contract)
    write_json_new(output / 'contract.json', contract)
    branchpoint_path = getattr(args, 'branchpoint', None)
    branchpoint = load_branchpoint(branchpoint_path) if branchpoint_path else None
    if branchpoint is not None:
        if args.model_id != branchpoint['model_id']:
            raise GuardrailError('continuation model differs from its shared branchpoint')
        harness_path = absolute(branchpoint['active']['harness'])
        trace_path = absolute(branchpoint['active']['trace_file'])
        history_path = absolute(branchpoint['route_history'])
        question = branchpoint['research_question'].strip()
        champion = {**branchpoint['champion'],
                    'harness': relative(branchpoint['champion']['harness']),
                    'trace_file': relative(branchpoint['champion']['trace_file'])}
        seed = int(branchpoint['seed'])
        budget_candidates = int(branchpoint['budget_candidates'])
        dev_split = branchpoint['dev_split']
        features = branchpoint['features']
        coverage_log_only = bool(branchpoint['coverage_log_only'])
        demo = bool(branchpoint['demo'])
        shared_memory = branchpoint['shared_memory']
        supplied_memory = getattr(args, 'shared_memory', None)
        if supplied_memory and shared_memory and absolute(supplied_memory) != absolute(shared_memory):
            raise GuardrailError('continuation shared memory differs from its branchpoint')
    else:
        harness_path = absolute(args.harness)
        trace_path = absolute(args.trace_file)
        history_path = absolute(args.route_history) if args.route_history else None
        question = args.research_question.strip()
        champion = None
        seed = args.seed
        budget_candidates = args.budget
        dev_split = args.dev_split
        features = args.features
        coverage_log_only = args.coverage_log_only
        demo = args.demo
        shared_memory = relative(args.shared_memory) if getattr(args, 'shared_memory', None) else None
    trace = _load_execution_trace(trace_path)
    history = load_history(history_path)['entries'] if history_path else []
    probe_metrics = trace.get('adopted_probe_metrics') or trace.get('reference_probe_metrics') or {}
    probe_score = probe_metrics.get('mean_abs_ic')
    root = {
        'id': 'root', 'parent_id': None,
        'run_id': branchpoint['source_run'] if branchpoint else trace['source_run'],
        'status': 'COMPLETED',
        'supported': (bool(branchpoint['active'].get('supported')) if branchpoint else True),
        'probe_score': probe_score,
        'children_count': 0, 'harness': relative(harness_path), 'trace_file': relative(trace_path),
        'research_question': question, 'contract': None,
    }
    if champion is None:
        champion = {'node_id': 'root', 'harness': root['harness'],
                    'trace_file': root['trace_file'], 'probe_score': probe_score}
    return {
        'schema': STATE_SCHEMA, 'status': 'RUNNING', 'stop_reason': None,
        'created_at': now(), 'updated_at': now(),
        'contract': str(output / 'contract.json'), 'contract_sha256': canonical_hash(contract),
        'contract_source': relative(args.contract),
        'proposer': relative(proposer), 'proposer_sha256': file_hash(proposer),
        'proposer_timeout_seconds': args.proposer_timeout_seconds,
        'execution_timeout_seconds': args.execution_timeout_seconds,
        'mode': args.mode, 'prompt_feedback': getattr(args, 'prompt_feedback', 'diagnostic'),
        'fixed_decisions': fixed_decisions,
        'branchpoint_source': relative(branchpoint_path) if branchpoint_path else None,
        'branchpoint_sha256': file_hash(branchpoint_path) if branchpoint_path else None,
        'shared_memory': shared_memory,
        'model_id': args.model_id, 'seed': seed,
        'budget_candidates': budget_candidates, 'dev_split': dev_split,
        'features': features, 'coverage_log_only': coverage_log_only,
        'demo': demo, 'current_node_id': 'root',
        'current_question': question,
        'champion': champion,
        'nodes': [root], 'history': history,
        'usage': {'decisions': 0, 'candidate_budget': 0, 'model_calls': 0,
                  'input_tokens': 0, 'output_tokens': 0},
        'no_improvement': 0, 'repair_parent_id': None, 'repair_depth': 0,
        'pending': None,
    }


def resume_state(args: argparse.Namespace, output: Path) -> dict:
    state_path = output / 'loop_state.json'
    state = json.loads(state_path.read_text(encoding='utf-8'))
    if not isinstance(state, dict) or state.get('schema') != STATE_SCHEMA:
        raise GuardrailError(f'loop state must use {STATE_SCHEMA}')
    contract = load_contract(output / 'contract.json')
    if canonical_hash(contract) != state['contract_sha256']:
        raise GuardrailError('loop contract changed; preserve the loop and start a new one')
    proposer = absolute(args.proposer or state['proposer'])
    if file_hash(proposer) != state['proposer_sha256']:
        raise GuardrailError('proposer executable changed; preserve the loop and start a new one')
    if state['status'] == 'STOPPED':
        print((output / 'result.json').read_text(encoding='utf-8'), end='')
        return state
    if state['status'] not in ('RUNNING', 'FAILED'):
        raise GuardrailError(f'cannot resume loop status {state["status"]}')
    state['status'] = 'RUNNING'
    state['stop_reason'] = None
    return state


def route_command(state: dict, pending: dict, output: Path, phase: str) -> list[str]:
    run_dir = absolute(pending['run_dir'])
    generation = run_dir / 'generation'
    proposal = generation / ('edits.json' if state['mode'] == 'local-only' else 'route.json')
    command = [
        sys.executable, str(ROUTE_STEP), '--output', str(run_dir), '--run-id', pending['run_id'],
        '--mode', state['mode'], '--harness', str(absolute(pending['harness'])),
        '--trace-file', str(absolute(pending['trace_file'])),
        '--route-history', str(output / 'route_history.json'),
        '--prompt-feedback', state['prompt_feedback'],
        '--champion-harness', str(absolute(state['champion']['harness'])),
        '--research-question', pending['research_question'], '--model-id', state['model_id'],
        '--seed', str(state['seed']), '--budget', str(state['budget_candidates']),
        '--dev-split', state['dev_split'], '--k', '1',
        '--execution-timeout-seconds', str(state['execution_timeout_seconds']),
    ]
    if state['shared_memory']:
        command += ['--shared-memory', str(absolute(state['shared_memory']))]
    if state['features']:
        command += ['--features', state['features']]
    for flag, enabled in (('--coverage-log-only', state['coverage_log_only']),
                          ('--demo', state['demo'])):
        if enabled:
            command.append(flag)
    if state['mode'] == 'local-only':
        command += ['--local-action', pending['local_action']]
        if pending['local_action'] == 'REPAIR':
            command += ['--repair-target', pending['repair_target']]
    if phase == 'PREPARE':
        command.append('--prepare')
    else:
        command += ['--edits' if state['mode'] == 'local-only' else '--route', str(proposal)]
    return command


def run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def proposer_environment(state: dict, pending: dict) -> dict:
    generation = absolute(pending['run_dir']) / 'generation'
    proposal = generation / ('edits.json' if state['mode'] == 'local-only' else 'route.json')
    environment = os.environ.copy()
    environment.update({
        'AUTO_RESEARCH_RUN_ID': pending['run_id'],
        'AUTO_RESEARCH_GENERATION': str(generation),
        'AUTO_RESEARCH_PROPOSAL': str(proposal),
        'AUTO_RESEARCH_USAGE': str(generation / 'model_usage.json'),
        'AUTO_RESEARCH_MODE': state['mode'],
        'AUTO_RESEARCH_MODEL_ID': state['model_id'],
    })
    return environment


def append_history(state: dict, pending: dict, result: dict) -> None:
    route = result.get('route') or {
        'action': pending['local_action'],
        'rationale': f"invalid or failed proposal: {result.get('error', 'execution failed')}",
        'evidence_refs': [],
    }
    execution = result.get('execution') or {}
    entry = {
        'run_id': pending['run_id'], 'mode': state['mode'], 'action': route['action'],
        'rationale': route.get('rationale', 'route execution'),
        'evidence_refs': route.get('evidence_refs', []),
        'base_harness_hash': harness_hash(json.loads(absolute(pending['harness']).read_text())),
        'status': result['status'] if result['status'] in ('COMPLETED', 'HANDOFF', 'STOPPED') else 'FAILED',
    }
    for key in ('falsification', 'target', 'next_question', 'branch_from'):
        if route.get(key) not in (None, [], ''):
            entry[key] = route[key]
    for key in ('child_run', 'proposal_order'):
        if execution.get(key) not in (None, [], ''):
            entry[key] = execution[key]
    state['history'].append(entry)


def add_node(state: dict, pending: dict, *, status: str, supported: bool,
             probe_score, harness: str, trace_file: str, question: str, contract=None) -> dict:
    parent = current_node(state)
    parent['children_count'] += 1
    node = {
        'id': f"node_{len(state['nodes']):04d}", 'parent_id': parent['id'],
        'run_id': pending['run_id'], 'status': status, 'supported': supported,
        'probe_score': probe_score, 'children_count': 0, 'harness': harness,
        'trace_file': trace_file, 'research_question': question, 'contract': contract,
    }
    state['nodes'].append(node)
    return node


def finalize_route_result(state: dict, pending: dict, result: dict) -> str | None:
    append_history(state, pending, result)
    status = result['status']
    action = (result.get('route') or {}).get('action', pending['local_action'])
    if (status == 'COMPLETED' or (status == 'FAILED'
                                  and result.get('candidate_evaluation_started') is not False)):
        if status != 'INVALID_PROPOSAL' and action in ('EXPLOIT', 'REPAIR'):
            state['usage']['candidate_budget'] += state['budget_candidates']
    if status == 'STOPPED':
        return 'MODEL_STOP'
    if status == 'HANDOFF':
        next_state = json.loads(absolute(result['next_state']).read_text(encoding='utf-8'))
        parent = current_node(state)
        node = add_node(
            state, pending, status='HANDOFF', supported=True, probe_score=parent['probe_score'],
            harness=next_state['harness'], trace_file=next_state['trace_file'],
            question=next_state['research_question'])
        state['current_node_id'] = node['id']
        state['current_question'] = node['research_question']
        state['no_improvement'] = 0
        state['repair_parent_id'] = None
        state['repair_depth'] = 0
        return None
    if status != 'COMPLETED':
        if state['repair_parent_id'] == state['current_node_id']:
            state['repair_depth'] += 1
        else:
            state['repair_parent_id'] = state['current_node_id']
            state['repair_depth'] = 1
        add_node(state, pending, status='FAILED', supported=False, probe_score=None,
                 harness=pending['harness'], trace_file=pending['trace_file'],
                 question=pending['research_question'])
        budget = load_contract(Path(state['contract']))['budget']['max_debug_depth']
        if state['repair_depth'] > budget:
            if state.get('fixed_decisions'):
                state['repair_parent_id'] = None
                state['repair_depth'] = 0
                return None
            return 'REPAIR_BUDGET_EXHAUSTED'
        return None

    run_dir = absolute(pending['run_dir'])
    record = json.loads((run_dir / 'child/step/step_record.json').read_text(encoding='utf-8'))
    reference = ((record.get('champion_probe') or {}).get('metrics')
                 or record['adopted_reference']['probe']['metrics'])
    adopted = record['adopted_probe']['metrics']
    for key in ('common_support_sha256', 'fit_common_support_sha256'):
        if reference.get(key) != adopted.get(key):
            raise GuardrailError(f'champion and active candidate differ on {key}')
    contract = load_contract(Path(state['contract']))
    adoption = evaluate_adoption(contract, reference, adopted)
    next_state = json.loads((run_dir / 'next_state.json').read_text(encoding='utf-8'))
    node = add_node(state, pending, status='COMPLETED', supported=adoption['supported'],
                    probe_score=adopted['mean_abs_ic'], harness=next_state['harness'],
                    trace_file=next_state['trace_file'], question=next_state['research_question'],
                    contract=adoption)
    state['repair_parent_id'] = None
    state['repair_depth'] = 0
    state['current_node_id'] = node['id']
    state['current_question'] = node['research_question']
    if adoption['supported']:
        state['champion'] = {
            'node_id': node['id'], 'run_id': node['run_id'], 'harness': node['harness'],
            'trace_file': node['trace_file'], 'probe_score': node['probe_score'],
            'contract': adoption,
        }
        state['no_improvement'] = 0
    else:
        state['no_improvement'] += 1
        if (not state.get('fixed_decisions')
                and state['no_improvement'] >= contract['budget']['max_no_improvement']):
            return 'NO_IMPROVEMENT_BUDGET_EXHAUSTED'
    return None


def start_pending(state: dict, output: Path) -> dict:
    if state['mode'] == 'local-only':
        parent = select_local_parent(state)
        state['current_node_id'] = parent['id']
        state['current_question'] = parent['research_question']
    else:
        parent = current_node(state)
    repairing = state['repair_parent_id'] == parent['id'] and state['repair_depth'] > 0
    local_action = 'REPAIR' if repairing else 'EXPLOIT'
    index = state['usage']['decisions'] + 1
    run_id = f'{output.name}_d{index:03d}'
    return {
        'run_id': run_id, 'run_dir': str(output / 'decisions' / run_id), 'phase': 'PREPARE',
        'parent_node_id': parent['id'], 'harness': parent['harness'],
        'trace_file': parent['trace_file'], 'research_question': state['current_question'],
        'local_action': local_action,
        'repair_target': f'failed proposal from {parent["id"]}' if repairing else None,
    }


def budget_stop_reason(state: dict, contract: dict) -> str | None:
    usage = state['usage']
    budget = contract['budget']
    if usage['decisions'] >= budget['max_decisions']:
        return 'DECISION_BUDGET_EXHAUSTED'
    if usage['model_calls'] >= budget['max_model_calls']:
        return 'MODEL_CALL_BUDGET_EXHAUSTED'
    return None


def proposal_needs_candidate_budget(state: dict, pending: dict) -> bool:
    if state['mode'] == 'local-only':
        return True
    proposal_path = absolute(pending['run_dir']) / 'generation/route.json'
    payload = json.loads(proposal_path.read_text(encoding='utf-8'))
    return isinstance(payload, dict) and payload.get('action') in ('EXPLOIT', 'REPAIR')


def charge_usage(state: dict, usage_record: dict) -> None:
    state['usage']['decisions'] += 1
    for source, target in (('calls', 'model_calls'),
                           ('input_tokens', 'input_tokens'),
                           ('output_tokens', 'output_tokens')):
        state['usage'][target] += usage_record[source]


def usage_within_budget(usage: dict, budget: dict) -> bool:
    return all((usage['decisions'] <= budget['max_decisions'],
                usage['model_calls'] <= budget['max_model_calls'],
                usage['input_tokens'] <= budget['max_input_tokens'],
                usage['output_tokens'] <= budget['max_output_tokens']))


def drive(output: Path, state: dict) -> int:
    while state['status'] == 'RUNNING':
        contract = load_contract(output / 'contract.json')
        if state['pending'] is None:
            reason = budget_stop_reason(state, contract)
            if reason:
                return stop(output, state, reason)
            state['pending'] = start_pending(state, output)
            write_state(output / 'loop_state.json', state)
        pending = state['pending']
        run_dir = absolute(pending['run_dir'])
        manifest_path = run_dir / 'run_manifest.json'
        if pending['phase'] == 'PREPARE':
            if manifest_path.is_file():
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
                if manifest.get('status') != 'PREPARED':
                    return recoverable_failure(output, state, 'PREPARE_IDENTITY_CONFLICT')
            else:
                completed = run_command(route_command(state, pending, output, 'PREPARE'),
                                        state['execution_timeout_seconds'])
                if completed.returncode != 0:
                    return recoverable_failure(output, state, 'ROUTE_PREPARE_FAILED')
            pending['phase'] = 'PROPOSE'
            write_state(output / 'loop_state.json', state)
        if pending['phase'] == 'PROPOSE':
            generation = run_dir / 'generation'
            proposal = generation / ('edits.json' if state['mode'] == 'local-only' else 'route.json')
            usage_path = generation / 'model_usage.json'
            if proposal.exists() != usage_path.exists():
                if state.get('fixed_decisions') and usage_path.is_file() and not proposal.exists():
                    usage_record = validate_usage(
                        json.loads(usage_path.read_text(encoding='utf-8')), state['model_id'])
                    charge_usage(state, usage_record)
                    write_json_new(generation / 'model_failure.json', {
                        'run_id': pending['run_id'], 'returncode': None,
                        'status': 'FAILED', 'error': 'incomplete proposer artifacts after a persisted model call',
                        'usage': usage_record,
                    })
                    reason = finalize_route_result(state, pending, {
                        'status': 'FAILED', 'error': 'incomplete proposer artifacts; usage preserved',
                        'candidate_evaluation_started': False,
                    })
                    state['pending'] = None
                    write_state(output / 'loop_state.json', state)
                    if reason:
                        return stop(output, state, reason)
                    if not usage_within_budget(state['usage'], contract['budget']):
                        return stop(output, state, 'MODEL_BUDGET_EXCEEDED')
                    continue
                return recoverable_failure(output, state, 'INCOMPLETE_PROPOSER_ARTIFACTS')
            if not proposal.exists():
                attempt_path = generation / 'proposer_attempt.json'
                if attempt_path.exists():
                    return recoverable_failure(output, state, 'PROPOSER_OUTCOME_UNKNOWN')
                write_json_new(attempt_path, {'run_id': pending['run_id'], 'started_at': now()})
                try:
                    proposer = absolute(state['proposer'])
                    command = ([sys.executable, str(proposer)] if proposer.suffix == '.py'
                               else [str(proposer)])
                    completed = subprocess.run(
                        command, capture_output=True, text=True,
                        timeout=state['proposer_timeout_seconds'], env=proposer_environment(state, pending))
                except subprocess.TimeoutExpired as error:
                    (generation / 'proposer_stdout.log').write_text(
                        process_text(error.stdout), encoding='utf-8')
                    (generation / 'proposer_stderr.log').write_text(
                        process_text(error.stderr), encoding='utf-8')
                    return recoverable_failure(output, state, 'PROPOSER_TIMEOUT')
                (generation / 'proposer_stdout.log').write_text(completed.stdout or '', encoding='utf-8')
                (generation / 'proposer_stderr.log').write_text(completed.stderr or '', encoding='utf-8')
                if completed.returncode != 0:
                    marker = generation / 'per_call_budget_exceeded.json'
                    if marker.is_file():
                        usage_record = validate_usage(
                            json.loads(usage_path.read_text(encoding='utf-8')), state['model_id'])
                        charge_usage(state, usage_record)
                        if state.get('fixed_decisions'):
                            reason = finalize_route_result(state, pending, {
                                'status': 'FAILED',
                                'error': 'proposer exceeded the per-call output budget; usage preserved',
                                'candidate_evaluation_started': False,
                            })
                            state['pending'] = None
                            write_state(output / 'loop_state.json', state)
                            if reason:
                                return stop(output, state, reason)
                            if not usage_within_budget(state['usage'], contract['budget']):
                                return stop(output, state, 'MODEL_BUDGET_EXCEEDED')
                            continue
                        pending['phase'] = 'EXECUTE'
                        write_state(output / 'loop_state.json', state)
                        return stop(output, state, 'MODEL_BUDGET_EXCEEDED')
                    if state.get('fixed_decisions') and usage_path.is_file():
                        usage_record = validate_usage(
                            json.loads(usage_path.read_text(encoding='utf-8')), state['model_id'])
                        charge_usage(state, usage_record)
                        write_json_new(generation / 'model_failure.json', {
                            'run_id': pending['run_id'], 'returncode': completed.returncode,
                            'status': 'FAILED', 'usage': usage_record,
                        })
                        reason = finalize_route_result(state, pending, {
                            'status': 'FAILED',
                            'error': f'proposer command exited {completed.returncode}; usage preserved',
                            'candidate_evaluation_started': False,
                        })
                        state['pending'] = None
                        write_state(output / 'loop_state.json', state)
                        if reason:
                            return stop(output, state, reason)
                        if not usage_within_budget(state['usage'], contract['budget']):
                            return stop(output, state, 'MODEL_BUDGET_EXCEEDED')
                        continue
                    return recoverable_failure(output, state, 'PROPOSER_COMMAND_FAILED')
            if not proposal.is_file() or not usage_path.is_file():
                return recoverable_failure(output, state, 'PROPOSER_ARTIFACTS_MISSING')
            usage_record = validate_usage(json.loads(usage_path.read_text(encoding='utf-8')),
                                          state['model_id'])
            charge_usage(state, usage_record)
            pending['phase'] = 'EXECUTE'
            write_state(output / 'loop_state.json', state)
            if not usage_within_budget(state['usage'], contract['budget']):
                return stop(output, state, 'MODEL_BUDGET_EXCEEDED')
            if (proposal_needs_candidate_budget(state, pending)
                    and state['usage']['candidate_budget'] + state['budget_candidates']
                    > contract['budget']['max_candidate_budget']):
                return stop(output, state, 'CANDIDATE_BUDGET_EXHAUSTED')
        result_path = run_dir / 'result.json'
        if not result_path.is_file():
            completed = run_command(route_command(state, pending, output, 'EXECUTE'),
                                    state['execution_timeout_seconds'] + 90)
            if completed.returncode != 0 and not result_path.is_file():
                return recoverable_failure(output, state, 'ROUTE_EXECUTION_FAILED')
        result = json.loads(result_path.read_text(encoding='utf-8'))
        reason = finalize_route_result(state, pending, result)
        state['pending'] = None
        write_state(output / 'loop_state.json', state)
        if reason:
            return stop(output, state, reason)
    return 0


def run(args: argparse.Namespace) -> int:
    output = absolute(args.output)
    state = resume_state(args, output) if args.resume else initial_state(args, output)
    if state['status'] == 'STOPPED':
        return 0
    return drive(output, state)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--contract', type=Path)
    parser.add_argument('--proposer', type=Path)
    parser.add_argument('--harness', type=Path)
    parser.add_argument('--trace-file', type=Path)
    parser.add_argument('--branchpoint', type=Path)
    parser.add_argument('--shared-memory', type=Path)
    parser.add_argument('--route-history', type=Path)
    parser.add_argument('--research-question')
    parser.add_argument('--mode', choices=['local-only', 'route-choice'], default='local-only')
    parser.add_argument('--prompt-feedback', choices=['parent', 'diagnostic'], default='diagnostic')
    parser.add_argument('--fixed-decisions', action='store_true',
                        help='disable no-improvement and repair-depth early stops in the bounded core protocol')
    parser.add_argument('--model-id', default='unspecified-external-agent')
    parser.add_argument('--seed', type=int, default=11)
    parser.add_argument('--budget', type=int, default=12)
    parser.add_argument('--dev-split', choices=['legacy', 'seeded-year-blocks'],
                        default='seeded-year-blocks')
    parser.add_argument('--features')
    parser.add_argument('--coverage-log-only', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--proposer-timeout-seconds', type=int, default=300)
    parser.add_argument('--execution-timeout-seconds', type=int, default=300)
    args = parser.parse_args()
    if not args.resume:
        required = ('contract', 'proposer') if args.branchpoint else (
            'contract', 'proposer', 'harness', 'trace_file', 'research_question')
        missing = [name for name in required if getattr(args, name) is None]
        if missing:
            parser.error('missing required initial arguments: ' + ', '.join(missing))
        if not args.branchpoint and not args.research_question.strip():
            parser.error('research-question must not be empty')
        if args.budget < 1 or args.proposer_timeout_seconds < 1 or args.execution_timeout_seconds < 1:
            parser.error('budget and timeouts must be positive')
    try:
        return run(args)
    except (GuardrailError, ValueError, FileNotFoundError, FileExistsError,
            subprocess.TimeoutExpired) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
