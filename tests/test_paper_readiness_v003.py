#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import random
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
import research_contract
import run_paper_readiness_v003 as v003

MODEL_PROPOSER = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
usage = {'schema': 'AUTO-RESEARCH-MODEL-USAGE-1',
         'model_id': os.environ['AUTO_RESEARCH_MODEL_ID'],
         'calls': 1, 'input_tokens': 10, 'output_tokens': 5}
Path(os.environ['AUTO_RESEARCH_USAGE']).write_text(json.dumps(usage))
payload = {'schema': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1', 'edits': [
    {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
     'value': 'rank', 'intent': 'test atomic cross-sectional normalization'},
    {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
     'op': 'set', 'value': 1, 'intent': 'retain a bounded mutation control'}]}
Path(os.environ['AUTO_RESEARCH_PROPOSAL']).write_text(json.dumps(payload))
'''


def contract() -> dict:
    return {
        'schema': research_contract.CONTRACT_SCHEMA,
        'budget': {'max_decisions': 4, 'max_candidate_budget': 48, 'max_model_calls': 4,
                   'max_input_tokens': 100, 'max_output_tokens': 100,
                   'max_no_improvement': 4, 'max_debug_depth': 2},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': 0.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }


class RandomProposalTests(unittest.TestCase):
    def test_random_atomic_proposal_is_deterministic_legal_and_zero_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            outputs = []
            for name in ('first', 'second'):
                generation = work / name
                generation.mkdir()
                (generation / 'research_state.json').write_text(json.dumps({
                    'current_harness': evo_harness.default_harness()}), encoding='utf-8')
                environment = os.environ.copy()
                environment.update({
                    'AUTO_RESEARCH_GENERATION': str(generation),
                    'AUTO_RESEARCH_RUN_ID': 'same_decision',
                    'AUTO_RESEARCH_RANDOM_SEED': '20261031',
                    'AUTO_RESEARCH_MODEL_ID': 'random-atomic-edits-v1',
                    'AUTO_RESEARCH_USAGE': str(generation / 'usage.json'),
                    'AUTO_RESEARCH_PROPOSAL': str(generation / 'proposal.json'),
                })
                subprocess.run([sys.executable, str(SCRIPTS / 'random_proposer_command.py')],
                               check=True, env=environment)
                payload = json.loads((generation / 'proposal.json').read_text())
                usage = json.loads((generation / 'usage.json').read_text())
                self.assertEqual(payload['schema'], 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1')
                self.assertEqual(usage['calls'], 0)
                evo_harness.apply_edits(evo_harness.default_harness(), payload['edits'])
                outputs.append((generation / 'proposal.json').read_bytes())
            self.assertEqual(outputs[0], outputs[1])


class ExecutionRecoveryTests(unittest.TestCase):
    def test_recovery_replays_persisted_proposal_without_recharging_usage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            output = work / 'loop'
            source = output / 'decisions/loop_d001'
            (source / 'generation').mkdir(parents=True)
            (source / 'generation/edits.json').write_text(
                json.dumps({'schema': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1', 'edits': []}), encoding='utf-8')
            (source / 'generation/model_usage.json').write_text('{}', encoding='utf-8')
            (source / 'run_manifest.json').write_text(
                json.dumps({'status': 'PREPARED'}), encoding='utf-8')
            (output / 'contract.json').write_text('{}', encoding='utf-8')
            usage = {'decisions': 1, 'model_calls': 1, 'input_tokens': 10, 'output_tokens': 5}
            (output / 'loop_state.json').write_text(json.dumps({
                'status': 'FAILED', 'stop_reason': 'ROUTE_EXECUTION_FAILED', 'history': [], 'usage': usage,
                'pending': {'run_id': 'loop_d001', 'run_dir': str(source), 'phase': 'EXECUTE'},
            }), encoding='utf-8')
            recovery = work / 'recovery'
            v003.prepare_execution_recovery({}, output, recovery)
            recovered = json.loads((recovery / 'loop_state.json').read_text())
            self.assertEqual(recovered['usage'], usage)
            self.assertEqual(recovered['pending']['phase'], 'EXECUTE')
            self.assertEqual(recovered['recovery_from'], str(output))
            self.assertTrue((recovery / 'decisions/loop_d001/generation/edits.json').is_file())
            self.assertFalse((source / 'result.json').exists())


    def test_no_usage_outcome_unknown_retries_only_prepared_input(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            output = work / 'loop'
            source = output / 'decisions/loop_d004'
            (source / 'generation').mkdir(parents=True)
            (source / 'generation/research_state.json').write_text('{}', encoding='utf-8')
            (source / 'generation/proposer_attempt.json').write_text('{}', encoding='utf-8')
            (source / 'generation/proposer_stdout.log').write_text('partial', encoding='utf-8')
            (source / 'run_manifest.json').write_text(
                json.dumps({'status': 'PREPARED'}), encoding='utf-8')
            (output / 'contract.json').write_text('{}', encoding='utf-8')
            usage = {'decisions': 3, 'model_calls': 3, 'input_tokens': 30, 'output_tokens': 15}
            state = {'status': 'FAILED', 'stop_reason': 'PROPOSER_OUTCOME_UNKNOWN', 'history': [], 'usage': usage,
                     'pending': {'run_id': 'loop_d004', 'run_dir': str(source), 'phase': 'PROPOSE'}}
            (output / 'loop_state.json').write_text(json.dumps(state), encoding='utf-8')
            recovery = work / 'recovery'
            v003.prepare_timeout_recovery({}, output, recovery)
            recovered = json.loads((recovery / 'loop_state.json').read_text())
            self.assertEqual(recovered['usage'], usage)
            self.assertEqual(recovered['pending']['phase'], 'PROPOSE')
            self.assertTrue((recovery / 'decisions/loop_d004/generation/research_state.json').is_file())
            self.assertFalse((recovery / 'decisions/loop_d004/generation/proposer_attempt.json').exists())
            self.assertFalse((recovery / 'decisions/loop_d004/generation/edits.json').exists())
            self.assertFalse((recovery / 'decisions/loop_d004/generation/model_usage.json').exists())
            self.assertIn('no-usage timeout', v003.infrastructure_reason(str(output)))


class AnalysisTests(unittest.TestCase):
    def test_executed_probe_steps_follows_recovery_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            recovery = root / 'recovery'
            recovery.mkdir()
            history = []
            for index in range(3):
                step = root / 'source' / f'd{index}' / 'child' / 'step'
                step.mkdir(parents=True)
                (step / 'step_record.json').write_text(json.dumps({
                    'probe_only': True, 'adopted_probe': {'metrics': {'n_candidates': 12}}}))
                history.append({'status': 'COMPLETED', 'child_run': str(step)})
            history.append({'status': 'FAILED', 'child_run': str(root / 'failed')})
            (recovery / 'loop_state.json').write_text(json.dumps({'history': history}))
            self.assertEqual(v003.executed_probe_steps(recovery), (3, 36))


    def test_cluster_interval_and_exact_sign_flip_are_deterministic(self) -> None:
        units = []
        for task_index in range(6):
            for seed in (11, 29):
                for replicate in ('A', 'B'):
                    units.append({'task': f'T{task_index}', 'seed': seed,
                                  'model_replicate': replicate, 'delta': 0.01 if task_index < 5 else -0.01})
        groups = {f'T{index}': [0.01] * 4 for index in range(5)}
        groups['T5'] = [-0.01] * 4
        tasks = sorted(groups)
        rng = random.Random(7)
        indices = [tuple(rng.randrange(6) for _ in tasks) for _ in range(100)]
        result = v003.contrast_analysis(units, tasks, 'delta', indices)
        self.assertEqual(result['positive_tasks'], 5)
        self.assertEqual(result['delta_counts'], {'positive': 20, 'zero': 0, 'negative': 4})
        self.assertEqual(result['exact_task_sign_flip_p'], v003.exact_sign_flip_p(groups, 0.01))
        self.assertEqual(v003.holm({'a': 0.01, 'b': 0.04}), {'a': 0.02, 'b': 0.04})


    def test_complete_panel_aggregation_balances_budgets_and_contrasts(self) -> None:
        units = []
        for task_index in range(6):
            for seed in (11, 29):
                for replicate in ('A', 'B'):
                    delta = 0.01 if task_index < 5 else -0.01
                    usage = {'decisions': 4, 'model_calls': 4, 'candidate_budget': 48,
                             'input_tokens': 40, 'output_tokens': 20}
                    random_usage = dict(usage, model_calls=0, input_tokens=0, output_tokens=0)
                    metrics = {'coverage': 1.0, 'pass_rate': 1.0, 'novelty': 0.2,
                               'duplicate_rate': 0.1, 'turnover_proxy': 0.3}
                    unit = {'task': f'T{task_index}', 'seed': seed, 'model_replicate': replicate,
                            'status': 'COMPLETED', 'select_memory_minus_scalar': delta,
                            'select_diagnostic_minus_memory': 0.0,
                            'select_memory_minus_random': delta + 0.01,
                            'select_memory_minus_fixed': delta + 0.02,
                            'select_metrics': {arm: metrics for arm in (*v003.ARMS, 'fixed')},
                            'usage': {arm: usage for arm in v003.MODEL_ARMS},
                            'failed_or_invalid_decisions': 0}
                    unit['usage']['random'] = random_usage
                    unit.update({key: True for key in v003.CHECKS})
                    units.append(unit)
        protocol = {'tasks': [{'task': f'T{index}'} for index in range(6)],
                    'seeds': [11, 29], 'model_replicates': ['A', 'B'],
                    'analysis': {'bootstrap_seed': 7, 'task_bootstrap_samples': 100},
                    'maximum_model_calls': 288, 'maximum_model_candidate_positions': 3456,
                    'maximum_random_candidate_positions': 1152}
        with mock.patch.object(v003, 'executed_positions', return_value=(144, 48, 16)):
            result = v003.aggregate_units(units, protocol)
        self.assertEqual(result['evidence_status'], 'development-panel complete')
        self.assertEqual(result['usage']['scalar']['model_calls'], 96)
        self.assertEqual(result['usage']['random']['model_calls'], 0)
        self.assertEqual(result['usage']['model_candidate_positions'], 3456)
        self.assertEqual(result['usage']['random_candidate_positions'], 1152)
        self.assertIn('holm_adjusted_p', result['contrasts']['memory_minus_scalar'])
        self.assertNotIn('holm_adjusted_p', result['contrasts']['memory_minus_random'])


class UnitIntegrationTests(unittest.TestCase):
    def test_four_arm_atomic_unit_and_random_budget(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            work = Path(raw)
            harness = evo_harness.default_harness()
            harness['protocol']['budget_candidates'] = 12
            harness['protocol']['dev_split'] = 'seeded-year-blocks'
            harness['memory']['experience_dir'] = str(work / 'experience')
            harness_path = work / 'harness.json'
            harness_path.write_text(json.dumps(harness), encoding='utf-8')
            trace_path = work / 'trace.json'
            trace_path.write_text(json.dumps({
                'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1', 'source_run': 'initial',
                'scope': 'probe only', 'reference_probe_metrics': {'mean_abs_ic': 0.04},
                'proposals': [], 'kept_edits': [],
            }), encoding='utf-8')
            memory_path = work / 'memory.json'
            memory_path.write_text(json.dumps({
                'schema': 'AUTO-RESEARCH-SHARED-MEMORY-1',
                'entries': [{'source_run': 'prior_task', 'status': 'COMPLETED',
                             'scope': 'cross-task development probe',
                             'probe_metrics': {'mean_abs_ic': 0.04}}],
            }), encoding='utf-8')
            proposer = work / 'proposer.py'
            proposer.write_text(MODEL_PROPOSER, encoding='utf-8')
            proposer.chmod(0o755)
            contract_path = work / 'contract.json'
            contract_path.write_text(json.dumps(contract()), encoding='utf-8')
            protocol_path = work / 'protocol.json'
            protocol_path.write_text(json.dumps({
                'schema': v003.PROTOCOL_SCHEMA, 'protocol_version': 'TEST',
                'model_id': 'fixed-test-model', 'random_model_id': 'random-atomic-edits-v1',
                'random_seed': 20261031, 'proposer': str(proposer),
                'random_proposer': str(SCRIPTS / 'random_proposer_command.py'),
                'proposer_environment': {'AUTO_RESEARCH_ATOMIC_PROPOSAL': '1'},
                'run_root': str(work / 'runs'),
                'shared_contract': str(contract_path), 'continuation_contract': str(contract_path),
                'shared_decisions': 4, 'continuation_decisions': 4,
                'candidate_budget': 12, 'dev_split': 'seeded-year-blocks',
                'proposer_timeout_seconds': 30, 'execution_timeout_seconds': 60,
                'coverage_log_only': True, 'demo': True, 'seeds': [11], 'model_replicates': ['A'],
                'tasks': [{'task': 'T9', 'research_question': 'Can atomic research improve probe IC?',
                           'initial_harness': str(harness_path), 'initial_safe_trace': str(trace_path),
                           'shared_memory': str(memory_path),
                           'task_constraints': {'min_coverage': 0.0}}],
            }), encoding='utf-8')
            v003.protocol_record(protocol_path)
            v003.protocol_record(protocol_path)
            blocked_bin = work / 'blocked-bin'
            blocked_bin.mkdir()
            blocked_python = blocked_bin / 'python3'
            blocked_python.write_text('#!/bin/sh\nexit 99\n', encoding='utf-8')
            blocked_python.chmod(0o755)
            blocked_path = str(blocked_bin) + os.pathsep + os.environ.get('PATH', '')
            with mock.patch.dict(os.environ, {'PATH': blocked_path}):
                result = v003.run_unit(protocol_path, 'T9', 11, 'A')
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertTrue(result['random_zero_calls_verified'])
            self.assertTrue(result['initial_identity_verified'])
            for arm in v003.MODEL_ARMS:
                self.assertEqual(result['usage'][arm]['model_calls'], 4)
            self.assertEqual(result['usage']['random']['model_calls'], 0)
            model_positions, random_positions, decisions = v003.executed_positions(result)
            self.assertEqual(model_positions, 144)
            self.assertEqual(random_positions, 48)
            self.assertEqual(decisions, 16)
            proposal_path = Path(result['scalar_run']) / 'decisions' / next(
                path.name for path in (Path(result['scalar_run']) / 'decisions').iterdir()
                if path.name.endswith('_d001')) / 'child/step/step_record.json'
            record = json.loads(proposal_path.read_text())
            self.assertEqual(record['llm_proposal']['proposal_semantics'], 'atomic')
            self.assertEqual(record['edits'][0]['component'], 'atomic')


if __name__ == '__main__':
    unittest.main()
