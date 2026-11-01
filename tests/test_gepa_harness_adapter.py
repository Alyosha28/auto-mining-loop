#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
import sys
sys.path.insert(0, str(SCRIPTS))
import evo_harness
import evo_probe
from gepa import optimize
import run_gepa_optimization
import confirm_gepa_guidance
from gepa_harness_adapter import HarnessGEPAAdapter, INVALID_SCORE

PROPOSER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

generation = Path(os.environ['AUTO_RESEARCH_GENERATION'])
usage = {'schema': 'AUTO-RESEARCH-MODEL-USAGE-1',
         'model_id': os.environ['AUTO_RESEARCH_MODEL_ID'],
         'calls': 1, 'input_tokens': 10, 'output_tokens': 5}
Path(os.environ['AUTO_RESEARCH_USAGE']).write_text(json.dumps(usage))
edits = [
    {'id': 'add_zscore', 'kind': 'INV', 'component': 'operators', 'op': 'append',
     'value': 'zscore', 'intent': 'test common-scale normalization'},
    {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
     'op': 'set', 'value': 1, 'intent': 'keep mutation depth fixed'}]
Path(os.environ['AUTO_RESEARCH_PROPOSAL']).write_text(
    json.dumps(edits[:1] if os.environ.get('AUTO_RESEARCH_TEST_INVALID_EDITS') else edits))
sys.exit(int(os.environ.get('AUTO_RESEARCH_TEST_PROPOSER_EXIT', '0')))
'''


def contract_payload() -> dict:
    return {
        'schema': 'AUTO-RESEARCH-LOOP-CONTRACT-1',
        'budget': {'max_decisions': 2, 'max_candidate_budget': 12, 'max_model_calls': 2,
                   'max_input_tokens': 100, 'max_output_tokens': 100,
                   'max_no_improvement': 1, 'max_debug_depth': 0},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': -1.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }


class GEPAAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.work = Path(self.temporary.name)
        self.harness = evo_harness.default_harness()
        self.harness['protocol']['budget_candidates'] = 6
        self.harness['protocol']['dev_split'] = 'seeded-year-blocks'
        self.harness['memory']['experience_dir'] = str(self.work / 'experience')
        self.harness_path = self.work / 'harness.json'
        self.harness_path.write_text(json.dumps(self.harness), encoding='utf-8')
        self.trace_path = self.work / 'trace.json'
        self.trace_path.write_text(json.dumps({
            'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1', 'source_run': 'GEPA_TEST',
            'scope': 'Prior probe evidence only.', 'reference_probe_metrics': {'mean_abs_ic': 0.04},
            'proposals': [], 'kept_edits': []}), encoding='utf-8')
        self.contract = self.work / 'contract.json'
        self.contract.write_text(json.dumps(contract_payload()), encoding='utf-8')
        self.proposer = self.work / 'proposer.py'
        self.proposer.write_text(PROPOSER, encoding='utf-8')
        self.proposer.chmod(0o755)
        self.data = {
            'data_id': 'dev11', 'harness': str(self.harness_path),
            'trace_file': str(self.trace_path), 'contract': str(self.contract),
            'research_question': 'Can reflective guidance improve bounded proposal allocation?',
            'seed': 11, 'budget': 6, 'dev_split': 'seeded-year-blocks',
            'features': None, 'demo': True, 'seed_panel': None,
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def adapter(self, **budget) -> HarnessGEPAAdapter:
        limits = {'max_model_calls': 4, 'max_input_tokens': 100, 'max_output_tokens': 100}
        limits.update(budget)
        return HarnessGEPAAdapter(self.work / 'gepa', self.proposer, 'fixed-test-model', limits,
                                  proposer_timeout_seconds=30, execution_timeout_seconds=60,
                                  comparison_timeout_seconds=180)

    def test_joint_probe_scoring_and_safe_reflection(self) -> None:
        adapter = self.adapter()
        first = {'proposer_guidance': 'Prefer a concrete budget diagnosis.'}
        second = {'proposer_guidance': 'Avoid repeated inert edits and explain the mechanism.'}
        batches = adapter.batch_evaluate([(first, [self.data]), (second, [self.data])],
                                         capture_traces=True)
        self.assertEqual(len(batches), 2)
        self.assertEqual(adapter.usage['model_calls'], 2)
        comparisons = list((self.work / 'gepa/comparisons/dev11').iterdir())
        self.assertEqual(len(comparisons), 1)
        comparison = json.loads((comparisons[0] / 'result.json').read_text())
        self.assertEqual(comparison['primary_evaluation'], 'probe')
        self.assertEqual(len(comparison['arms']), 3)
        self.assertFalse((comparisons[0] / 'select').exists())
        hashes = {arm['probe_metrics']['common_support_sha256'] for arm in comparison['arms'].values()}
        self.assertEqual(len(hashes), 1)
        for batch in batches:
            self.assertEqual(len(batch.scores), 1)
            self.assertEqual(len(batch.trajectories), 1)
            self.assertEqual(len(batch.objective_scores), 1)
        dataset = adapter.make_reflective_dataset(first, batches[0], ['proposer_guidance'])
        self.assertEqual(len(dataset['proposer_guidance']), 1)
        self.assertIsInstance(dataset['proposer_guidance'][0]['Feedback'], str)
        self.assertNotIn('select', json.dumps(dataset).lower())
        candidate_dir = self.work / 'gepa/generations/dev11'
        prompts = [path.read_text() for path in candidate_dir.glob('*/generation/prompt.txt')]
        self.assertTrue(any(first['proposer_guidance'] in prompt for prompt in prompts))
        self.assertTrue(any(second['proposer_guidance'] in prompt for prompt in prompts))
        adapter.evaluate([self.data], first, capture_traces=False)
        self.assertEqual(adapter.usage['model_calls'], 2)

    def test_gepa_engine_runs_with_local_reflection(self) -> None:
        validation = dict(self.data, data_id='dev29', seed=29)
        config = {
            'schema': run_gepa_optimization.CONFIG_SCHEMA,
            'seed_candidate': {'proposer_guidance': 'Use the supplied probe diagnosis.'},
            'trainset': [self.data], 'valset': [validation], 'proposer': str(self.proposer),
            'model_id': 'fixed-test-model', 'proposer_backend': 'openai-compatible',
            'proposer_budget': {'max_model_calls': 8, 'max_input_tokens': 100,
                                'max_output_tokens': 100},
            'reflection_budget': {'max_model_calls': 4, 'max_input_tokens': 100,
                                  'max_output_tokens': 100},
            'reflection_minibatch_size': 1, 'max_metric_calls': 8,
            'seed': 1, 'proposer_timeout_seconds': 30,
            'execution_timeout_seconds': 60, 'comparison_timeout_seconds': 180,
        }
        config_path = self.work / 'gepa_config.json'
        config_path.write_text(json.dumps(config), encoding='utf-8')
        output = self.work / 'cli_gepa'

        def reflection(_prompt: str) -> str:
            return '```\nFocus on one diagnosed allocation mechanism and its falsification.\n```'

        def offline_optimize(**options):
            options['reflection_lm'] = reflection
            options['acceptance_criterion'] = 'improvement_or_equal'
            return optimize(**options)

        args = argparse.Namespace(config=config_path, output=output,
                                  reflection_lm='fixed-test-model')
        with mock.patch.object(run_gepa_optimization, 'optimize', side_effect=offline_optimize):
            self.assertEqual(run_gepa_optimization.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertGreaterEqual(len(result['gepa_result']['candidates']), 2)
        self.assertIn('proposer_guidance', result['best_candidate'])
        self.assertGreaterEqual(result['adapter_usage']['usage']['model_calls'], 2)
        self.assertEqual(result['reflection_usage']['model_calls'], 0)
        self.assertFalse((output / 'engine/select').exists())
        confirmation_data = dict(self.data, data_id='confirm43', seed=43,
                                 research_question='Can frozen guidance transfer to a fresh split?')
        confirmation_path = self.work / 'confirmation_data.json'
        confirmation_path.write_text(json.dumps(confirmation_data), encoding='utf-8')
        confirmation_output = self.work / 'confirmation'
        confirmation_args = argparse.Namespace(
            gepa_run=output, data=confirmation_path, output=confirmation_output)
        self.assertEqual(confirm_gepa_guidance.run(confirmation_args), 0)
        confirmation = json.loads((confirmation_output / 'result.json').read_text())
        self.assertEqual(confirmation['fresh_seed'], 43)
        self.assertFalse(confirmation['text_changed'])
        comparison = json.loads((confirmation_output / 'comparison/result.json').read_text())
        self.assertEqual(len(comparison['arms']), 3)
        self.assertTrue(comparison['common_support_verified'])

    def test_invalid_candidate_is_preserved_with_failure_score(self) -> None:
        adapter = self.adapter()
        candidate = {'proposer_guidance': 'Use an invalid proposal to test recovery.'}
        with mock.patch.dict(os.environ, {'AUTO_RESEARCH_TEST_INVALID_EDITS': '1'}, clear=False):
            batch = adapter.evaluate([self.data], candidate, capture_traces=True)
        self.assertEqual(batch.scores, [INVALID_SCORE])
        trajectory = batch.trajectories[0]
        self.assertFalse(trajectory['comparison']['metric_available'])
        self.assertEqual(trajectory['comparison']['status'], 'INVALID_PROPOSAL')
        self.assertIsNone(trajectory['adopted_probe_metrics'])
        dataset = adapter.make_reflective_dataset(candidate, batch, ['proposer_guidance'])
        self.assertIn('INVALID_PROPOSAL', dataset['proposer_guidance'][0]['Feedback'])

    def test_failed_proposer_usage_is_charged(self) -> None:
        adapter = self.adapter()
        candidate = {'proposer_guidance': 'Use the frozen evidence boundary.'}
        with mock.patch.dict(os.environ, {'AUTO_RESEARCH_TEST_PROPOSER_EXIT': '2'}, clear=False):
            with self.assertRaises(evo_probe.GuardrailError):
                adapter.evaluate([self.data], candidate, capture_traces=False)
        self.assertEqual(adapter.usage['model_calls'], 1)
        self.assertEqual(adapter.usage['output_tokens'], 5)
        persisted = json.loads((self.work / 'gepa/adapter_usage.json').read_text())
        self.assertEqual(persisted['usage']['model_calls'], 1)

    def test_proposer_budget_stops_before_candidate_execution(self) -> None:
        adapter = self.adapter(max_output_tokens=4)
        candidate = {'proposer_guidance': 'Use the frozen evidence boundary.'}
        with self.assertRaises(evo_probe.GuardrailError):
            adapter.evaluate([self.data], candidate, capture_traces=False)
        self.assertEqual(adapter.usage['output_tokens'], 5)
        persisted = json.loads((self.work / 'gepa/adapter_usage.json').read_text())
        self.assertEqual(persisted['usage']['output_tokens'], 5)
        generations = list((self.work / 'gepa/generations/dev11').iterdir())
        self.assertEqual(len(generations), 1)
        self.assertFalse((generations[0] / 'harness/adopted.json').exists())


if __name__ == '__main__':
    unittest.main()
