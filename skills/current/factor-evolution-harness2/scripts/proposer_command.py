#!/usr/bin/env python3
"""Connect the bounded research proposer protocol to the existing LLM client."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_research.agents.llm import MODEL as CONFIGURED_MODEL
from auto_research.agents.llm import chat, extract_json, get_last_usage
from trace_contract import reject_sensitive_fields

LOCAL_EVIDENCE = ('parent_metrics.json', 'parent_harness.json', 'editable_surface.json',
                  'experience_lessons.json', 'diagnostics.json', 'execution_trace.json')
ROUTE_EVIDENCE = ('research_state.json', 'execution_trace.json',
                  'route_history_input.json', 'editable_surface.json',
                  'shared_research_memory.json')
MAX_EVIDENCE_CHARACTERS = 500000


def write_json_new(path: Path, payload) -> None:
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def model_id(expected: str | None = None) -> str:
    value = expected or os.environ.get('AUTO_RESEARCH_MODEL_ID', '')
    configured = os.environ.get('AUTO_RESEARCH_LLM_MODEL', CONFIGURED_MODEL)
    if not value or value == 'unspecified-external-agent':
        raise ValueError('AUTO_RESEARCH_MODEL_ID must identify the frozen proposer model')
    if value != configured and not value.endswith('/' + configured):
        raise ValueError('AUTO_RESEARCH_LLM_MODEL differs from the frozen proposer model')
    return value


def model_call(prompt: str, max_tokens_environment: str) -> tuple[str, dict]:
    max_tokens = int(os.environ.get(max_tokens_environment, '4000'))
    if max_tokens < 1:
        raise ValueError(f'{max_tokens_environment} must be positive')
    temperature = float(os.environ.get('AUTO_RESEARCH_PROPOSER_TEMPERATURE', '0.2'))
    content = chat([{'role': 'user', 'content': prompt}], temperature=temperature,
                   max_tokens=max_tokens, retries=0)
    usage = get_last_usage()
    if not usage.get('available'):
        raise RuntimeError('model response has no token usage; refusing to under-report the budget')
    return content, usage


def proposer_prompt() -> str:
    generation = Path(os.environ['AUTO_RESEARCH_GENERATION'])
    mode = os.environ['AUTO_RESEARCH_MODE']
    if mode not in ('local-only', 'route-choice'):
        raise ValueError('unsupported proposer mode')
    prompt_path = generation / 'prompt.txt'
    if not prompt_path.is_file() or prompt_path.is_symlink():
        raise ValueError('proposer prompt must be a regular generation file')
    prompt = prompt_path.read_text(encoding='utf-8')
    names = ROUTE_EVIDENCE if (generation / 'research_state.json').exists() else LOCAL_EVIDENCE
    required = set(names[:4])
    sections, total = [], 0
    for name in names:
        path = generation / name
        if not path.exists():
            if name in required:
                raise ValueError(f'missing proposer evidence: {name}')
            continue
        if not path.is_file() or path.is_symlink():
            raise ValueError(f'proposer evidence must be a regular generation file: {name}')
        payload = json.loads(path.read_text(encoding='utf-8'))
        reject_sensitive_fields(payload)
        text = json.dumps(payload, ensure_ascii=False)
        total += len(text)
        if total > MAX_EVIDENCE_CHARACTERS:
            raise ValueError('proposer evidence exceeds the bounded context size')
        sections.append(f'\n\n## {name}\n{text}')
    return prompt + ''.join(sections)


def usage_record(usage: dict) -> dict:
    return {'schema': 'AUTO-RESEARCH-MODEL-USAGE-1', 'model_id': model_id(),
            'calls': 1, 'input_tokens': usage['prompt_tokens'],
            'output_tokens': usage['completion_tokens']}


def run_proposer() -> int:
    content, usage = model_call(proposer_prompt(), 'AUTO_RESEARCH_PROPOSER_MAX_TOKENS')
    write_json_new(Path(os.environ['AUTO_RESEARCH_USAGE']), usage_record(usage))
    proposal = extract_json(content)
    mode = os.environ['AUTO_RESEARCH_MODE']
    atomic = os.environ.get('AUTO_RESEARCH_ATOMIC_PROPOSAL') == '1'
    if atomic:
        if (not isinstance(proposal, dict)
                or proposal.get('schema') != 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1'
                or not isinstance(proposal.get('edits'), list)):
            raise ValueError('atomic proposer output must contain the frozen schema and edits array')
    elif mode == 'local-only' and not isinstance(proposal, list):
        raise ValueError('local-only proposer output must be a JSON array')
    if mode == 'route-choice' and not isinstance(proposal, dict):
        raise ValueError('route proposer output must be a JSON object')
    reject_sensitive_fields(proposal)
    write_json_new(Path(os.environ['AUTO_RESEARCH_PROPOSAL']), proposal)
    return 0


class BudgetedReflection:
    def __init__(self, usage_path: Path, budget: dict, model_identity: str):
        self.usage_path = Path(usage_path)
        self.model_identity = model_identity
        self.budget = self._validate_budget(budget)
        self.usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}
        if self.usage_path.is_file():
            payload = json.loads(self.usage_path.read_text(encoding='utf-8'))
            if (not isinstance(payload, dict)
                    or payload.get('schema') != 'AUTO-RESEARCH-REFLECTION-USAGE-1'):
                raise ValueError('invalid reflection usage record')
            restored = payload.get('usage')
            self._validate_counters(restored)
            self.usage = restored

    @staticmethod
    def _validate_budget(payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {
                'max_model_calls', 'max_input_tokens', 'max_output_tokens'}:
            raise ValueError('reflection budget needs calls, input tokens and output tokens')
        for key, value in payload.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'reflection budget {key} must be positive')
        return payload

    @staticmethod
    def _validate_counters(payload: dict) -> None:
        if not isinstance(payload, dict) or set(payload) != {
                'model_calls', 'input_tokens', 'output_tokens'}:
            raise ValueError('invalid reflection usage counters')
        for value in payload.values():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError('invalid reflection usage counters')

    def _persist(self) -> None:
        temporary = self.usage_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'schema': 'AUTO-RESEARCH-REFLECTION-USAGE-1', 'usage': self.usage,
        }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        temporary.replace(self.usage_path)

    def __call__(self, prompt: str) -> str:
        model_id(self.model_identity)
        for key, budget_key in (('model_calls', 'max_model_calls'),
                                ('input_tokens', 'max_input_tokens'),
                                ('output_tokens', 'max_output_tokens')):
            if self.usage[key] >= self.budget[budget_key]:
                raise RuntimeError(f'reflection budget exhausted before model call: {key}')
        content, usage = model_call(prompt, 'AUTO_RESEARCH_REFLECTION_MAX_TOKENS')
        updated = {'model_calls': self.usage['model_calls'] + 1,
                   'input_tokens': self.usage['input_tokens'] + usage['prompt_tokens'],
                   'output_tokens': self.usage['output_tokens'] + usage['completion_tokens']}
        self.usage = updated
        self._persist()
        for key, budget_key in (('model_calls', 'max_model_calls'),
                                ('input_tokens', 'max_input_tokens'),
                                ('output_tokens', 'max_output_tokens')):
            if updated[key] > self.budget[budget_key]:
                raise RuntimeError(f'reflection budget exhausted: {key}')
        return content

    def get_usage(self) -> dict:
        return dict(self.usage)


def main() -> int:
    try:
        return run_proposer()
    except (ValueError, RuntimeError, FileNotFoundError, FileExistsError, json.JSONDecodeError) as error:
        print(f'proposer failed: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
