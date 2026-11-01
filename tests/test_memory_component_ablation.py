#!/usr/bin/env python3
from __future__ import annotations

import json
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
import run_memory_component_ablation as component
import run_route_step

PROPOSER = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
usage = {'schema': 'AUTO-RESEARCH-MODEL-USAGE-1',
         'model_id': os.environ['AUTO_RESEARCH_MODEL_ID'],
         'calls': 1, 'input_tokens': 10, 'output_tokens': 5}
Path(os.environ['AUTO_RESEARCH_USAGE']).write_text(json.dumps(usage))
edits = [
    {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
     'value': 'rank', 'intent': 'test cross-sectional normalization'},
    {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
     'op': 'set', 'value': 1, 'intent': 'retain a bounded mutation control'}]
Path(os.environ['AUTO_RESEARCH_PROPOSAL']).write_text(json.dumps(edits))
'''


def contract() -> dict:
    return {
        'schema': research_contract.CONTRACT_SCHEMA,
        'budget': {'max_decisions': 2, 'max_candidate_budget': 24, 'max_model_calls': 2,
                   'max_input_tokens': 100, 'max_output_tokens': 100,
                   'max_no_improvement': 1, 'max_debug_depth': 1},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': 0.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }


def unit(task: str, seed: int, delta: float) -> dict:
    usage = {'decisions': 2, 'model_calls': 2, 'input_tokens': 20,
             'output_tokens': 10, 'candidate_budget': 24}
    return {
        'task': task, 'seed': seed, 'status': 'COMPLETED',
        'select_memory_minus_scalar': delta,
        'select_memory_minus_fixed': delta + 0.01,
        'select_scalar_minus_fixed': 0.01,
        'select_diagnostic_minus_memory': 0.0,
        'select_diagnostic_minus_fixed': delta + 0.01,
        'common_support_verified': True, 'actual_model_calls_equal': True,
        'actual_decisions_equal': True, 'split_verified': True,
        'initial_identity_verified': True, 'memory_contract_verified': True,
        'diagnostic_contract_verified': True, 'frozen_champions_verified': True,
        'internal_select_excluded': True, 'task_constraints_satisfied': True,
        'usage': {arm: usage for arm in component.ARMS},
        'select_metrics': {
            arm: {'coverage': 1.0, 'pass_rate': 1.0, 'novelty': 0.2,
                  'duplicate_rate': 0.1, 'turnover_proxy': 0.3}
            for arm in (*component.ARMS, 'fixed')},
        'date_block_memory_minus_scalar': {'largest_positive_block_share': 0.5},
        'date_block_diagnostic_minus_memory': {'largest_positive_block_share': None},
        'comparison_run': 'test/common_comparison',
        'failed_or_invalid_decisions': 0,
    }


class AggregationTests(unittest.TestCase):
    def test_memory_panel_median_and_task_rules(self) -> None:
        tasks = ['T5', 'T6', 'T7', 'T8']
        units = [unit(task, seed, -0.01 if task == 'T8' else 0.001)
                 for task in tasks for seed in (11, 29, 43)]
        with mock.patch.object(component, 'executed_positions', return_value=(72, 6)):
            result = component.aggregate_units(units, tasks, [11, 29, 43])
        self.assertEqual(result['median_paired_delta'], 0.001)
        self.assertEqual(result['tasks_with_positive_seed_median'], 3)
        self.assertEqual(result['evidence_status'], 'development-panel supported')
        self.assertEqual(result['usage']['model_calls'], 72)
        self.assertEqual(result['usage']['executed_candidate_positions'], 864)
        self.assertEqual(result['delta_counts'], {'positive': 9, 'zero': 0, 'negative': 3})
        self.assertEqual(result['behavior_select_deltas']['memory_minus_scalar']['coverage']['median'], 0.0)
        self.assertEqual(result['date_block_concentration']['date_block_memory_minus_scalar']['median'], 0.5)
        self.assertEqual(result['date_block_concentration']['date_block_diagnostic_minus_memory']['non_null_units'], 0)

    def test_protocol_failure_is_not_research_null(self) -> None:
        tasks = ['T5', 'T6', 'T7', 'T8']
        units = [unit(task, seed, 0.0) for task in tasks for seed in (11, 29, 43)]
        units[0]['memory_contract_verified'] = False
        with mock.patch.object(component, 'executed_positions', return_value=(72, 6)):
            result = component.aggregate_units(units, tasks, [11, 29, 43])
        self.assertEqual(result['evidence_status'], 'protocol-invalid')

    def test_real_memory_has_no_select_payload(self) -> None:
        memory = ROOT / 'auto_research/config/memory_component_ablation_v002/memory/shared_research_memory.json'
        loaded = run_route_step.load_shared_memory(memory)
        self.assertGreater(len(loaded['entries']), 0)


    def test_incomplete_comparison_uses_a_new_recovery_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw)
            partial = output / 'common_comparison'
            partial.mkdir()
            (partial / 'contract.json').write_text('{}', encoding='utf-8')
            recovery = component.comparison_directory(output)
            self.assertEqual(recovery.name, 'common_comparison_RECOVERY_001')
            self.assertTrue((partial / 'contract.json').is_file())
            recovery.mkdir()
            (recovery / 'result.json').write_text('{}', encoding='utf-8')
            self.assertEqual(component.comparison_directory(output), recovery)


class UnitIntegrationTests(unittest.TestCase):
    def test_three_model_arms_share_initial_state_and_execute_once(self) -> None:
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
            proposer.write_text(PROPOSER, encoding='utf-8')
            proposer.chmod(0o755)
            contract_path = work / 'contract.json'
            contract_path.write_text(json.dumps(contract()), encoding='utf-8')
            protocol_path = work / 'protocol.json'
            protocol_path.write_text(json.dumps({
                'schema': component.PROTOCOL_SCHEMA, 'protocol_version': 'TEST',
                'model_id': 'fixed-test-model', 'proposer': str(proposer),
                'run_root': str(work / 'runs'),
                'shared_contract': str(contract_path), 'continuation_contract': str(contract_path),
                'shared_decisions': 2, 'continuation_decisions': 2,
                'candidate_budget': 12, 'dev_split': 'seeded-year-blocks',
                'proposer_timeout_seconds': 30, 'execution_timeout_seconds': 60,
                'coverage_log_only': True, 'demo': True, 'seeds': [11],
                'tasks': [{
                    'task': 'T5', 'research_question': 'Can cumulative memory improve probe IC?',
                    'initial_harness': str(harness_path), 'initial_safe_trace': str(trace_path),
                    'shared_memory': str(memory_path), 'task_constraints': {'min_coverage': 0.0},
                }],
            }), encoding='utf-8')
            component.protocol_record(protocol_path)
            result = component.run_unit(protocol_path, 'T5', 11)
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertTrue(result['initial_identity_verified'])
            self.assertTrue(result['memory_contract_verified'])
            self.assertTrue(result['diagnostic_contract_verified'])
            self.assertTrue(result['internal_select_excluded'])
            for arm in component.ARMS:
                self.assertEqual(result['usage'][arm]['model_calls'], 2)
            positions, decisions = component.executed_positions(result)
            self.assertEqual(positions, 72)
            self.assertEqual(decisions, 6)
            scalar_memory = work / 'runs/MEMORY_COMPONENT_T5_S11_TEST/T5_S11_SCALAR/decisions/T5_S11_SCALAR_d001/generation/shared_research_memory.json'
            self.assertEqual(json.loads(scalar_memory.read_text())['entries'], [])


if __name__ == '__main__':
    unittest.main()
