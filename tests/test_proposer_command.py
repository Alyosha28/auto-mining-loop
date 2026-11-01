#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
import sys
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
import proposer_command
import claude_proposer_command
from auto_research.agents import llm


class ProposerCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.generation = self.work / 'generation'
        self.generation.mkdir()
        (self.generation / 'prompt.txt').write_text('Propose bounded JSON edits.', encoding='utf-8')
        for name in ('parent_metrics.json', 'parent_harness.json',
                     'editable_surface.json', 'experience_lessons.json'):
            (self.generation / name).write_text(json.dumps({'name': name}), encoding='utf-8')
        self.environment = {
            'AUTO_RESEARCH_GENERATION': str(self.generation),
            'AUTO_RESEARCH_PROPOSAL': str(self.generation / 'edits.json'),
            'AUTO_RESEARCH_USAGE': str(self.generation / 'model_usage.json'),
            'AUTO_RESEARCH_MODE': 'local-only',
            'AUTO_RESEARCH_MODEL_ID': 'fixed-test-model',
            'AUTO_RESEARCH_LLM_MODEL': 'fixed-test-model',
            'AUTO_RESEARCH_PROPOSER_MAX_TOKENS': '4000',
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_proposer_uses_only_whitelisted_evidence_and_actual_usage(self) -> None:
        edits = [{'id': 'e1', 'kind': 'INV', 'component': 'operators',
                  'op': 'append', 'value': 'rank', 'intent': 'test'}]
        usage = {'available': True, 'prompt_tokens': 12, 'completion_tokens': 7,
                 'cached': False, 'api_calls': 1}
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(proposer_command, 'chat', return_value=json.dumps(edits)) as chat, \
                mock.patch.object(proposer_command, 'get_last_usage', return_value=usage):
            self.assertEqual(proposer_command.run_proposer(), 0)
        prompt = chat.call_args.args[0][0]['content']
        self.assertIn('parent_metrics.json', prompt)
        self.assertIn('editable_surface.json', prompt)
        self.assertNotIn('child_result.json', prompt)
        self.assertEqual(json.loads((self.generation / 'edits.json').read_text()), edits)
        usage_record = json.loads((self.generation / 'model_usage.json').read_text())
        self.assertEqual(usage_record['input_tokens'], 12)
        self.assertEqual(usage_record['output_tokens'], 7)

    def test_malformed_model_content_preserves_actual_usage(self) -> None:
        usage = {'available': True, 'prompt_tokens': 12, 'completion_tokens': 7,
                 'cached': False, 'api_calls': 1}
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(proposer_command, 'chat', return_value='not-json'), \
                mock.patch.object(proposer_command, 'get_last_usage', return_value=usage):
            with self.assertRaises(Exception):
                proposer_command.run_proposer()
        record = json.loads((self.generation / 'model_usage.json').read_text())
        self.assertEqual(record['calls'], 1)
        self.assertEqual(record['output_tokens'], 7)

    def test_local_route_generation_uses_route_evidence(self) -> None:
        for name in ('research_state.json', 'execution_trace.json',
                     'route_history_input.json', 'editable_surface.json',
                     'shared_research_memory.json'):
            (self.generation / name).write_text(json.dumps({'name': name}), encoding='utf-8')
        with mock.patch.dict(os.environ, self.environment, clear=False):
            prompt = proposer_command.proposer_prompt()
        self.assertIn('research_state.json', prompt)
        self.assertIn('route_history_input.json', prompt)
        self.assertIn('shared_research_memory.json', prompt)
        self.assertNotIn('parent_metrics.json', prompt)

    def test_nested_select_evidence_is_rejected_before_model_call(self) -> None:
        (self.generation / 'execution_trace.json').write_text(
            json.dumps({'select_metrics': {'mean_abs_ic': 1}}), encoding='utf-8')
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(proposer_command, 'chat') as chat:
            with self.assertRaises(Exception):
                proposer_command.proposer_prompt()
        chat.assert_not_called()

    def test_reflection_budget_is_persistent(self) -> None:
        usage_path = self.work / 'reflection_usage.json'
        reflection = proposer_command.BudgetedReflection(
            usage_path, {'max_model_calls': 1, 'max_input_tokens': 100,
                         'max_output_tokens': 100}, 'fixed-test-model')
        usage = {'available': True, 'prompt_tokens': 10, 'completion_tokens': 5}
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(proposer_command, 'chat', return_value='```\nnew guidance\n```') as chat, \
                mock.patch.object(proposer_command, 'get_last_usage', return_value=usage):
            self.assertIn('new guidance', reflection('reflect'))
            restored = proposer_command.BudgetedReflection(
                usage_path, {'max_model_calls': 1, 'max_input_tokens': 100,
                             'max_output_tokens': 100}, 'fixed-test-model')
            self.assertEqual(restored.get_usage()['model_calls'], 1)
            with self.assertRaises(RuntimeError):
                restored('reflect again')
            with self.assertRaises(RuntimeError):
                restored('must not call the model again')
            self.assertEqual(chat.call_count, 1)
        persisted = json.loads(usage_path.read_text())
        self.assertEqual(persisted['usage']['model_calls'], 1)
        self.assertEqual(persisted['usage']['output_tokens'], 5)

    def test_claude_cli_disables_tools_and_exports_usage(self) -> None:
        edits = [{'id': 'e1', 'kind': 'INV', 'component': 'operators',
                  'op': 'append', 'value': 'rank', 'intent': 'test'}]
        payload = {
            'is_error': False, 'structured_output': {'edits': edits}, 'total_cost_usd': 0.01,
            'duration_ms': 1,
            'modelUsage': {'fixed-test-model': {
                'inputTokens': 10, 'cacheCreationInputTokens': 2,
                'cacheReadInputTokens': 3, 'outputTokens': 5}},
        }
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr='')
        with mock.patch.dict(os.environ, {**self.environment,
                                          'AUTO_RESEARCH_CLAUDE_CLI_MODEL': 'fixed-test-model'}, clear=False), \
                mock.patch.object(claude_proposer_command.subprocess, 'run', return_value=completed) as run:
            self.assertEqual(claude_proposer_command.run_proposer(), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertEqual(command[command.index('--model') + 1], 'fixed-test-model')
        self.assertIn('--no-session-persistence', command)
        usage = json.loads((self.generation / 'model_usage.json').read_text())
        self.assertEqual(usage['input_tokens'], 15)
        self.assertEqual(usage['output_tokens'], 5)

    def test_claude_cli_malformed_content_preserves_actual_usage(self) -> None:
        payload = {'is_error': False, 'result': 'not-json',
                   'modelUsage': {'fixed-test-model': {'inputTokens': 10, 'outputTokens': 5}}}
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr='')
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(claude_proposer_command.subprocess, 'run', return_value=completed):
            with self.assertRaises(Exception):
                claude_proposer_command.run_proposer()
        usage = json.loads((self.generation / 'model_usage.json').read_text())
        self.assertEqual(usage['calls'], 1)
        self.assertEqual(usage['output_tokens'], 5)

    def test_claude_cli_over_limit_preserves_actual_usage(self) -> None:
        payload = {'is_error': False, 'structured_output': {'edits': []},
                   'modelUsage': {'fixed-test-model': {
                       'inputTokens': 10, 'outputTokens': 5000}}}
        completed = mock.Mock(returncode=0, stdout=json.dumps(payload), stderr='')
        with mock.patch.dict(os.environ, self.environment, clear=False), \
                mock.patch.object(claude_proposer_command.subprocess, 'run', return_value=completed):
            with self.assertRaises(RuntimeError):
                claude_proposer_command.run_proposer()
        usage = json.loads((self.generation / 'model_usage.json').read_text())
        self.assertEqual(usage['output_tokens'], 5000)
        self.assertTrue((self.generation / 'per_call_budget_exceeded.json').is_file())
        self.assertFalse((self.generation / 'edits.json').exists())

    def test_claude_cli_reflection_budget_blocks_retries(self) -> None:
        usage_path = self.work / 'claude_reflection_usage.json'
        budget = {'max_model_calls': 1, 'max_input_tokens': 100,
                  'max_output_tokens': 100}
        response = ('new guidance', {'input_tokens': 10, 'output_tokens': 5},
                    {'output_limit_exceeded': False})
        with mock.patch.object(claude_proposer_command, 'call_cli',
                               return_value=response) as call:
            reflection = claude_proposer_command.ClaudeCLIReflection(
                usage_path, budget, 'fixed-test-model')
            self.assertEqual(reflection('reflect'), 'new guidance')
            restored = claude_proposer_command.ClaudeCLIReflection(
                usage_path, budget, 'fixed-test-model')
            with self.assertRaises(RuntimeError):
                restored('reflect again')
            with self.assertRaises(RuntimeError):
                restored('must not call the model again')
            self.assertEqual(call.call_count, 1)
        persisted = json.loads(usage_path.read_text())
        self.assertEqual(persisted['usage']['model_calls'], 1)
        self.assertEqual(persisted['usage']['output_tokens'], 5)

    def test_claude_cli_uses_default_model_without_override(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            command = claude_proposer_command.cli_command('prompt', 'kimi/k3.1-pro[1m]', None)
        self.assertNotIn('--model', command)

    def test_cached_llm_response_restores_token_usage(self) -> None:
        messages = [{'role': 'user', 'content': 'cached test'}]
        payload = {'model': 'fixed-test-model', 'messages': messages, 'temperature': 0.2,
                   'max_tokens': 2400, 'chat_template_kwargs': {'enable_thinking': False},
                   'reasoning_effort': 'none'}
        cache_path = self.work / f'{llm._key(payload)}.json'
        cache_path.write_text(json.dumps({
            'content': 'cached answer', 'usage': {'prompt_tokens': 12, 'completion_tokens': 7}}),
            encoding='utf-8')
        stats = {'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
        with mock.patch.multiple(llm, CACHE=self.work, PROVIDER='local',
                                 MODEL='fixed-test-model', STATS=stats), \
                mock.patch.dict(os.environ, {'AUTO_RESEARCH_LLM_THINKING': '0',
                                             'AUTO_RESEARCH_LLM_REASONING_EFFORT': 'none'}, clear=False):
            self.assertEqual(llm.chat(messages), 'cached answer')
            usage = llm.get_last_usage()
        self.assertTrue(usage['available'])
        self.assertTrue(usage['cached'])
        self.assertEqual(usage['api_calls'], 0)
        self.assertEqual(usage['prompt_tokens'], 12)
        self.assertEqual(usage['completion_tokens'], 7)
        self.assertEqual(stats['api_calls'], 0)


if __name__ == '__main__':
    unittest.main()
