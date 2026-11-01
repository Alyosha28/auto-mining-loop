#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
sys.path.insert(0, str(SCRIPTS))
import evo_harness
import evo_probe
import run_evo_step
import run_route_step as router

SCRIPT = SCRIPTS / 'run_route_step.py'
QUESTION = 'Can route choice improve research allocation under a fixed evaluator?'


def example_trace() -> dict:
    return {
        'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1',
        'source_run': 'R3_TEST',
        'scope': 'Prior probe evidence only.',
        'search_policy': {'operators': ['residual_size'], 'skills': {'crossover_pairs': 0}},
        'reference_probe_metrics': {'mean_abs_ic': 0.04, 'novelty': 0.125},
        'next_question': 'old framing',
        'proposals': [
            {'edit': {'id': 'old_edit', 'kind': 'INV', 'component': 'operators',
                      'op': 'append', 'value': 'rank', 'intent': 'old interpretation'},
             'probe_metrics': {'mean_abs_ic': 0.045},
             'delta': {'delta_mean_abs_ic': 0.005, 'delta_novelty': -0.025},
             'support_reason': None, 'verdict': 'dropped'},
        ],
        'kept_edits': [],
    }


def example_edits() -> list[dict]:
    return [
        {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
         'value': 'rank', 'intent': 'test cross-sectional normalization'},
        {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
         'op': 'set', 'value': 1, 'intent': 'keep mutation depth fixed for this control'},
    ]


def route_payload(action='EXPLOIT') -> dict:
    payload = {'schema': router.ROUTE_SCHEMA, 'action': action,
               'rationale': 'use the observed probe contrast to choose the next bounded action',
               'evidence_refs': ['R3_TEST'],
               'falsification': 'the frozen comparison does not support this action',
               'edits': example_edits()}
    if action == 'REPAIR':
        payload['target'] = 'candidate generation'
    if action in ('FORK', 'REFRAME'):
        payload.update(next_question='test a different research framing',
                       branch_from='current', edits=[])
    if action == 'STOP':
        payload['edits'] = []
        payload.pop('falsification')
    return payload


def without_runtime(payload):
    if isinstance(payload, dict):
        return {key: without_runtime(value) for key, value in payload.items()
                if key != 'runtime_seconds'}
    if isinstance(payload, list):
        return [without_runtime(value) for value in payload]
    return payload


class RouteTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.harness = evo_harness.default_harness()
        self.harness['protocol']['budget_candidates'] = 12
        self.harness['protocol']['dev_split'] = 'seeded-year-blocks'
        self.harness['memory']['experience_dir'] = str(self.work / 'experience')
        self.harness_path = self.work / 'harness.json'
        self.harness_path.write_text(json.dumps(self.harness), encoding='utf-8')
        self.trace_path = self.work / 'trace.json'
        self.trace_path.write_text(json.dumps(example_trace()), encoding='utf-8')

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def args(self, name: str, *, mode='route-choice', prepare=True,
             edits=False, route=False, question=QUESTION) -> argparse.Namespace:
        return argparse.Namespace(
            output=self.work / name, run_id=name, mode=mode,
            harness=self.harness_path, trace_file=self.trace_path,
            route_history=None, research_question=question, model_id='secondary-test',
            prompt_feedback='diagnostic', shared_memory=None, champion_harness=None,
            seed=11, budget=0, dev_split='seeded-year-blocks', features=None,
            k=1, coverage_log_only=True, demo=True, execution_timeout_seconds=60,
            prepare=prepare,
            edits=(self.work / name / 'generation/edits.json') if edits else None,
            route=(self.work / name / 'generation/route.json') if route else None,
        )

    def prepare(self, name: str, *, mode='route-choice') -> Path:
        output = self.work / name
        self.assertEqual(router.run(self.args(name, mode=mode)), 0)
        self.assertTrue((output / 'generation/prompt.txt').is_file())
        return output


class RouteContractTests(RouteTestCase):
    def test_all_actions_and_validation_fail_closed(self) -> None:
        for action in router.ACTIONS:
            with self.subTest(action=action):
                payload = route_payload(action)
                validated = router.validate_route(
                    payload, current_question=QUESTION, allowed_evidence={'R3_TEST'})
                self.assertEqual(validated['action'], action)
        invalid = route_payload('STOP')
        invalid['edits'] = example_edits()
        with self.assertRaises(ValueError):
            router.validate_route(invalid, current_question=QUESTION,
                                  allowed_evidence={'R3_TEST'})
        invalid = route_payload('REFRAME')
        invalid['next_question'] = QUESTION
        with self.assertRaises(ValueError):
            router.validate_route(invalid, current_question=QUESTION,
                                  allowed_evidence={'R3_TEST'})
        invalid = route_payload('REPAIR')
        invalid.pop('target')
        with self.assertRaises(ValueError):
            router.validate_route(invalid, current_question=QUESTION,
                                  allowed_evidence={'R3_TEST'})
        invalid = route_payload()
        invalid['evidence_refs'] = ['unsupplied_evidence']
        with self.assertRaises(ValueError):
            router.validate_route(invalid, current_question=QUESTION,
                                  allowed_evidence={'R3_TEST'})
        invalid['action'] = 'UNKNOWN'
        with self.assertRaises(ValueError):
            router.validate_route(invalid, current_question=QUESTION,
                                  allowed_evidence={'R3_TEST'})

    def test_neutral_trace_and_history_reject_holdout(self) -> None:
        trace = run_evo_step._load_execution_trace(self.trace_path)
        neutral = router.neutral_trace(trace)
        self.assertNotIn('next_question', neutral)
        self.assertNotIn('support_reason', neutral['proposals'][0])
        self.assertNotIn('intent', neutral['proposals'][0]['edit'])
        self.assertEqual(neutral['proposals'][0]['delta'], trace['proposals'][0]['delta'])
        for key in ('select_metrics', 'dev_holdout', 'child_result', 'childSelect', 'devHoldout'):
            invalid = copy.deepcopy(example_trace())
            invalid['proposals'][0][key] = {'mean_abs_ic': 10}
            self.trace_path.write_text(json.dumps(invalid), encoding='utf-8')
            with self.subTest(key=key), self.assertRaises(evo_probe.GuardrailError):
                run_evo_step._load_execution_trace(self.trace_path)
        history_path = self.work / 'history.json'
        history_path.write_text(json.dumps({'schema': router.HISTORY_SCHEMA,
                                            'entries': [{'run_id': 'bad', 'action': 'STOP',
                                                         'status': 'STOPPED', 'rationale': 'bad',
                                                         'evidence_refs': [], 'select_value': 1}]}),
                                encoding='utf-8')
        with self.assertRaises(evo_probe.GuardrailError):
            router.load_history(history_path)

    def test_prepare_is_panel_free_and_shared_by_both_modes(self) -> None:
        failure = AssertionError('prepare must not load a panel')
        with mock.patch.object(evo_probe, 'load_panel', side_effect=failure), \
                mock.patch.object(run_evo_step, 'load_panel', side_effect=failure), \
                mock.patch.object(evo_probe, '_panel_files', side_effect=failure):
            local = self.prepare('local', mode='local-only')
            routed = self.prepare('routed')
        self.assertEqual((local / 'generation/execution_trace.json').read_bytes(),
                         (routed / 'generation/execution_trace.json').read_bytes())
        for output in (local, routed):
            state = json.loads((output / 'generation/research_state.json').read_text())
            self.assertEqual(state['current_harness'], self.harness)
            self.assertFalse((output / 'child').exists())
            self.assertFalse((output / 'cache').exists())
            manifest = json.loads((output / 'run_manifest.json').read_text())
            self.assertTrue(manifest['router_market_data_read'] is False)
            for template in (output / 'generation').glob('*.template.json'):
                self.assertNotIn('"rank"', template.read_text())
        self.assertNotIn('Choose exactly one route action',
                         (local / 'generation/prompt.txt').read_text())
        self.assertIn('Choose exactly one route action',
                      (routed / 'generation/prompt.txt').read_text())

    def test_interrupted_prepare_completes_identical_partial_artifacts(self) -> None:
        complete = self.prepare('complete')
        partial = self.work / 'partial_prepare'
        (partial / 'generation').mkdir(parents=True)
        (partial / 'generation/research_state.json').write_bytes(
            (complete / 'generation/research_state.json').read_bytes())
        (partial / 'generation/execution_trace.json').write_text('', encoding='utf-8')
        self.assertEqual(router.run(self.args('partial_prepare')), 0)
        self.assertTrue((partial / 'run_manifest.json').is_file())
        self.assertEqual((partial / 'generation/prompt.txt').read_bytes(),
                         (complete / 'generation/prompt.txt').read_bytes())
        (partial / 'generation/research_state.json').write_text('{}', encoding='utf-8')
        (partial / 'run_manifest.json').unlink()
        with self.assertRaises(evo_probe.GuardrailError):
            router.run(self.args('partial_prepare'))

    def test_atomic_prompt_and_nested_trace_sanitization(self) -> None:
        with mock.patch.dict(os.environ, {'AUTO_RESEARCH_ATOMIC_PROPOSAL': '1'}):
            output = self.prepare('atomic', mode='local-only')
        manifest = json.loads((output / 'run_manifest.json').read_text())
        self.assertTrue(manifest['atomic_proposals'])
        template = json.loads((output / 'generation/edits.json.template.json').read_text())
        self.assertEqual(template['schema'], 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1')
        self.assertIn('one atomic candidate', (output / 'generation/prompt.txt').read_text())
        trace = example_trace()
        trace['proposals'][0]['edit'] = {
            'id': 'atomic', 'component': 'atomic', 'intent': 'outer prose',
            'value': [{'id': 'nested', 'component': 'operators', 'value': 'rank',
                       'intent': 'nested prose'}],
        }
        neutral = router.neutral_trace(trace)
        edit = neutral['proposals'][0]['edit']
        self.assertNotIn('intent', edit)
        self.assertNotIn('intent', edit['value'][0])

    def test_parent_and_diagnostic_share_memory_but_not_candidate_trace(self) -> None:
        memory_path = self.work / 'memory.json'
        memory_path.write_text(json.dumps({
            'schema': router.MEMORY_SCHEMA,
            'entries': [{'source_run': 'prior_task', 'task': 'other', 'status': 'COMPLETED',
                         'scope': 'development probe on another task',
                         'probe_metrics': {'mean_abs_ic': 0.04},
                         'edits': [{'component': 'operators', 'op': 'append', 'value': 'rank'}]}],
        }), encoding='utf-8')
        outputs = {}
        for feedback in ('parent', 'diagnostic'):
            args = self.args(feedback, mode='local-only')
            args.prompt_feedback = feedback
            args.shared_memory = memory_path
            self.assertEqual(router.run(args), 0)
            outputs[feedback] = self.work / feedback
        parent = json.loads((outputs['parent'] / 'generation/execution_trace.json').read_text())
        diagnostic = json.loads((outputs['diagnostic'] / 'generation/execution_trace.json').read_text())
        self.assertEqual(parent['proposals'], [])
        self.assertEqual(len(diagnostic['proposals']), 1)
        self.assertEqual((outputs['parent'] / 'generation/shared_research_memory.json').read_bytes(),
                         (outputs['diagnostic'] / 'generation/shared_research_memory.json').read_bytes())
        invalid = json.loads(memory_path.read_text())
        invalid['entries'][0]['select_metrics'] = {'mean_abs_ic': 9}
        memory_path.write_text(json.dumps(invalid), encoding='utf-8')
        with self.assertRaises(evo_probe.GuardrailError):
            router.load_shared_memory(memory_path)

    def test_resume_identity_and_invalid_proposal_are_preserved(self) -> None:
        output = self.work / 'resume'
        base = [sys.executable, str(SCRIPT), '--output', str(output), '--run-id', 'resume',
                '--mode', 'route-choice', '--demo', '--harness', str(self.harness_path),
                '--trace-file', str(self.trace_path), '--research-question', QUESTION,
                '--seed', '11', '--dev-split', 'seeded-year-blocks', '--coverage-log-only']
        prepared = subprocess.run(base + ['--prepare'], capture_output=True, text=True, timeout=30)
        self.assertEqual(prepared.returncode, 0, prepared.stderr)
        route_path = output / 'generation/route.json'
        route_path.write_text(json.dumps(route_payload()), encoding='utf-8')
        wrong = subprocess.run(base + ['--route', str(route_path), '--research-question', 'changed'],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(wrong.returncode, 2)
        self.assertIn('research_question', wrong.stderr)
        self.assertFalse((output / 'result.json').exists())
        neutral_path = output / 'generation/execution_trace.json'
        original_neutral = neutral_path.read_bytes()
        neutral = json.loads(original_neutral)
        neutral['proposals'][0]['delta']['delta_mean_abs_ic'] = 999
        neutral_path.write_text(json.dumps(neutral), encoding='utf-8')
        tampered = subprocess.run(base + ['--route', str(route_path)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(tampered.returncode, 2)
        self.assertIn('prepared neutral trace changed', tampered.stderr)
        neutral_path.write_bytes(original_neutral)
        invalid = route_payload()
        invalid['action'] = 'UNKNOWN'
        invalid_text = json.dumps(invalid, indent=1) + '\n'
        route_path.write_text(invalid_text, encoding='utf-8')
        rejected = subprocess.run(base + ['--route', str(route_path)],
                                  capture_output=True, text=True, timeout=30)
        self.assertEqual(rejected.returncode, 2, rejected.stderr)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['status'], 'INVALID_PROPOSAL')
        self.assertFalse(result['candidate_evaluation_started'])
        self.assertFalse((output / 'child').exists())
        self.assertEqual((output / 'rejected_proposal.json').read_text(), invalid_text)
        manifest = json.loads((output / 'run_manifest.json').read_text())
        self.assertEqual(manifest['status'], 'INVALID_PROPOSAL')
        self.assertIn('--route', manifest['resume_command'])
        route_path.write_text(json.dumps(route_payload()), encoding='utf-8')
        retry = subprocess.run(base + ['--route', str(route_path)],
                               capture_output=True, text=True, timeout=30)
        self.assertEqual(retry.returncode, 3)
        self.assertEqual((output / 'rejected_proposal.json').read_text(), invalid_text)

    def test_handoff_history_accumulates_in_the_next_run(self) -> None:
        first = self.prepare('first')
        (first / 'generation/route.json').write_text(json.dumps(route_payload('REFRAME')),
                                                      encoding='utf-8')
        self.assertEqual(router.run(self.args('first', prepare=False, route=True)), 0)
        state = json.loads((first / 'next_state.json').read_text())
        following = self.args('second', question=state['research_question'])
        following.harness = Path(state['harness'])
        following.trace_file = Path(state['trace_file'])
        following.route_history = Path(state['route_history'])
        following.mode = state['mode']
        following.model_id = state['model_id']
        self.assertEqual(router.run(following), 0)
        stop = route_payload('STOP')
        stop['evidence_refs'] = ['first']
        second = self.work / 'second'
        (second / 'generation/route.json').write_text(json.dumps(stop), encoding='utf-8')
        following.prepare = False
        following.route = second / 'generation/route.json'
        self.assertEqual(router.run(following), 0)
        history = router.load_history(second / 'route_history.json')
        self.assertEqual([entry['action'] for entry in history['entries']], ['REFRAME', 'STOP'])
        self.assertEqual(history['entries'][0]['run_id'], 'first')
        self.assertTrue((second / 'generation/route_history_input.json').is_file())

    def test_partial_run_and_bad_harness_fail_cleanly(self) -> None:
        partial = self.prepare('partial')
        (partial / 'child').mkdir()
        (partial / 'generation/route.json').write_text(json.dumps(route_payload()), encoding='utf-8')
        self.assertEqual(router.run(self.args('partial', prepare=False, route=True)), 3)
        self.assertFalse((partial / 'result.json').exists())
        invalid = copy.deepcopy(self.harness)
        del invalid['protocol']['dev_start']
        self.harness_path.write_text(json.dumps(invalid), encoding='utf-8')
        with self.assertRaises(evo_probe.GuardrailError):
            router.run(self.args('missing_window'))
        invalid = copy.deepcopy(self.harness)
        invalid['seed_pool'] = [{'name': 'missing_feature'}]
        self.harness_path.write_text(json.dumps(invalid), encoding='utf-8')
        with self.assertRaises(evo_probe.GuardrailError):
            router.run(self.args('missing_feature'))

    def test_stop_and_reframe_do_not_execute_or_change_base(self) -> None:
        for action, expected in [('STOP', 'STOPPED'), ('REFRAME', 'HANDOFF')]:
            name = action.lower()
            output = self.prepare(name)
            payload = route_payload(action)
            (output / 'generation/route.json').write_text(json.dumps(payload), encoding='utf-8')
            with mock.patch.object(router.subprocess, 'run',
                                   side_effect=AssertionError('non-local route must not execute')):
                code = router.run(self.args(name, prepare=False, route=True))
            self.assertEqual(code, 0)
            result = json.loads((output / 'result.json').read_text())
            self.assertEqual(result['status'], expected)
            self.assertFalse((output / 'child').exists())
            self.assertFalse(result['execution']['candidate_evaluation_started'])
            history = router.load_history(output / 'route_history.json')
            self.assertEqual(history['entries'][-1]['action'], action)
            self.assertTrue((output / 'generation/route_history_input.json').is_file())
            manifest = json.loads((output / 'run_manifest.json').read_text())
            self.assertIn('proposal_sha256', manifest)
            self.assertIn('resume_command', manifest)
            if action == 'STOP':
                self.assertIsNone(result['next_state'])
            else:
                state = json.loads((output / 'next_state.json').read_text())
                self.assertEqual(state['research_question'], payload['next_question'])
                self.assertEqual(state['harness'], str(self.harness_path))
                self.assertEqual(state['status'], 'HANDOFF')
                self.assertEqual(state['model_id'], 'secondary-test')
                self.assertEqual(state['mode'], 'route-choice')
                self.assertTrue(state['requires_new_run'])

    def test_child_timeout_preserves_failure(self) -> None:
        output = self.prepare('timeout')
        (output / 'generation/route.json').write_text(json.dumps(route_payload()), encoding='utf-8')
        error = subprocess.TimeoutExpired('child', 60, output=b'partial', stderr=b'timeout')
        with mock.patch.object(router.subprocess, 'run', side_effect=error):
            code = router.run(self.args('timeout', prepare=False, route=True))
        self.assertEqual(code, 2)
        self.assertEqual((output / 'child_stdout.log').read_text(), 'partial')
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['status'], 'FAILED')
        history = router.load_history(output / 'route_history.json')
        self.assertEqual(history['entries'][-1]['status'], 'FAILED')


class RouteExecutionTests(RouteTestCase):
    def test_local_and_route_exploit_delegate_identically(self) -> None:
        experience = self.work / 'experience'
        experience.mkdir()
        index = experience / 'index.json'
        index.write_text('[{"id": "sentinel"}]\n', encoding='utf-8')
        original_index = index.read_bytes()
        local = self.prepare('local_execution', mode='local-only')
        routed = self.prepare('route_execution')
        (local / 'generation/edits.json').write_text(json.dumps(example_edits()), encoding='utf-8')
        (routed / 'generation/route.json').write_text(json.dumps(route_payload()), encoding='utf-8')
        self.assertEqual(router.run(self.args('local_execution', mode='local-only',
                                              prepare=False, edits=True)), 0)
        self.assertEqual(router.run(self.args('route_execution', prepare=False, route=True)), 0)
        for output in (local, routed):
            result = json.loads((output / 'result.json').read_text())
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertFalse(result['execution']['reflection_write'])
            self.assertTrue(result['execution']['experience_index_unchanged'])
            self.assertEqual(result['execution']['lessons_used'], [])
            child = output / 'child/step'
            child_manifest = json.loads((child / 'run_manifest.json').read_text())
            self.assertFalse(child_manifest['reflection_write'])
            child_result = json.loads((child / 'result.json').read_text())
            child_record = json.loads((child / 'step_record.json').read_text())
            self.assertTrue(child_record['probe_only'])
            self.assertIsNone(child_record['adopted_select'])
            self.assertIsNone(child_result['select'])
            self.assertFalse(child_result['select_evaluated'])
            self.assertFalse(child_result['score_window_read'])
            self.assertFalse(child_result['independent_real_data_oos'])
            self.assertLessEqual(child_result['max_date_read'], '2020-11-30')
            run_evo_step._load_execution_trace(child / 'generation/next_execution_trace.json')
            state = json.loads((output / 'next_state.json').read_text())
            self.assertEqual(state['status'], 'READY')
            self.assertEqual(state['model_id'], 'secondary-test')
            router.load_history(output / 'route_history.json')
        self.assertEqual(index.read_bytes(), original_index)
        for relative in ['probe/reference.json', 'probe/candidates/add_rank.json',
                         'probe/candidates/fixed_depth.json']:
            left = json.loads((local / 'child/step' / relative).read_text())
            right = json.loads((routed / 'child/step' / relative).read_text())
            self.assertEqual(without_runtime(left), without_runtime(right), relative)
        local_result = json.loads((local / 'child/step/result.json').read_text())
        routed_result = json.loads((routed / 'child/step/result.json').read_text())
        self.assertEqual(local_result['primary'], routed_result['primary'])
        self.assertEqual(local_result['base_harness_hash'], routed_result['base_harness_hash'])

    def test_delegate_preserves_a_narrower_dev_window(self) -> None:
        dates = sorted(evo_probe.synthetic_panel(seed=11)['date'].unique())
        start, end = str(dates[5])[:10], str(dates[-6])[:10]
        self.harness['protocol']['dev_start'] = start
        self.harness['protocol']['dev_end'] = end
        self.harness_path.write_text(json.dumps(self.harness), encoding='utf-8')
        output = self.prepare('narrow_window')
        (output / 'generation/route.json').write_text(json.dumps(route_payload()), encoding='utf-8')
        self.assertEqual(router.run(self.args('narrow_window', prepare=False, route=True)), 0)
        child = output / 'child/step'
        manifest = json.loads((child / 'run_manifest.json').read_text())
        self.assertEqual(manifest['dev_window'], [start, end])
        record = json.loads((child / 'step_record.json').read_text())
        self.assertEqual(record['base_harness']['protocol']['dev_start'], start)
        self.assertEqual(record['base_harness']['protocol']['dev_end'], end)
        result = json.loads((child / 'result.json').read_text())
        self.assertFalse(result['score_window_read'])
        self.assertLessEqual(result['max_date_read'], end)


if __name__ == '__main__':
    unittest.main()
