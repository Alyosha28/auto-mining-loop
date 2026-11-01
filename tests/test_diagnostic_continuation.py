#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
sys.path.insert(0, str(SCRIPTS))
import diagnostic_results
import evo_harness
import research_contract
import run_diagnostic_continuation as continuation

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


def probe(values: list[dict[str, float]]) -> dict:
    return {'candidates': [{'name': str(index), 'ic_by_date': row}
                           for index, row in enumerate(values)]}


def unit(task: str, seed: int, delta: float) -> dict:
    usage = {'decisions': 2, 'model_calls': 2, 'input_tokens': 20,
             'output_tokens': 10, 'candidate_budget': 24}
    return {
        'task': task, 'seed': seed, 'status': 'COMPLETED',
        'select_diagnostic_minus_parent': delta,
        'common_support_verified': True, 'actual_model_calls_equal': True,
        'actual_decisions_equal': True, 'split_verified': True,
        'branchpoint_identity_verified': True, 'shared_memory_verified': True,
        'frozen_champions_verified': True, 'internal_select_excluded': True,
        'task_constraints_satisfied': True,
        'usage': {'shared': usage, 'parent': usage, 'diagnostic': usage},
        'failed_or_invalid_decisions': 0,
    }


class DateDiagnosticTests(unittest.TestCase):
    def test_date_blocks_use_explicit_daily_absolute_ic_diagnostic(self) -> None:
        parent = probe([{'2020-01-01': -0.1, '2020-01-02': 0.2},
                        {'2020-01-01': 0.1, '2020-01-02': -0.2}])
        diagnostic = probe([{'2020-01-01': -0.2, '2020-01-02': 0.1},
                            {'2020-01-01': 0.2, '2020-01-02': -0.1}])
        result = diagnostic_results.date_block_diagnostic(parent, diagnostic, block_size=2)
        self.assertEqual(result['metric'], 'mean_daily_abs_ic_delta')
        self.assertEqual(result['dates'][0]['mean_daily_abs_ic_delta'], 0.1)
        self.assertEqual(result['dates'][1]['mean_daily_abs_ic_delta'], -0.1)
        self.assertEqual(result['blocks'][0]['mean_daily_abs_ic_delta'], 0.0)


class AggregationTests(unittest.TestCase):
    def test_panel_median_task_rules_and_leave_one_task_out(self) -> None:
        tasks = ['T1', 'T2', 'T3', 'T4']
        units = [unit(task, seed, -0.01 if task == 'T4' else 0.001)
                 for task in tasks for seed in (11, 29, 43)]
        result = diagnostic_results.aggregate_units(units, tasks, [11, 29, 43])
        self.assertEqual(result['median_paired_delta'], 0.001)
        self.assertEqual(result['tasks_with_positive_seed_median'], 3)
        self.assertEqual(result['evidence_status'], 'development-panel supported')
        self.assertEqual(result['usage']['model_calls'], 72)

    def test_protocol_invalid_does_not_become_research_null(self) -> None:
        tasks = ['T1', 'T2', 'T3', 'T4']
        units = [unit(task, seed, 0.0) for task in tasks for seed in (11, 29, 43)]
        units[0]['actual_model_calls_equal'] = False
        result = diagnostic_results.aggregate_units(units, tasks, [11, 29, 43])
        self.assertEqual(result['evidence_status'], 'protocol-invalid')
        self.assertFalse(result['all_protocol_checks_passed'])


class UnitIntegrationTests(unittest.TestCase):
    def test_one_synthetic_unit_executes_shared_branch_and_frozen_comparison(self) -> None:
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
                'schema': continuation.PROTOCOL_SCHEMA, 'protocol_version': 'TEST',
                'model_id': 'fixed-test-model', 'proposer': str(proposer),
                'run_root': str(work / 'runs'),
                'shared_contract': str(contract_path), 'continuation_contract': str(contract_path),
                'shared_decisions': 2, 'continuation_decisions': 2,
                'candidate_budget': 12, 'dev_split': 'seeded-year-blocks',
                'proposer_timeout_seconds': 30, 'execution_timeout_seconds': 60,
                'coverage_log_only': True, 'demo': True, 'seeds': [11],
                'tasks': [{
                    'task': 'T1', 'research_question': 'Can bounded allocation improve probe IC?',
                    'initial_harness': str(harness_path), 'initial_safe_trace': str(trace_path),
                    'shared_memory': str(memory_path), 'task_constraints': {'min_coverage': 0.0},
                }],
            }), encoding='utf-8')
            continuation.protocol_record(protocol_path)
            result = continuation.run_unit(protocol_path, 'T1', 11)
            self.assertEqual(result['status'], 'COMPLETED')
            self.assertTrue(result['common_support_verified'])
            self.assertTrue(result['actual_model_calls_equal'])
            self.assertTrue(result['branchpoint_identity_verified'])
            self.assertTrue(result['shared_memory_verified'])
            self.assertTrue(result['internal_select_excluded'])
            self.assertEqual(result['usage']['shared']['model_calls'], 2)
            self.assertEqual(result['usage']['parent']['model_calls'], 2)
            self.assertEqual(result['usage']['diagnostic']['model_calls'], 2)
            self.assertIn('date_block_diagnostic', result)
            comparison = work / 'runs/DIAGNOSTIC_CONTINUATION_T1_S11_TEST/common_comparison/result.json'
            self.assertTrue(comparison.is_file())
            self.assertFalse(json.loads(comparison.read_text())['selection_performed'])


if __name__ == '__main__':
    unittest.main()
