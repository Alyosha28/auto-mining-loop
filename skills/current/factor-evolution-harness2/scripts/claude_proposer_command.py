#!/usr/bin/env python3
"""Run bounded research proposals through Claude Code with all tools disabled."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from auto_research.agents.llm import extract_json
from proposer_command import proposer_prompt, write_json_new
from trace_contract import reject_sensitive_fields

LOCAL_SCHEMA = {
    'type': 'object', 'required': ['edits'],
    'properties': {
        'edits': {
            'type': 'array', 'minItems': 2, 'maxItems': 5,
            'items': {'type': 'object',
                      'required': ['id', 'kind', 'component', 'op', 'value', 'intent']},
        },
    },
}
ATOMIC_LOCAL_SCHEMA = {
    'type': 'object', 'required': ['schema', 'edits'],
    'properties': {
        'schema': {'const': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1'},
        'edits': LOCAL_SCHEMA['properties']['edits'],
    },
}
ROUTE_SCHEMA = {
    'type': 'object',
    'required': ['schema', 'action', 'rationale', 'evidence_refs', 'edits'],
    'properties': {
        'edits': {
            'type': 'array',
            'items': {'type': 'object',
                      'required': ['id', 'kind', 'component', 'op', 'value', 'intent']},
        },
    },
}


def cli_command(prompt: str, model_id: str, schema: dict | None) -> list[str]:
    command = [
        os.environ.get('CLAUDE_CODE_CLI', 'claude'), '-p', prompt,
        '--tools', '', '--output-format', 'json',
        '--no-session-persistence', '--setting-sources', '',
        '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--max-budget-usd', os.environ.get('AUTO_RESEARCH_CLAUDE_MAX_USD', '0.50'),
        '--effort', 'low',
    ]
    cli_model = os.environ.get('AUTO_RESEARCH_CLAUDE_CLI_MODEL')
    if cli_model:
        command += ['--model', cli_model]
    if schema is not None:
        command += ['--json-schema', json.dumps(schema, ensure_ascii=False)]
    return command


def call_cli(prompt: str, model_id: str, schema: dict | None = None) -> tuple[object, dict, dict]:
    completed = subprocess.run(cli_command(prompt, model_id, schema),
                               capture_output=True, text=True)
    if completed.returncode != 0:
        details = '\n'.join(value for value in (completed.stderr, completed.stdout) if value)
        raise RuntimeError(
            f'Claude CLI exited with {completed.returncode}: {details[-1500:]}')
    payload = json.loads(completed.stdout)
    if not isinstance(payload, dict) or payload.get('is_error'):
        raise RuntimeError(f'Claude CLI returned an error: {str(payload)[:500]}')
    model_usage = payload.get('modelUsage')
    if not isinstance(model_usage, dict) or model_id not in model_usage:
        actual = sorted(model_usage) if isinstance(model_usage, dict) else []
        raise RuntimeError(
            f'Claude CLI model identity is missing or differs from the frozen model: {actual}')
    usage = model_usage[model_id]
    input_tokens = sum(int(usage.get(key) or 0) for key in (
        'inputTokens', 'cacheCreationInputTokens', 'cacheReadInputTokens'))
    output_tokens = int(usage.get('outputTokens') or 0)
    if input_tokens < 0 or output_tokens < 0:
        raise RuntimeError('Claude CLI returned invalid token usage')
    output_limit_exceeded = output_tokens > int(
        os.environ.get('AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS', '4000'))
    content = payload.get('structured_output', payload.get('result'))
    if content is None:
        raise RuntimeError('Claude CLI returned no structured output or result')
    metadata = {'model': model_id, 'input_tokens': input_tokens, 'output_tokens': output_tokens,
                'output_limit_exceeded': output_limit_exceeded,
                'cost_usd': payload.get('total_cost_usd'),
                'duration_ms': payload.get('duration_ms')}
    return content, {'input_tokens': input_tokens, 'output_tokens': output_tokens}, metadata


def run_proposer() -> int:
    model_id = os.environ.get('AUTO_RESEARCH_MODEL_ID', '')
    if not model_id or model_id == 'unspecified-external-agent':
        raise ValueError('AUTO_RESEARCH_MODEL_ID must identify the frozen proposer model')
    mode = os.environ['AUTO_RESEARCH_MODE']
    if mode not in ('local-only', 'route-choice'):
        raise ValueError('unsupported proposer mode')
    atomic = os.environ.get('AUTO_RESEARCH_ATOMIC_PROPOSAL') == '1'
    if atomic and mode != 'local-only':
        raise ValueError('atomic proposals require local-only mode')
    schema = ATOMIC_LOCAL_SCHEMA if atomic else LOCAL_SCHEMA if mode == 'local-only' else ROUTE_SCHEMA
    content, usage, metadata = call_cli(proposer_prompt(), model_id, schema)
    usage_path = Path(os.environ['AUTO_RESEARCH_USAGE'])
    write_json_new(usage_path, {
        'schema': 'AUTO-RESEARCH-MODEL-USAGE-1', 'model_id': model_id,
        'calls': 1, **usage,
    })
    if metadata['output_limit_exceeded']:
        write_json_new(usage_path.parent / 'per_call_budget_exceeded.json', {
            'schema': 'AUTO-RESEARCH-PER-CALL-BUDGET-EXCEEDED-1', **usage,
        })
        raise RuntimeError('Claude CLI exceeded the per-call output token limit')
    proposal = extract_json(content) if isinstance(content, str) else content
    if mode == 'local-only' and isinstance(proposal, dict) and set(proposal) == {'edits'} and not atomic:
        proposal = proposal['edits']
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


class ClaudeCLIReflection:
    def __init__(self, usage_path: Path, budget: dict, model_identity: str):
        self.usage_path = Path(usage_path)
        self.model_identity = model_identity
        if not isinstance(budget, dict) or set(budget) != {
                'max_model_calls', 'max_input_tokens', 'max_output_tokens'}:
            raise ValueError('reflection budget needs calls, input tokens and output tokens')
        for key, value in budget.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'reflection budget {key} must be positive')
        self.budget = budget
        self.usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}
        if self.usage_path.is_file():
            payload = json.loads(self.usage_path.read_text(encoding='utf-8'))
            if (not isinstance(payload, dict)
                    or payload.get('schema') != 'AUTO-RESEARCH-REFLECTION-USAGE-1'
                    or set(payload.get('usage', {})) != set(self.usage)):
                raise ValueError('invalid reflection usage record')
            self.usage = payload['usage']

    def _persist(self) -> None:
        temporary = self.usage_path.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'schema': 'AUTO-RESEARCH-REFLECTION-USAGE-1', 'usage': self.usage,
        }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        temporary.replace(self.usage_path)

    def __call__(self, prompt: str) -> str:
        for key, budget_key in (('model_calls', 'max_model_calls'),
                                ('input_tokens', 'max_input_tokens'),
                                ('output_tokens', 'max_output_tokens')):
            if self.usage[key] >= self.budget[budget_key]:
                raise RuntimeError(f'reflection budget exhausted before model call: {key}')
        content, usage, metadata = call_cli(prompt, self.model_identity)
        if not isinstance(content, str):
            raise RuntimeError('Claude CLI reflection must return text')
        updated = {'model_calls': self.usage['model_calls'] + 1,
                   'input_tokens': self.usage['input_tokens'] + usage['input_tokens'],
                   'output_tokens': self.usage['output_tokens'] + usage['output_tokens']}
        self.usage = updated
        self._persist()
        if metadata['output_limit_exceeded']:
            raise RuntimeError('Claude CLI reflection exceeded the per-call output token limit')
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
