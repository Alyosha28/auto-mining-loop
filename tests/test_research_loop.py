#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
import sys
sys.path.insert(0, str(SCRIPTS))
import compare_harnesses
import evo_harness
import research_contract
import run_research_loop as loop
import run_diagnostic_continuation as continuation

QUESTION = 'Can a bounded loop improve allocation under a frozen evaluator?'


def example_trace() -> dict:
    return {
        'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1', 'source_run': 'R3_TEST',
        'scope': 'Prior probe evidence only.',
        'search_policy': {'operators': ['residual_size'], 'skills': {'crossover_pairs': 0}},
        'reference_probe_metrics': {'mean_abs_ic': 0.04, 'novelty': 0.125},
        'proposals': [], 'kept_edits': [],
    }


def example_edits() -> list[dict]:
    return [
        {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
         'value': 'rank', 'intent': 'test cross-sectional normalization'},
        {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
         'op': 'set', 'value': 1, 'intent': 'keep mutation depth fixed for this control'},
    ]


def contract_payload() -> dict:
    return {
        'schema': research_contract.CONTRACT_SCHEMA,
        'budget': {'max_decisions': 2, 'max_candidate_budget': 24, 'max_model_calls': 2,
                   'max_input_tokens': 100, 'max_output_tokens': 100,
                   'max_no_improvement': 2, 'max_debug_depth': 1},
        'primary': {'metric': 'mean_abs_ic', 'min_delta': -1.0},
        'guards': {'min_delta_coverage': -1.0, 'min_delta_pass_rate': -1.0,
                   'max_delta_turnover_proxy': 1.0, 'min_delta_novelty': -1.0,
                   'max_delta_duplicate_rate': 1.0},
        'online_evaluation': 'probe', 'final_evaluation': 'select',
    }


PROPOSER = r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

generation = Path(os.environ['AUTO_RESEARCH_GENERATION'])
run_id = os.environ['AUTO_RESEARCH_RUN_ID']
index = int(run_id.rsplit('_d', 1)[1]) - 1
scenario = json.loads(Path(__file__).with_name('scenario.json').read_text())
action = scenario[index]
usage = {'schema': 'AUTO-RESEARCH-MODEL-USAGE-1',
         'model_id': os.environ['AUTO_RESEARCH_MODEL_ID'],
         'calls': 1, 'input_tokens': 10, 'output_tokens': 5}
usage.update(action.get('usage', {}))
Path(os.environ['AUTO_RESEARCH_USAGE']).write_text(json.dumps(usage))
edits = [
    {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
     'value': 'rank', 'intent': 'test cross-sectional normalization'},
    {'id': 'fixed_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
     'op': 'set', 'value': 1, 'intent': 'keep mutation depth fixed'}]
if os.environ['AUTO_RESEARCH_MODE'] == 'local-only':
    Path(os.environ['AUTO_RESEARCH_PROPOSAL']).write_text(
        json.dumps(edits[:1] if action.get('invalid') else edits))
else:
    route_action = action.get('action', 'EXPLOIT')
    research_state = json.loads((generation / 'research_state.json').read_text())
    route = {'schema': 'AUTO-RESEARCH-ROUTE-1', 'action': route_action,
             'rationale': 'bounded synthetic proposer action',
             'evidence_refs': [research_state['source_run']],
             'edits': edits if route_action in ('EXPLOIT', 'REPAIR') else []}
    if route_action in ('EXPLOIT', 'REPAIR'):
        route['falsification'] = 'the paired comparison does not support the edit'
    if route_action == 'REPAIR':
        route['target'] = 'synthetic failure'
    Path(os.environ['AUTO_RESEARCH_PROPOSAL']).write_text(json.dumps(route))
if action.get('over_limit'):
    (generation / 'per_call_budget_exceeded.json').write_text(json.dumps(
        {'schema': 'AUTO-RESEARCH-PER-CALL-BUDGET-EXCEEDED-1'}))
sys.exit(action.get('exit', 0))
'''


class LoopTestCase(unittest.TestCase):
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
        self.proposer = self.work / 'proposer.py'
        self.proposer.write_text(PROPOSER, encoding='utf-8')
        self.proposer.chmod(0o755)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def prepare(self, scenario: list[dict], contract: dict, name='loop') -> tuple[argparse.Namespace, Path]:
        output = self.work / name
        contract_path = self.work / f'{name}_contract.json'
        contract_path.write_text(json.dumps(contract), encoding='utf-8')
        (self.work / 'scenario.json').write_text(json.dumps(scenario), encoding='utf-8')
        args = argparse.Namespace(
            output=output, resume=False, contract=contract_path, proposer=self.proposer,
            harness=self.harness_path, trace_file=self.trace_path, route_history=None,
            research_question=QUESTION, mode='local-only', model_id='fixed-test-model',
            prompt_feedback='diagnostic', fixed_decisions=False, branchpoint=None,
            shared_memory=None,
            seed=11, budget=12, dev_split='seeded-year-blocks', features=None,
            coverage_log_only=True, demo=True, proposer_timeout_seconds=30,
            execution_timeout_seconds=60)
        return args, output


class ContractTests(unittest.TestCase):
    def test_contract_and_usage_fail_closed(self) -> None:
        payload = contract_payload()
        self.assertEqual(research_contract.validate_contract(payload), payload)
        invalid = copy.deepcopy(payload)
        del invalid['guards']['min_delta_novelty']
        with self.assertRaises(Exception):
            research_contract.validate_contract(invalid)
        usage = {'schema': research_contract.USAGE_SCHEMA, 'model_id': 'fixed',
                 'calls': 1, 'input_tokens': 2, 'output_tokens': 3}
        self.assertEqual(research_contract.validate_usage(usage, 'fixed'), usage)
        usage['model_id'] = 'changed'
        with self.assertRaises(Exception):
            research_contract.validate_usage(usage, 'fixed')

    def test_adoption_requires_primary_and_every_guard(self) -> None:
        reference = {'mean_abs_ic': 0.04, 'coverage': 1.0, 'pass_rate': 0.5,
                     'turnover_proxy': 0.2, 'novelty': 0.5, 'duplicate_rate': 0.1}
        adopted = dict(reference, mean_abs_ic=0.05, novelty=0.4, duplicate_rate=0.2)
        contract = contract_payload()
        contract['primary']['min_delta'] = 0.002
        contract['guards']['min_delta_novelty'] = 0.0
        contract['guards']['max_delta_duplicate_rate'] = 0.0
        result = research_contract.evaluate_adoption(contract, reference, adopted)
        self.assertFalse(result['supported'])
        self.assertIn('min_delta_novelty', result['reject_reasons'])
        self.assertIn('max_delta_duplicate_rate', result['reject_reasons'])


class ResearchLoopTests(LoopTestCase):
    def test_local_loop_consumes_decision_and_model_budget(self) -> None:
        args, output = self.prepare([{}, {}], contract_payload())
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'DECISION_BUDGET_EXHAUSTED')
        self.assertEqual(result['usage']['decisions'], 2)
        self.assertEqual(result['usage']['candidate_budget'], 24)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(result['usage']['input_tokens'], 20)
        self.assertEqual(result['nodes'], 3)
        history = json.loads((output / 'route_history.json').read_text())
        self.assertEqual([entry['action'] for entry in history['entries']], ['EXPLOIT', 'EXPLOIT'])

    def test_route_stop_does_not_require_unused_candidate_budget(self) -> None:
        contract = contract_payload()
        contract['budget']['max_candidate_budget'] = 12
        args, output = self.prepare([{}, {'action': 'STOP'}], contract)
        args.mode = 'route-choice'
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'MODEL_STOP')
        self.assertEqual(result['usage']['candidate_budget'], 12)
        decisions = sorted((output / 'decisions').iterdir())
        second = json.loads((decisions[-1] / 'result.json').read_text())
        self.assertEqual(second['action'], 'STOP')
        self.assertFalse((decisions[-1] / 'child').exists())

    def test_invalid_local_proposal_uses_bounded_repair(self) -> None:
        contract = contract_payload()
        contract['budget']['max_candidate_budget'] = 12
        args, output = self.prepare([{'invalid': True}, {}], contract)
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['usage']['candidate_budget'], 12)
        history = json.loads((output / 'route_history.json').read_text())
        self.assertEqual([(entry['action'], entry['status']) for entry in history['entries']],
                         [('EXPLOIT', 'FAILED'), ('REPAIR', 'COMPLETED')])
        second = json.loads((output / 'decisions/loop_d002/result.json').read_text())
        self.assertEqual(second['action'], 'REPAIR')

    def test_failed_contract_preserves_champion_but_advances_active_branch(self) -> None:
        contract = contract_payload()
        contract['primary']['min_delta'] = 999.0
        contract['budget']['max_no_improvement'] = 1
        args, output = self.prepare([{}], contract)
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'NO_IMPROVEMENT_BUDGET_EXHAUSTED')
        self.assertNotEqual(result['current_node_id'], 'root')
        self.assertEqual(result['champion_node_id'], 'root')
        self.assertEqual(result['champion_harness'], str(self.harness_path))
        state = json.loads((output / 'loop_state.json').read_text())
        self.assertFalse(state['nodes'][-1]['supported'])

    def test_model_budget_stops_before_candidate_execution(self) -> None:
        contract = contract_payload()
        contract['budget']['max_output_tokens'] = 4
        args, output = self.prepare([{}], contract)
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'MODEL_BUDGET_EXCEEDED')
        self.assertEqual(result['usage']['candidate_budget'], 0)
        self.assertFalse((output / 'decisions/loop_d001/child').exists())

    def test_cli_over_limit_usage_is_charged_without_execution(self) -> None:
        contract = contract_payload()
        args, output = self.prepare([{'over_limit': True, 'exit': 7}], contract)
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'MODEL_BUDGET_EXCEEDED')
        self.assertEqual(result['usage']['decisions'], 1)
        self.assertEqual(result['usage']['output_tokens'], 5)
        self.assertEqual(result['usage']['candidate_budget'], 0)
        self.assertFalse((output / 'decisions/loop_d001/child').exists())

    def test_proposer_failure_recovers_without_a_second_model_call(self) -> None:
        contract = contract_payload()
        contract['budget']['max_decisions'] = 1
        contract['budget']['max_candidate_budget'] = 12
        contract['budget']['max_model_calls'] = 1
        args, output = self.prepare([{'exit': 7}], contract)
        self.assertEqual(loop.run(args), 2)
        state = json.loads((output / 'loop_state.json').read_text())
        self.assertEqual(state['status'], 'FAILED')
        self.assertEqual(state['usage']['decisions'], 0)
        args.resume = True
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['usage']['model_calls'], 1)
        self.assertEqual(result['usage']['candidate_budget'], 12)
    def test_fixed_protocol_continues_negative_active_branch_and_freezes_branchpoint(self) -> None:
        contract = contract_payload()
        contract['primary']['min_delta'] = 999.0
        contract['budget']['max_no_improvement'] = 1
        args, output = self.prepare([{}, {}], contract)
        args.fixed_decisions = True
        args.prompt_feedback = 'parent'
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['usage']['decisions'], 2)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertNotEqual(result['active_branch']['node_id'], 'root')
        self.assertEqual(result['champion_node_id'], 'root')
        self.assertTrue((output / 'branchpoint.json').is_file())
        for record_path in output.glob('decisions/*/child/step/step_record.json'):
            record = json.loads(record_path.read_text())
            self.assertTrue(record['probe_only'])
            self.assertIsNone(record['adopted_select'])
            self.assertIsNone(record['reference']['select'])

    def test_failed_model_call_is_charged_and_fixed_protocol_continues(self) -> None:
        args, output = self.prepare([{'exit': 7}, {}], contract_payload())
        args.fixed_decisions = True
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['usage']['decisions'], 2)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(result['usage']['candidate_budget'], 12)
        failure = output / 'decisions/loop_d001/generation/model_failure.json'
        self.assertTrue(failure.is_file())
        self.assertEqual(json.loads(failure.read_text())['returncode'], 7)

    def test_fixed_protocol_continues_after_per_call_output_failure(self) -> None:
        args, output = self.prepare([{'over_limit': True, 'exit': 7}, {}], contract_payload())
        args.fixed_decisions = True
        self.assertEqual(loop.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertEqual(result['stop_reason'], 'DECISION_BUDGET_EXHAUSTED')
        self.assertEqual(result['usage']['decisions'], 2)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(result['usage']['candidate_budget'], 12)
        history = json.loads((output / 'route_history.json').read_text())
        self.assertEqual(history['entries'][0]['status'], 'FAILED')
        self.assertEqual(history['entries'][1]['action'], 'REPAIR')

    def test_budget_recovery_preserves_failed_run_and_charges_only_remaining_call(self) -> None:
        args, output = self.prepare([{'over_limit': True, 'exit': 7}, {}], contract_payload())
        self.assertEqual(loop.run(args), 0)
        original = (output / 'result.json').read_bytes()
        state_path = output / 'loop_state.json'
        state = json.loads(state_path.read_text())
        state['fixed_decisions'] = True
        loop.write_state(state_path, state)
        protocol = {'proposer_environment': {}}
        recovery, result = continuation.ensure_budget_recovery(
            protocol, {}, output, feedback='diagnostic', branchpoint=None, expected_calls=2)
        self.assertEqual((output / 'result.json').read_bytes(), original)
        self.assertEqual(result['usage']['decisions'], 2)
        self.assertEqual(result['usage']['model_calls'], 2)
        self.assertEqual(result['usage']['candidate_budget'], 12)
        self.assertEqual(result['stop_reason'], 'DECISION_BUDGET_EXHAUSTED')
        recovered_state = json.loads((recovery / 'loop_state.json').read_text())
        self.assertEqual(recovered_state['recovery_from'], str(output))

    def test_two_continuations_restore_the_same_read_only_branchpoint(self) -> None:
        shared_args, shared = self.prepare([{}, {}], contract_payload(), name='shared')
        shared_args.fixed_decisions = True
        shared_args.prompt_feedback = 'parent'
        self.assertEqual(loop.run(shared_args), 0)
        branchpoint = shared / 'branchpoint.json'
        original = branchpoint.read_bytes()
        outputs = {}
        for feedback in ('parent', 'diagnostic'):
            args, output = self.prepare([{}, {}], contract_payload(), name=feedback)
            args.fixed_decisions = True
            args.prompt_feedback = feedback
            args.branchpoint = branchpoint
            self.assertEqual(loop.run(args), 0)
            outputs[feedback] = output
        self.assertEqual(branchpoint.read_bytes(), original)
        left_state = json.loads((outputs['parent'] / 'loop_state.json').read_text())
        right_state = json.loads((outputs['diagnostic'] / 'loop_state.json').read_text())
        self.assertEqual(left_state['branchpoint_sha256'], right_state['branchpoint_sha256'])
        left = outputs['parent'] / 'decisions/parent_d001/generation'
        right = outputs['diagnostic'] / 'decisions/diagnostic_d001/generation'
        self.assertEqual((left / 'shared_research_memory.json').read_bytes(),
                         (right / 'shared_research_memory.json').read_bytes())
        self.assertEqual(json.loads((left / 'execution_trace.json').read_text())['proposals'], [])
        self.assertTrue(json.loads((right / 'execution_trace.json').read_text())['proposals'])


class CommonComparisonTests(LoopTestCase):
    def test_multiple_frozen_harnesses_share_probe_and_select_support(self) -> None:
        alternate = copy.deepcopy(self.harness)
        alternate['skills']['crossover_pairs'] = 0
        alternate_path = self.work / 'alternate.json'
        alternate_path.write_text(json.dumps(alternate), encoding='utf-8')
        contract = self.work / 'comparison_contract.json'
        contract.write_text(json.dumps(contract_payload()), encoding='utf-8')
        output = self.work / 'comparison'
        args = argparse.Namespace(
            arm=[f'base={self.harness_path}', f'alternate={alternate_path}'],
            contract=contract, output=output, seed=11, budget=12,
            dev_split='seeded-year-blocks', seed_panel=None, features=None,
            coverage_log_only=True, demo=True, probe_only=False)
        self.assertEqual(compare_harnesses.run(args), 0)
        result = json.loads((output / 'result.json').read_text())
        self.assertTrue(result['common_support_verified'])
        self.assertFalse(result['selection_performed'])
        for split in ('probe_metrics', 'select_metrics'):
            hashes = {arm[split]['common_support_sha256'] for arm in result['arms'].values()}
            self.assertEqual(len(hashes), 1)
        self.assertIn('alternate', result['comparisons'])
        self.assertFalse(result['independent_real_data_oos'])


if __name__ == '__main__':
    unittest.main()
