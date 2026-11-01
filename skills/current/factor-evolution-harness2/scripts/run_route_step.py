#!/usr/bin/env python3
"""Prepare and execute one bounded research-route decision without calling a model.

The local-only and route-choice arms share the same neutral probe trace. Local
execution is delegated to run_evo_step.py; FORK/REFRAME produce a handoff and
STOP does not fabricate an evo-step record.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_harness import editable_surface, harness_hash, validate_proposal
from evo_memory import Ledger
from evo_probe import GuardrailError
from run_evo_step import _load_execution_trace, load_llm_edits
from trace_contract import reject_sensitive_fields

STEP = SCRIPTS / 'run_evo_step.py'
SUMMARIZER = ROOT / 'auto_research/scripts/summarize_research_step.py'
ACTIONS = ('EXPLOIT', 'REPAIR', 'FORK', 'REFRAME', 'STOP')
ROUTE_SCHEMA = 'AUTO-RESEARCH-ROUTE-1'
HISTORY_SCHEMA = 'AUTO-RESEARCH-ROUTE-HISTORY-1'
MEMORY_SCHEMA = 'AUTO-RESEARCH-SHARED-MEMORY-1'
ROUTE_KEYS = {'schema', 'action', 'rationale', 'evidence_refs', 'falsification',
              'edits', 'target', 'next_question', 'branch_from'}
HISTORY_KEYS = {'run_id', 'mode', 'action', 'rationale', 'evidence_refs',
                'falsification', 'target', 'next_question', 'branch_from',
                'base_harness_hash', 'child_run', 'status', 'proposal_order'}

def relative_path(path: Path) -> str:
    absolute = Path(path).absolute()
    try:
        return str(absolute.relative_to(ROOT))
    except ValueError:
        return str(absolute)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(text)


def write_json_new(path: Path, payload) -> None:
    write_new(path, json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def write_prepare(path: Path, text: str) -> None:
    if path.is_file():
        existing = path.read_text(encoding='utf-8')
        if existing == text:
            return
        if existing:
            raise GuardrailError(f'partial prepare artifact differs: {path}')
        path.write_text(text, encoding='utf-8')
        return
    write_new(path, text)


def write_json_prepare(path: Path, payload) -> None:
    write_prepare(path, json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def neutral_edit(edit: dict) -> dict:
    result = {key: value for key, value in edit.items() if key != 'intent'}
    if edit.get('component') == 'atomic' and isinstance(edit.get('value'), list):
        result['value'] = [neutral_edit(item) for item in edit['value']]
    return result


def neutral_trace(trace: dict, feedback: str = 'diagnostic') -> dict:
    if feedback not in ('parent', 'diagnostic'):
        raise GuardrailError(f'unknown prompt feedback: {feedback}')
    result = {'schema': trace['schema'], 'source_run': trace['source_run'],
              'scope': ('Shared scalar probe outcomes and active lineage; candidate diagnostics omitted.'
                        if feedback == 'parent' else
                        'Prior proposal parameters and probe outcomes; prior prose conclusions omitted.')}
    for key in ('seed_semantics', 'search_policy', 'reference_probe_metrics',
                'adopted_probe_metrics', 'kept_edits'):
        if key in trace:
            result[key] = trace[key]
    proposals = []
    if feedback == 'diagnostic':
        for proposal in trace['proposals']:
            item = {key: value for key, value in proposal.items() if key != 'support_reason'}
            edit = item.get('edit')
            if isinstance(edit, dict):
                item['edit'] = neutral_edit(edit)
            proposals.append(item)
    result['proposals'] = proposals
    reject_sensitive_fields(result)
    return result


def load_history(path: Path | None) -> dict:
    if path is None:
        return {'schema': HISTORY_SCHEMA, 'entries': []}
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('schema') != HISTORY_SCHEMA:
        raise GuardrailError(f'route history must use {HISTORY_SCHEMA}')
    if set(payload) != {'schema', 'entries'} or not isinstance(payload['entries'], list):
        raise GuardrailError('route history must contain only schema and entries')
    reject_sensitive_fields(payload)
    run_ids = set()
    for entry in payload['entries']:
        if not isinstance(entry, dict) or set(entry) - HISTORY_KEYS:
            raise GuardrailError('route history contains an invalid entry')
        if entry.get('action') not in ACTIONS:
            raise GuardrailError('route history contains an unknown action')
        if entry.get('status') not in ('COMPLETED', 'FAILED', 'HANDOFF', 'STOPPED'):
            raise GuardrailError('route history contains an unknown status')
        if not isinstance(entry.get('run_id'), str) or not entry['run_id']:
            raise GuardrailError('route history entry needs run_id')
        if entry['run_id'] in run_ids:
            raise GuardrailError('route history contains a duplicate run_id')
        run_ids.add(entry['run_id'])
        if not isinstance(entry.get('rationale'), str):
            raise GuardrailError('route history rationale must be text')
        refs = entry.get('evidence_refs')
        if (not isinstance(refs, list)
                or any(not isinstance(ref, str) or not ref for ref in refs)):
            raise GuardrailError('route history evidence_refs must be a string list')
    return payload


def load_shared_memory(path: Path | None) -> dict:
    if path is None:
        return {'schema': MEMORY_SCHEMA, 'entries': []}
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('schema') != MEMORY_SCHEMA:
        raise GuardrailError(f'shared memory must use {MEMORY_SCHEMA}')
    if set(payload) != {'schema', 'entries'} or not isinstance(payload['entries'], list):
        raise GuardrailError('shared memory must contain only schema and entries')
    allowed = {'source_run', 'task', 'status', 'research_question', 'harness',
               'probe_metrics', 'edits', 'scope'}
    for entry in payload['entries']:
        if not isinstance(entry, dict) or set(entry) - allowed:
            raise GuardrailError('shared memory contains an invalid entry')
        if not isinstance(entry.get('source_run'), str) or not entry['source_run']:
            raise GuardrailError('shared memory entry needs source_run')
        if entry.get('status') not in ('COMPLETED', 'FAILED', 'NULL', 'INVALID'):
            raise GuardrailError('shared memory entry has an unknown status')
        if not isinstance(entry.get('scope'), str) or not entry['scope'].strip():
            raise GuardrailError('shared memory entry needs an evidence scope')
    reject_sensitive_fields(payload)
    return payload


def evidence_ids(trace: dict, history: dict) -> set[str]:
    refs = {trace['source_run']}
    for proposal in trace['proposals']:
        edit = proposal['edit']
        if isinstance(edit, dict) and isinstance(edit.get('id'), str):
            refs.add(edit['id'])
        elif isinstance(edit, str):
            refs.add(edit)
    for entry in history['entries']:
        refs.add(entry['run_id'])
        if isinstance(entry.get('child_run'), str):
            refs.add(entry['child_run'])
    return refs


def validate_route(payload, *, current_question: str, allowed_evidence: set[str]) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('route must be one JSON object')
    if set(payload) - ROUTE_KEYS:
        raise ValueError(f'route contains unsupported fields: {sorted(set(payload) - ROUTE_KEYS)}')
    if payload.get('schema') != ROUTE_SCHEMA:
        raise ValueError(f'route schema must be {ROUTE_SCHEMA}')
    action = payload.get('action')
    if action not in ACTIONS:
        raise ValueError(f'unsupported route action: {action!r}')
    rationale = payload.get('rationale')
    if not isinstance(rationale, str) or not rationale.strip():
        raise ValueError('route needs a non-empty rationale')
    refs = payload.get('evidence_refs')
    if not isinstance(refs, list) or not refs:
        raise ValueError('route needs at least one evidence reference')
    if any(not isinstance(ref, str) or ref not in allowed_evidence for ref in refs):
        raise ValueError('route evidence_refs must name supplied runs or proposal ids')
    edits = payload.get('edits', [])
    if not isinstance(edits, list):
        raise ValueError('route edits must be a list')
    if action in ('EXPLOIT', 'REPAIR'):
        if not 2 <= len(edits) <= 5:
            raise ValueError(f'{action} needs 2-5 local edits for the unchanged inner executor')
        edits = [validate_proposal(edit) for edit in edits]
        if len({edit['id'] for edit in edits}) != len(edits):
            raise ValueError('route contains duplicate edit ids')
        if not isinstance(payload.get('falsification'), str) or not payload['falsification'].strip():
            raise ValueError(f'{action} needs a falsification condition')
    elif edits:
        raise ValueError(f'{action} cannot execute local edits in the same route step')
    if action == 'REPAIR' and (not isinstance(payload.get('target'), str)
                               or not payload['target'].strip()):
        raise ValueError('REPAIR needs a non-empty target')
    if action != 'REPAIR' and 'target' in payload:
        raise ValueError('target is only valid for REPAIR')
    if action in ('FORK', 'REFRAME'):
        question = payload.get('next_question')
        if not isinstance(question, str) or not question.strip() or question == current_question:
            raise ValueError(f'{action} needs a next_question different from the current question')
        if payload.get('branch_from') != 'current':
            raise ValueError(f'{action} requires branch_from=current; start another run for an older branch')
        if not isinstance(payload.get('falsification'), str) or not payload['falsification'].strip():
            raise ValueError(f'{action} needs a falsification condition')
    elif 'next_question' in payload or 'branch_from' in payload:
        raise ValueError('next_question and branch_from are only valid for FORK or REFRAME')
    return {**payload, 'action': action, 'rationale': rationale.strip(), 'edits': edits}


def prompt_text(mode: str, question: str, local_action: str = 'EXPLOIT',
                repair_target: str | None = None, feedback: str = 'diagnostic',
                shared_memory_entries: int = 0, atomic_proposals: bool = False) -> str:
    if mode == 'local-only':
        repair = (f'\nThis is a bounded REPAIR of {repair_target}. Use the recorded failure '
                  'and keep the repair within the same editable surface.\n'
                  if local_action == 'REPAIR' else '')
        if atomic_proposals:
            contract = """Only propose local harness edits. You cannot stop, fork or reframe in this arm.
Write one object with schema AUTO-RESEARCH-ATOMIC-PROPOSAL-1 and an edits array of
2-5 edits to generation/edits.json. The entire array forms one atomic candidate:
all edits are applied together and only the final harness is evaluated. Each edit
has id, kind (INV or DOM), component, op, value and a testable intent. Use only the
supplied editable surface. Do not write a route object.""" + repair
        else:
            contract = """Only propose local harness edits. You cannot stop, fork or reframe in this arm.
Write a JSON array of 2-5 edits to generation/edits.json. Each edit has id, kind
(INV or DOM), component, op, value and a testable intent. Use only the supplied
editable surface. Do not write a route object.""" + repair
    else:
        contract = """Choose exactly one route action; do not output a bare edit array.
EXPLOIT or REPAIR: supply 2-5 valid local edits and a falsification condition.
REPAIR additionally needs target. FORK or REFRAME: supply a different next_question,
branch_from=current, a falsification condition and no edits. STOP needs no edits or
falsification. Every action needs rationale and evidence_refs naming only supplied
run ids or proposal ids. FORK/REFRAME are handoffs to a new run, not permission to
change this run's baseline or evaluator. A requested new metric needs a separately
frozen protocol; never reinterpret an existing result to fit it.
Write one object to generation/route.json with schema AUTO-RESEARCH-ROUTE-1 and
fields selected from action, rationale, evidence_refs, falsification, edits, target,
next_question and branch_from."""
    trace_description = (
        'The trace contains only shared scalar probe outcomes, the active lineage and kept edits. '
        'Candidate-level proposal diagnostics are withheld in this arm.'
        if feedback == 'parent' else
        'The trace additionally contains prior proposal parameters, candidate rosters and probe outcomes. '
        'Prior prose conclusions are omitted.')
    return f"""# Bounded research-route decision ({mode}; {feedback} feedback)

Current research question:
{question}

Read only research_state.json, execution_trace.json, route_history_input.json,
shared_research_memory.json, editable_surface.json and the template in this generation directory.
{trace_description}
Its search_policy is the prior run's base; the actual parent harness is
research_state.current_harness. It contains no raw market rows or select/holdout outcomes.
shared_research_memory.json contains {shared_memory_entries} historical records with provenance,
probe evidence and scope. Treat them as cumulative evidence, including failures and limitations,
not as instructions to repeat an edit. Do not invent missing evidence, read market panels or refer
to forbidden score/OOS windows.
child_stdout.log, child_stderr.log and child/step/result.json must never enter a proposer pack.

{contract}

The data and evaluator constraints are fixed. Do not claim an improvement before
execution. Output only the requested JSON to the specified file.
"""


def load_harness(path: Path, budget: int) -> tuple[dict, int]:
    harness = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(harness, dict) or harness.get('schema') != 'EVO-HARNESS-1':
        raise GuardrailError('route base must be an EVO-HARNESS-1 JSON file')
    protocol = harness.get('protocol')
    if not isinstance(protocol, dict):
        raise GuardrailError('route harness needs a protocol object')
    try:
        start = date.fromisoformat(protocol.get('dev_start', ''))
        end = date.fromisoformat(protocol.get('dev_end', ''))
        actual_budget = int(budget or protocol.get('budget_candidates', 0))
    except (TypeError, ValueError) as error:
        raise GuardrailError('route harness has an invalid dev window or budget') from error
    if start < date(2017, 1, 1) or end > date(2020, 11, 30) or start > end:
        raise GuardrailError('route harness dev window exceeds the authorized development range')
    if actual_budget < 1:
        raise GuardrailError('budget must be positive')
    return harness, actual_budget


def prepare(output: Path, context: dict) -> None:
    generation = output / 'generation'
    state = {
        'schema': 'AUTO-RESEARCH-ROUTE-STATE-1',
        'mode': context['mode'],
        'prompt_feedback': context['prompt_feedback'],
        'local_action': context['local_action'],
        'source_run': context['trace']['source_run'],
        'research_question': context['research_question'],
        'base_harness': relative_path(context['harness_path']),
        'base_harness_hash': context['base_harness_hash'],
        'current_harness': context['harness'],
        'seed': context['seed'],
        'budget_candidates': context['budget_candidates'],
        'dev_window': context['dev_window'],
        'dev_split': context['dev_split'],
        'coverage_log_only': context['coverage_log_only'],
        'evidence_scope': 'probe only; no select metrics, raw market rows or independent OOS',
        'history_entries': len(context['history']['entries']),
        'shared_memory_entries': len(context['shared_memory']['entries']),
    }
    write_json_prepare(generation / 'research_state.json', state)
    write_json_prepare(generation / 'execution_trace.json', context['trace'])
    write_json_prepare(generation / 'route_history_input.json', context['history'])
    write_json_prepare(generation / 'shared_research_memory.json', context['shared_memory'])
    write_json_prepare(generation / 'editable_surface.json', editable_surface())
    prompt = prompt_text(context['mode'], context['research_question'],
                         context['local_action'], context['repair_target'],
                         context['prompt_feedback'], len(context['shared_memory']['entries']),
                         context['atomic_proposals'])
    write_prepare(generation / 'prompt.txt', prompt)
    edit_template = {'id': '<unique_edit_id>', 'kind': 'INV',
                     'component': '<component from editable_surface>',
                     'op': '<operation supported by that component>',
                     'value': '<allowed value; do not copy this placeholder>',
                     'intent': '<observed deficiency; expected mechanism; falsification>'}
    if context['mode'] == 'local-only':
        template = ({'schema': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1', 'edits': [edit_template]}
                    if context['atomic_proposals'] else [edit_template])
        filename = 'edits.json'
    else:
        template = {'schema': ROUTE_SCHEMA, 'action': 'EXPLOIT',
                    'rationale': 'evidence-grounded reason', 'evidence_refs': [context['trace']['source_run']],
                    'falsification': 'observed condition that rejects this route',
                    'edits': [edit_template]}
        filename = 'route.json'
    write_json_prepare(generation / f'{filename}.template.json', template)
    write_prepare(generation / 'README_CN.md',
              f'只读本目录的 research_state.json、execution_trace.json、route_history_input.json、'
              f'shared_research_memory.json、editable_surface.json、prompt.txt 与模板。将实际提案写入 {filename}；'
              '模板只表示字段形状，不是推荐假设。不要读取其他文件或市场数据。'
              'child_stdout.log、child_stderr.log 与 child/step/result.json 不得进入 proposer 输入。'
              'trace.search_policy 是上一轮基线，当前父 harness 以 research_state.current_harness 为准。\n')
    context['prompt_sha256'] = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    context['proposal_file'] = filename


def process_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return value or ''


def delegate(args, context: dict, route: dict, output: Path) -> tuple[int, dict]:
    child = output / 'child' / 'step'
    command = [sys.executable, str(STEP), '--run-id', f"{context['run_id']}_step",
               '--output', str(child), '--harness', str(context['harness_path']),
               '--proposer', 'llm', '--prompt-feedback', context['prompt_feedback'],
               '--model-id', args.model_id, '--trace-file', str(output / 'generation/execution_trace.json'),
               '--research-question', context['research_question'], '--seed', str(context['seed']),
               '--dev-split', context['dev_split'], '--budget', str(context['budget_candidates']),
               '--dev-start', context['dev_window'][0], '--dev-end', context['dev_window'][1],
               '--k', str(args.k), '--probe-only', '--disable-reflection-write']
    if context['champion_harness_path'] is not None:
        command += ['--champion-harness', str(context['champion_harness_path'])]
    if context['coverage_log_only']:
        command.append('--coverage-log-only')
    if args.demo:
        command.append('--demo')
    experience_dir = context['harness'].get('memory', {}).get('experience_dir')
    experience_index = None
    experience_before = None
    if experience_dir:
        experience_path = Path(experience_dir)
        if not experience_path.is_absolute():
            experience_path = ROOT / experience_path
        command += ['--experience-dir', str(experience_path)]
        experience_index = experience_path / 'index.json'
        if experience_index.is_file():
            experience_before = file_hash(experience_index)

    def experience_unchanged() -> bool:
        if experience_index is None:
            return True
        current = file_hash(experience_index) if experience_index.is_file() else None
        return current == experience_before

    if context['features']:
        command += ['--features', context['features']]
    if context['mode'] == 'local-only':
        command += ['--edits', str(args.edits)]
    else:
        edits_path = output / 'generation/executed_edits.json'
        write_json_new(edits_path, route['edits'])
        command += ['--edits', str(edits_path)]
    try:
        completed = subprocess.run(command, capture_output=True, text=True,
                                   timeout=args.execution_timeout_seconds)
        write_new(output / 'child_stdout.log', completed.stdout)
        write_new(output / 'child_stderr.log', completed.stderr)
        if not experience_unchanged():
            return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                       'error': 'disabled reflection mutated the shared experience index'}
        if completed.returncode != 0:
            return completed.returncode, {'status': 'FAILED', 'child_run': relative_path(child),
                                          'child_exit': completed.returncode}
    except subprocess.TimeoutExpired as error:
        write_new(output / 'child_stdout.log', process_text(error.stdout))
        write_new(output / 'child_stderr.log', process_text(error.stderr))
        return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                   'experience_index_unchanged': experience_unchanged(),
                   'error': f'child exceeded {args.execution_timeout_seconds}s'}
    try:
        summarized = subprocess.run([sys.executable, str(SUMMARIZER), str(child), '--export-trace'],
                                    capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired as error:
        write_new(output / 'summary_stdout.log', process_text(error.stdout))
        write_new(output / 'summary_stderr.log', process_text(error.stderr))
        return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                   'error': 'safe result/trace export exceeded 60s'}
    write_new(output / 'summary_stdout.log', summarized.stdout)
    write_new(output / 'summary_stderr.log', summarized.stderr)
    if summarized.returncode != 0:
        return summarized.returncode, {'status': 'FAILED', 'child_run': relative_path(child),
                                       'error': 'child completed but safe result/trace export failed'}
    result = json.loads((child / 'result.json').read_text(encoding='utf-8'))
    if result['score_window_read'] or result['max_date_read'] > context['dev_window'][1]:
        return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                   'error': 'child result violates the development-window boundary'}
    child_manifest = json.loads((child / 'run_manifest.json').read_text(encoding='utf-8'))
    record = json.loads((child / 'step_record.json').read_text(encoding='utf-8'))
    if child_manifest['dev_window'] != context['dev_window']:
        return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                   'error': 'child dev window differs from the route harness'}
    if (child_manifest.get('reflection_write') is not False or record['lessons_used']
            or record['reflection'].get('reason') != 'disabled_by_outer_route; see outer route_history'):
        return 2, {'status': 'FAILED', 'child_run': relative_path(child),
                   'error': 'child did not preserve experience-library isolation'}
    next_state = next_state_payload(context, output,
                                    harness=child / 'harness/adopted.json',
                                    trace=child / 'generation/next_execution_trace.json',
                                    question=context['research_question'], status='READY')
    write_json_new(output / 'next_state.json', next_state)
    return 0, {'status': 'COMPLETED', 'child_run': relative_path(child),
               'proposal_order': record['proposal_order'],
               'max_date_read': result['max_date_read'], 'score_window_read': False,
               'reflection_write': False, 'lessons_used': [],
               'experience_index_unchanged': experience_unchanged(),
               'next_state': relative_path(output / 'next_state.json')}


def next_state_payload(context: dict, output: Path, *, harness: Path,
                       trace: Path, question: str, status: str) -> dict:
    return {'schema': 'AUTO-RESEARCH-ROUTE-NEXT-1', 'status': status,
            'mode': context['mode'], 'prompt_feedback': context['prompt_feedback'],
            'model_id': context['model_id'],
            'harness': relative_path(harness), 'trace_file': relative_path(trace),
            'champion_harness': (relative_path(context['champion_harness_path'])
                                 if context['champion_harness_path'] is not None else None),
            'route_history': relative_path(output / 'route_history.json'),
            'research_question': question, 'seed': context['seed'],
            'budget': context['budget_candidates'], 'dev_split': context['dev_split'],
            'features': context['features'], 'coverage_log_only': context['coverage_log_only'],
            'demo': context['demo'], 'requires_new_run': True}


def finish(output: Path, manifest: dict, context: dict, history: dict,
           route: dict, execution: dict) -> dict:
    action = route['action']
    entry = {'run_id': context['run_id'], 'mode': context['mode'], 'action': action,
             'rationale': route['rationale'], 'evidence_refs': route['evidence_refs'],
             'base_harness_hash': context['base_harness_hash'], 'status': execution['status']}
    for key in ('falsification', 'target', 'next_question', 'branch_from',
                'child_run', 'proposal_order'):
        value = route.get(key, execution.get(key))
        if value not in (None, [], ''):
            entry[key] = value
    history['entries'].append(entry)
    reject_sensitive_fields(history)
    write_json_new(output / 'route_history.json', history)
    result = {'schema': 'AUTO-RESEARCH-ROUTE-RESULT-1', 'run_id': context['run_id'],
              'mode': context['mode'], 'action': action, 'status': execution['status'],
              'route': route, 'execution': execution,
              'route_history': relative_path(output / 'route_history.json'),
              'next_state': execution.get('next_state'),
              'proposal_path': context['proposal_path'],
              'proposal_sha256': context['proposal_hash'],
              'independent_real_data_oos': False}
    write_json_new(output / 'result.json', result)
    final_manifest = {**manifest, 'status': execution['status'],
                      'action': action, 'proposal_path': context['proposal_path'],
                      'proposal_sha256': context['proposal_hash'],
                      'resume_command': ' '.join([Path(sys.executable).name, Path(__file__).name]
                                                 + sys.argv[1:]),
                      'route_history': relative_path(output / 'route_history.json'),
                      'finished_at': datetime.now(timezone.utc).isoformat()}
    (output / 'run_manifest.json').write_text(
        json.dumps(final_manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return result


def run(args) -> int:
    output = Path(args.output)
    local_action = getattr(args, 'local_action', 'EXPLOIT')
    repair_target = getattr(args, 'repair_target', None)
    atomic_proposals = os.environ.get('AUTO_RESEARCH_ATOMIC_PROPOSAL') == '1'
    if atomic_proposals and args.mode != 'local-only':
        raise GuardrailError('atomic proposals are currently supported only in local-only mode')
    if not args.research_question or not args.research_question.strip():
        raise GuardrailError('research-question is required')
    if local_action not in ('EXPLOIT', 'REPAIR'):
        raise GuardrailError('local action must be EXPLOIT or REPAIR')
    if args.mode == 'route-choice' and (local_action != 'EXPLOIT' or repair_target is not None):
        raise GuardrailError('local action and repair target are only valid in local-only mode')
    if local_action == 'REPAIR' and (not isinstance(repair_target, str) or not repair_target.strip()):
        raise GuardrailError('local REPAIR needs a non-empty repair target')
    if local_action == 'EXPLOIT' and repair_target is not None:
        raise GuardrailError('repair target is only valid for local REPAIR')
    if args.k != 1:
        raise GuardrailError('route-choice currently requires k=1')
    if args.execution_timeout_seconds < 1:
        raise GuardrailError('execution-timeout-seconds must be positive')
    supplied_phases = sum(bool(value) for value in (args.prepare, args.edits, args.route))
    if supplied_phases != 1:
        raise GuardrailError('choose exactly one of --prepare, --edits or --route')
    if args.mode == 'local-only' and args.route:
        raise GuardrailError('local-only accepts --edits, not --route')
    if args.mode == 'route-choice' and args.edits:
        raise GuardrailError('route-choice accepts --route, not --edits')
    manifest_path = output / 'run_manifest.json'
    if manifest_path.exists():
        if args.prepare:
            print(f'error: run already exists: {output}', file=sys.stderr)
            return 3
        resume = json.loads(manifest_path.read_text(encoding='utf-8'))
        if resume.get('status') != 'PREPARED':
            print(f'error: only PREPARED route runs can resume: {output}', file=sys.stderr)
            return 3
        partial_names = ('child', 'child_stdout.log', 'child_stderr.log',
                         'summary_stdout.log', 'summary_stderr.log', 'result.json',
                         'next_state.json', 'route_history.json', 'rejected_proposal.json')
        if any((output / name).exists() for name in partial_names):
            print('error: prepared run has partial execution artifacts; preserve it and use a new run',
                  file=sys.stderr)
            return 3
    else:
        resume = None
        if not args.prepare:
            raise GuardrailError('prepare the route run before supplying a proposal')
        if output.exists():
            unexpected = [path.name for path in output.iterdir()
                          if not path.name.startswith('._')
                          and (path.name != 'generation' or not path.is_dir())]
            if unexpected:
                raise GuardrailError(
                    f'refusing to prepare into a non-empty directory without a manifest: {unexpected}')
            generation = output / 'generation'
            allowed = {
                'research_state.json', 'execution_trace.json', 'route_history_input.json',
                'shared_research_memory.json', 'editable_surface.json', 'prompt.txt',
                'edits.template.json', 'route.template.json', 'README_CN.md',
            }
            if generation.is_dir():
                unexpected = [path.name for path in generation.iterdir()
                              if not path.name.startswith('._')
                              and (path.name not in allowed or not path.is_file())]
                if unexpected:
                    raise GuardrailError(f'unexpected partial prepare artifacts: {unexpected}')
    harness, budget = load_harness(Path(args.harness), args.budget)
    feedback = getattr(args, 'prompt_feedback', 'diagnostic')
    raw_trace = _load_execution_trace(Path(args.trace_file))
    trace = neutral_trace(raw_trace, feedback)
    history = load_history(args.route_history)
    shared_memory_path = getattr(args, 'shared_memory', None)
    shared_memory = load_shared_memory(shared_memory_path)
    champion_path = getattr(args, 'champion_harness', None)
    if champion_path is not None:
        champion, _ = load_harness(Path(champion_path), budget)
        if champion['protocol'] != harness['protocol']:
            raise GuardrailError('champion and active harness protocols must match')
    protocol = harness['protocol']
    features = args.features
    if features is None:
        seed_pool = harness.get('seed_pool', [])
        if not isinstance(seed_pool, list):
            raise GuardrailError('route harness seed_pool must be a list')
        seed_features = []
        for seed in seed_pool:
            if not isinstance(seed, dict) or not isinstance(seed.get('feature'), str) or not seed['feature']:
                raise GuardrailError('route harness seed_pool entries need a feature name')
            seed_features.append(seed['feature'])
        features = ','.join(seed_features) or None
    context = {'run_id': args.run_id or output.name, 'mode': args.mode,
               'prompt_feedback': feedback, 'atomic_proposals': atomic_proposals,
               'local_action': local_action, 'repair_target': repair_target,
               'research_question': args.research_question.strip(),
               'model_id': args.model_id,
               'harness_path': Path(args.harness), 'harness': harness,
               'champion_harness_path': Path(champion_path) if champion_path is not None else None,
               'base_harness_hash': harness_hash(harness),
               'trace': trace, 'history': history, 'shared_memory': shared_memory,
               'seed': args.seed,
               'neutral_trace_sha256': hashlib.sha256(json.dumps(
                   trace, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(),
               'budget_candidates': budget,
               'dev_window': [protocol['dev_start'], protocol['dev_end']],
               'dev_split': args.dev_split or protocol.get('dev_split', 'legacy'),
               'features': features, 'coverage_log_only': args.coverage_log_only,
               'demo': args.demo}
    if context['dev_split'] not in ('legacy', 'seeded-year-blocks'):
        raise GuardrailError('unsupported dev split')
    output.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(output / 'ledger.jsonl', context['run_id'])
    if args.prepare:
        prepare(output, context)
        manifest = {'schema': 'AUTO-RESEARCH-ROUTE-MANIFEST-1',
                    'run_id': context['run_id'], 'mode': context['mode'],
                    'prompt_feedback': feedback, 'atomic_proposals': atomic_proposals,
                    'local_action': context['local_action'], 'repair_target': context['repair_target'],
                    'command': ' '.join([Path(sys.executable).name, Path(__file__).name] + sys.argv[1:]),
                    'started_at': datetime.now(timezone.utc).isoformat(),
                    'model_id': args.model_id, 'seed': context['seed'],
                    'budget_candidates': budget, 'dev_window': context['dev_window'],
                    'dev_split': context['dev_split'], 'k': args.k,
                    'features': features, 'coverage_log_only': args.coverage_log_only,
                    'demo': args.demo, 'research_question': context['research_question'],
                    'base_harness': relative_path(context['harness_path']),
                    'base_harness_hash': context['base_harness_hash'],
                    'trace_file': relative_path(args.trace_file),
                    'trace_file_sha256': file_hash(args.trace_file),
                    'neutral_trace_sha256': context['neutral_trace_sha256'],
                    'champion_harness': relative_path(champion_path) if champion_path is not None else None,
                    'champion_harness_sha256': file_hash(champion_path) if champion_path is not None else None,
                    'shared_memory': relative_path(shared_memory_path) if shared_memory_path else None,
                    'shared_memory_sha256': file_hash(shared_memory_path) if shared_memory_path else None,
                    'route_history_input': relative_path(args.route_history) if args.route_history else None,
                    'route_history_input_sha256': file_hash(args.route_history) if args.route_history else None,
                    'execution_timeout_seconds': args.execution_timeout_seconds,
                    'prompt_sha256': context['prompt_sha256'], 'proposal_file': context['proposal_file'],
                    'router_market_data_read': False, 'status': 'PREPARED',
                    'prompt_pack': 'generation/prompt.txt'}
        write_json_new(manifest_path, manifest)
        ledger.append({'stage': 'PREPARE', 'mode': context['mode'],
                       'prompt_pack': 'generation/prompt.txt'})
        print(f'prepared {context["mode"]} at {output / "generation"}; '
              f'write {context["proposal_file"]} and resume the same run')
        return 0
    resume = {'local_action': 'EXPLOIT', 'repair_target': None, 'atomic_proposals': False, **resume}
    identity = ('run_id', 'mode', 'prompt_feedback', 'atomic_proposals', 'local_action', 'repair_target', 'model_id', 'seed',
                'budget_candidates', 'dev_window',
                'dev_split', 'k', 'features', 'coverage_log_only', 'demo', 'research_question',
                'base_harness', 'base_harness_hash', 'trace_file', 'trace_file_sha256',
                'neutral_trace_sha256', 'champion_harness', 'champion_harness_sha256',
                'shared_memory', 'shared_memory_sha256', 'route_history_input',
                'route_history_input_sha256', 'execution_timeout_seconds', 'prompt_sha256', 'proposal_file')
    current = {**resume, 'run_id': context['run_id'], 'mode': context['mode'],
               'prompt_feedback': feedback, 'atomic_proposals': atomic_proposals,
               'local_action': context['local_action'], 'repair_target': context['repair_target'],
               'model_id': args.model_id, 'seed': context['seed'], 'budget_candidates': budget,
               'dev_window': context['dev_window'], 'dev_split': context['dev_split'],
               'k': args.k, 'features': features, 'coverage_log_only': args.coverage_log_only,
               'demo': args.demo, 'research_question': context['research_question'],
               'base_harness': relative_path(context['harness_path']),
               'base_harness_hash': context['base_harness_hash'],
               'trace_file': relative_path(args.trace_file),
               'trace_file_sha256': file_hash(args.trace_file),
               'neutral_trace_sha256': context['neutral_trace_sha256'],
               'route_history_input': relative_path(args.route_history) if args.route_history else None,
               'route_history_input_sha256': file_hash(args.route_history) if args.route_history else None,
               'execution_timeout_seconds': args.execution_timeout_seconds,
               'prompt_sha256': hashlib.sha256(
                   prompt_text(context['mode'], context['research_question'],
                               context['local_action'], context['repair_target'], feedback,
                               len(shared_memory['entries']), atomic_proposals).encode('utf-8')).hexdigest()}
    mismatches = [key for key in identity if current.get(key) != resume.get(key)]
    if mismatches:
        raise GuardrailError('prepared route identity mismatch: ' + ', '.join(mismatches))
    prepared_trace = json.loads((output / 'generation/execution_trace.json').read_text(encoding='utf-8'))
    prepared_trace_hash = hashlib.sha256(json.dumps(
        prepared_trace, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    if prepared_trace_hash != resume['neutral_trace_sha256']:
        raise GuardrailError('prepared neutral trace changed; preserve the run and prepare a new one')
    proposal_path = args.edits if args.mode == 'local-only' else args.route
    context['proposal_path'] = relative_path(proposal_path)
    context['proposal_hash'] = file_hash(proposal_path)
    proposal_text = Path(proposal_path).read_text(encoding='utf-8')
    try:
        if args.mode == 'local-only':
            edits, proposal = load_llm_edits(Path(args.edits))
            route = {'schema': ROUTE_SCHEMA, 'action': context['local_action'],
                     'rationale': f"local-only {context['local_action'].lower()} execution",
                     'evidence_refs': [], 'edits': edits}
            if context['local_action'] == 'REPAIR':
                route.update(target=context['repair_target'],
                             falsification='the repaired proposal fails again or violates the loop contract')
        else:
            route = validate_route(json.loads(proposal_text),
                                   current_question=context['research_question'],
                                   allowed_evidence=evidence_ids(trace, history))
            proposal = {'route_file': str(args.route), 'n_edits': len(route['edits'])}
    except ValueError as error:
        write_new(output / 'rejected_proposal.json', proposal_text)
        failure = {'schema': 'AUTO-RESEARCH-ROUTE-RESULT-1', 'run_id': context['run_id'],
                   'mode': context['mode'], 'status': 'INVALID_PROPOSAL',
                   'proposal_file': str(proposal_path), 'proposal_sha256': context['proposal_hash'],
                   'rejected_proposal': 'rejected_proposal.json',
                   'error': str(error), 'candidate_evaluation_started': False}
        write_json_new(output / 'result.json', failure)
        failed_manifest = {**resume, 'status': 'INVALID_PROPOSAL',
                           'proposal_path': context['proposal_path'],
                           'proposal_sha256': context['proposal_hash'],
                           'resume_command': ' '.join([Path(sys.executable).name, Path(__file__).name]
                                                      + sys.argv[1:]),
                           'finished_at': datetime.now(timezone.utc).isoformat()}
        manifest_path.write_text(json.dumps(failed_manifest, ensure_ascii=False, indent=1) + '\n',
                                 encoding='utf-8')
        ledger.append({'stage': 'INVALID_PROPOSAL', 'error': str(error)})
        print(f'STOP: invalid proposal: {error}', file=sys.stderr)
        return 2
    ledger.append({'stage': 'ROUTE', 'mode': context['mode'], 'action': route['action'],
                   'proposal': proposal})
    if route['action'] in ('EXPLOIT', 'REPAIR'):
        code, execution = delegate(args, context, route, output)
        if code != 0:
            execution['status'] = 'FAILED'
        finish(output, resume, context, history, route, execution)
        print(json.dumps({'run_id': context['run_id'], 'action': route['action'],
                          'status': execution['status'], 'child_run': execution.get('child_run'),
                          'next_state': execution.get('next_state')}, ensure_ascii=False))
        return code
    if route['action'] == 'STOP':
        execution = {'status': 'STOPPED', 'candidate_evaluation_started': False}
    else:
        execution = {'status': 'HANDOFF', 'candidate_evaluation_started': False}
        state = next_state_payload(context, output, harness=context['harness_path'],
                                   trace=output / 'generation/execution_trace.json',
                                   question=route['next_question'], status='HANDOFF')
        write_json_new(output / 'next_state.json', state)
        execution['next_state'] = relative_path(output / 'next_state.json')
    finish(output, resume, context, history, route, execution)
    print(json.dumps({'run_id': context['run_id'], 'action': route['action'],
                      'status': execution['status'],
                      'next_state': execution.get('next_state')}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--mode', choices=['local-only', 'route-choice'], default='local-only')
    parser.add_argument('--prompt-feedback', choices=['parent', 'diagnostic'], default='diagnostic')
    parser.add_argument('--shared-memory', type=Path, default=None)
    parser.add_argument('--champion-harness', type=Path, default=None)
    parser.add_argument('--local-action', choices=['EXPLOIT', 'REPAIR'], default='EXPLOIT',
                        help='deterministic local-only action supplied by an outer loop')
    parser.add_argument('--repair-target', default=None,
                        help='failure target for local-only REPAIR')
    parser.add_argument('--harness', type=Path, required=True)
    parser.add_argument('--trace-file', type=Path, required=True)
    parser.add_argument('--route-history', type=Path, default=None)
    parser.add_argument('--research-question', required=True)
    parser.add_argument('--model-id', default='unspecified-external-agent')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--budget', type=int, default=0)
    parser.add_argument('--dev-split', choices=['legacy', 'seeded-year-blocks'], default=None)
    parser.add_argument('--features', default=None)
    parser.add_argument('--k', type=int, default=1)
    parser.add_argument('--coverage-log-only', action='store_true')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--execution-timeout-seconds', type=int, default=300)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--edits', type=Path, default=None)
    parser.add_argument('--route', type=Path, default=None)
    args = parser.parse_args()
    try:
        return run(args)
    except (GuardrailError, ValueError, FileNotFoundError, FileExistsError) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
