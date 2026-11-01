#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/current/factor-evolution-harness2/scripts'))
import evo_harness
import evo_probe

SPEC = importlib.util.spec_from_file_location(
    'research_harness_runner', ROOT / 'skills/current/factor-evolution-harness2/scripts/run_evo_step.py')
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
SCRIPT = ROOT / 'skills/current/factor-evolution-harness2/scripts/run_evo_step.py'


def example_trace() -> dict:
    return {
        'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1',
        'source_run': 'R1_TEST',
        'scope': 'Prior proposal and probe evidence only.',
        'search_policy': {'operators': ['residual_size', 'delta4', 'delta13', 'rank']},
        'proposals': [{'edit': {'id': 'inert_budget', 'component': 'skills.mutation_per_seed',
                                'op': 'set', 'value': 2},
                       'probe_metrics': {'mean_abs_ic': 0.04},
                       'delta': {'delta_mean_abs_ic': 0.0}, 'verdict': 'dropped'}],
    }


class CandidateBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.panel = evo_probe.synthetic_panel(seed=3)
        self.dates = pd.DatetimeIndex(sorted(self.panel['date'].unique()))[:70]
        self.harness = evo_harness.default_harness(seed_features=evo_probe.PREFERRED_SEEDS[:6])
        self.harness['protocol']['budget_candidates'] = 12
        self.harness['operators'].append('rank')

    def test_crossovers_are_reserved_and_mutations_visit_all_seeds(self) -> None:
        candidates = evo_probe.generate_candidates(self.harness, self.panel, self.dates, 12)
        self.assertEqual(Counter(item['kind'] for item in candidates),
                         {'mutation': 6, 'crossover': 2, 'seed': 4})
        self.assertEqual({item['parents'][0] for item in candidates if item['kind'] == 'mutation'},
                         set(evo_probe.PREFERRED_SEEDS[:6]))
        self.assertEqual(len({item['name'] for item in candidates}), 12)
        self.harness['skills']['mutation_per_seed'] = 2
        candidates = evo_probe.generate_candidates(self.harness, self.panel, self.dates, 12)
        counts = Counter(item['parents'][0] for item in candidates if item['kind'] == 'mutation')
        self.assertEqual(sum(counts.values()), 10)
        self.assertTrue(all(value <= 2 for value in counts.values()))
        self.assertEqual(Counter(item['kind'] for item in candidates)['crossover'], 2)

    def test_operator_removal_makes_later_operator_reachable(self) -> None:
        edit = evo_harness.validate_proposal({
            'id': 'focus', 'kind': 'INV', 'component': 'operators.remove', 'op': 'remove',
            'value': ['delta4', 'delta13'], 'intent': 'prioritize a tested transform within fixed budget'})
        harness = evo_harness.apply_edit(self.harness, edit)
        harness['skills']['mutation_per_seed'] = 2
        candidates = evo_probe.generate_candidates(harness, self.panel, self.dates, 12)
        self.assertTrue(any(item['operator'] == 'rank' for item in candidates))
        without_neg = evo_harness.apply_edit(self.harness, {
            'component': 'operators.remove', 'op': 'remove', 'value': 'neg'})
        self.assertNotIn('neg', without_neg['operators'])
        with self.assertRaises(ValueError):
            evo_harness.apply_edit(harness, {'component': 'operators.remove', 'op': 'remove',
                                             'value': ['residual_size', 'neg', 'rank']})
        with self.assertRaises(ValueError):
            evo_harness.validate_proposal({'id': 'bad', 'kind': 'INV', 'component': 'operators',
                                           'op': 'append', 'value': 'neg', 'intent': 'duplicate sign handling'})

    def test_atomic_proposal_validates_the_final_operator_roster(self) -> None:
        nested = [
            evo_harness.validate_proposal({
                'id': 'append', 'kind': 'INV', 'component': 'operators', 'op': 'append',
                'value': 'zscore', 'intent': 'add a replacement normalization operator'}),
            evo_harness.validate_proposal({
                'id': 'remove', 'kind': 'DOM', 'component': 'operators.remove', 'op': 'remove',
                'value': list(self.harness['operators']),
                'intent': 'focus the atomic candidate on the replacement operator'}),
        ]
        changed = evo_harness.apply_edit(self.harness, {
            'id': 'atomic', 'kind': 'DOM', 'component': 'atomic', 'op': 'compose', 'value': nested})
        self.assertEqual(changed['operators'], ['zscore'])
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'edits.json'
            path.write_text(json.dumps({'schema': 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1',
                                        'edits': nested}), encoding='utf-8')
            edits, record = RUNNER.load_llm_edits(path)
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]['component'], 'atomic')
        self.assertEqual(record['proposal_semantics'], 'atomic')
        with self.assertRaises(ValueError):
            evo_harness.apply_edit(self.harness, {
                'component': 'atomic', 'value': [nested[1]]})

    def test_infeasible_budget_rejects_duplicate_padding(self) -> None:
        self.harness['seed_pool'] = self.harness['seed_pool'][:2]
        with self.assertRaises(evo_probe.GuardrailError):
            evo_probe.generate_candidates(self.harness, self.panel, self.dates, 12)

    def test_duplication_threshold_is_connected(self) -> None:
        rng = np.random.default_rng(7)
        left = rng.normal(size=120)
        right_noise = rng.normal(size=120)
        right_noise -= np.dot(right_noise, left) / np.dot(left, left) * left
        right_noise /= np.std(right_noise)
        standardized = left / np.std(left)
        right = 0.875 * standardized + np.sqrt(1 - 0.875 ** 2) * right_noise
        dates = pd.date_range('2017-01-01', periods=120)
        candidates = [{'name': 'a', 'ic_series': pd.Series(left, index=dates)},
                      {'name': 'b', 'ic_series': pd.Series(right, index=dates)}]
        self.assertEqual(evo_probe._duplicate_rate(candidates, corr_threshold=0.9), 0.0)
        self.assertEqual(evo_probe._duplicate_rate(candidates, corr_threshold=0.85), 1.0)

    def test_probe_forwards_duplication_protocol(self) -> None:
        self.harness['probe']['duplication_corr'] = 0.85
        self.harness['probe']['duplication_min_overlap'] = 30
        original = evo_probe._duplicate_rate
        calls = []
        def spy(*args, **kwargs):
            calls.append(kwargs)
            return original(*args, **kwargs)
        evo_probe._duplicate_rate = spy
        try:
            evo_probe.probe_round(self.harness, self.panel, self.dates, self.dates)
        finally:
            evo_probe._duplicate_rate = original
        self.assertEqual(calls, [{'min_overlap': 30, 'corr_threshold': 0.85}])


class CommonSupportTests(unittest.TestCase):
    def test_missing_cells_cannot_create_an_ic_increment(self) -> None:
        panel = evo_probe.synthetic_panel(seed=19, dates=12, symbols=40)
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        left = panel['momentum_20'].copy()
        right = left.copy()
        right.loc[panel['symbol'].eq('S000')] = np.nan
        independent_left, _ = evo_probe.ic_series(panel, left, dates)
        independent_right, _ = evo_probe.ic_series(panel, right, dates)
        self.assertNotEqual(float(independent_left.mean()), float(independent_right.mean()))

        support, diagnostic = evo_probe.common_support(panel, [left, right], dates)
        common_left, _ = evo_probe.ic_series(panel, left, dates, support_mask=support)
        common_right, _ = evo_probe.ic_series(panel, right, dates, support_mask=support)
        pd.testing.assert_series_equal(common_left, common_right)
        self.assertEqual(float(common_left.mean()), float(common_right.mean()))
        self.assertEqual(diagnostic['dates'], len(dates))  # equal dates alone are insufficient
        self.assertEqual(diagnostic['cells'], int(panel['research_mask'].sum()) - len(dates))
        expected = support & panel['symbol'].ne('S000')
        self.assertEqual(diagnostic['support_sha256'], evo_probe._support_sha256(panel, support))
        self.assertTrue(panel.loc[support, 'symbol'].ne('S000').all())
        self.assertEqual(int(expected.sum()), diagnostic['cells'])

    def test_cross_harness_probe_uses_one_stock_level_support(self) -> None:
        panel = evo_probe.synthetic_panel(seed=5, dates=36, symbols=40)
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
        harness = evo_harness.default_harness(seed_features=evo_probe.PREFERRED_SEEDS[:6])
        harness['protocol']['budget_candidates'] = 6
        changed = copy.deepcopy(harness)
        panel_changed = panel.copy()
        panel_changed.loc[panel_changed['symbol'].eq('S001'), 'volatility_20'] = np.nan
        standalone = evo_probe.probe_round(harness, panel, dates, dates)
        reference, candidate = evo_probe.probe_rounds([harness, changed], panel_changed, dates, dates)
        self.assertEqual(reference['metrics']['common_support_sha256'],
                         candidate['metrics']['common_support_sha256'])
        self.assertEqual(reference['common_support']['key'], 'date+symbol')
        self.assertLess(reference['metrics']['common_cells'], standalone['metrics']['common_cells'])
        self.assertTrue(all(reference['metrics'][key] == candidate['metrics'][key]
                            for key in ('common_cells', 'common_dates', 'common_support_sha256',
                                        'fit_common_cells', 'fit_common_dates',
                                        'fit_common_support_sha256')))


class CrossoverRuleTests(unittest.TestCase):
    def test_signed_sum_applies_both_fit_directions(self) -> None:
        panel = pd.DataFrame({'date': pd.to_datetime(['2017-01-01', '2017-01-01'])})
        left = pd.Series([1.0, 2.0])
        right = pd.Series([3.0, 4.0])
        legacy = evo_probe._crossover(panel, left, right)
        signed = evo_probe._crossover(panel, left, right, rule='signed_rank_sum',
                                      direction_a=1.0, direction_b=-1.0)
        pd.testing.assert_series_equal(signed, legacy)
        positive = evo_probe._crossover(panel, left, right, rule='signed_rank_sum')
        self.assertEqual(positive.tolist(), [1.0, 2.0])

    def test_diversified_pair_selection_avoids_redundant_strong_pair(self) -> None:
        panel = evo_probe.synthetic_panel(seed=3)
        panel['volatility_20'] = panel['momentum_20']
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))[:70]
        harness = evo_harness.default_harness(seed_features=evo_probe.PREFERRED_SEEDS[:3])
        harness['protocol']['budget_candidates'] = 6
        harness['operators'] = ['residual_size']
        harness['skills']['crossover_pairs'] = 1
        harness['skills']['crossover_rule'] = 'signed_rank_sum'
        baseline = evo_probe.probe_round(harness, panel, dates, dates)
        diversified_harness = copy.deepcopy(harness)
        diversified_harness['skills']['pair_selection'] = 'diversified'
        diversified = evo_probe.probe_round(diversified_harness, panel, dates, dates)
        base_pair = next(item for item in baseline['candidates'] if item['kind'] == 'crossover')
        diversified_pair = next(item for item in diversified['candidates'] if item['kind'] == 'crossover')
        self.assertEqual(base_pair['operator'], 'signed_rank_sum')
        self.assertLess(diversified_pair['fit_pair_correlation'], base_pair['fit_pair_correlation'])
        self.assertNotEqual(diversified_pair['parents'], base_pair['parents'])

    def test_new_crossover_controls_are_editable(self) -> None:
        harness = evo_harness.default_harness(seed_features=['momentum_20', 'volatility_20'])
        for component, value in [('skills.crossover_rule', 'signed_rank_sum'),
                                 ('skills.pair_selection', 'diversified')]:
            edit = evo_harness.validate_proposal({'id': component, 'kind': 'INV', 'component': component,
                                                  'op': 'set', 'value': value, 'intent': 'test complementarity'})
            harness = evo_harness.apply_edit(harness, edit)
        self.assertEqual(harness['skills']['crossover_rule'], 'signed_rank_sum')
        self.assertEqual(harness['skills']['pair_selection'], 'diversified')
        with self.assertRaises(ValueError):
            evo_harness.validate_proposal({'id': 'bad', 'kind': 'INV', 'component': 'skills.crossover_rule',
                                           'op': 'set', 'value': 'unbounded_blend', 'intent': 'invalid'})


    def test_atomic_crossover_and_diversity_guard(self) -> None:
        harness = evo_harness.default_harness(seed_features=evo_probe.PREFERRED_SEEDS[:6])
        harness['skills']['crossover_pairs'] = 0
        edit = evo_harness.validate_proposal({
            'id': 'bundle', 'kind': 'INV', 'component': 'skills.crossover', 'op': 'configure',
            'value': {'pairs': 2, 'rule': 'signed_rank_sum', 'pair_selection': 'diversified'},
            'intent': 'test a complete complementary composition rather than an inert rule toggle'})
        enabled = evo_harness.apply_edit(harness, edit)
        self.assertEqual(enabled['skills']['crossover_pairs'], 2)
        self.assertEqual(enabled['skills']['crossover_rule'], 'signed_rank_sum')
        self.assertEqual(enabled['skills']['pair_selection'], 'diversified')
        enabled['protocol']['budget_candidates'] = 12
        panel = evo_probe.synthetic_panel(seed=3)
        dates = pd.DatetimeIndex(sorted(panel['date'].unique()))[:70]
        executed = evo_probe.probe_round(enabled, panel, dates, dates)
        crossovers = [item for item in executed['candidates'] if item['kind'] == 'crossover']
        self.assertEqual(len(crossovers), 2)
        self.assertTrue(all(item['operator'] == 'signed_rank_sum' for item in crossovers))
        self.assertTrue(all('fit_pair_correlation' in item for item in crossovers))
        with self.assertRaises(ValueError):
            evo_harness.validate_proposal({
                'id': 'bad_bundle', 'kind': 'INV', 'component': 'skills.crossover', 'op': 'configure',
                'value': {'pairs': 2, 'rule': 'signed_rank_sum', 'weight': 9},
                'intent': 'reject an unbounded weight'})
        reference = {'pass_count': 11, 'n_candidates': 12, 'pass_rate': 11 / 12,
                     'mean_abs_ic': 0.04, 'top_ic_mean': 0.06, 'icir_median': 0.3,
                     'icir_p25': 0.2, 'turnover_proxy': 0.7, 'novelty': 0.5,
                     'coverage': 1.0, 'duplicate_rate': 0.1}
        candidate = dict(reference, mean_abs_ic=0.05, novelty=0.4, duplicate_rate=0.2)
        probes = [{'metrics': candidate}]
        unguarded = RUNNER.compose(reference, probes, [edit])
        guarded = RUNNER.compose(reference, probes, [edit],
                                 policy={'min_delta_novelty': 0.0, 'max_delta_duplicate_rate': 0.0})
        self.assertEqual(len(unguarded['kept']), 1)
        self.assertTrue(guarded['keep_base'])
        self.assertEqual(guarded['decisions'][0]['guard_reject_reasons'],
                         ['novelty_below_composition_guard', 'duplicate_rate_above_composition_guard'])


class DevSplitTests(unittest.TestCase):
    def test_seeded_blocks_are_disjoint_and_reproducible(self) -> None:
        panel = evo_probe.synthetic_panel(seed=3)
        first = evo_probe.split_dev_dates(panel, 11)
        second = evo_probe.split_dev_dates(panel, 11)
        fresh = evo_probe.split_dev_dates(panel, 29)
        self.assertEqual(sum(len(part) for part in first), panel['date'].nunique())
        for left, right in zip(first, second):
            self.assertTrue(left.equals(right))
        self.assertFalse(first[0].equals(fresh[0]))
        for index, left in enumerate(first):
            for right in first[index + 1:]:
                self.assertTrue(left.intersection(right).empty)
        union = first[0].union(first[1]).union(first[2])
        self.assertEqual(len(union), panel['date'].nunique())

    def test_inclusive_end_day_filter(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dates = pd.to_datetime(['2017-01-01', '2020-11-30 15:00:00', '2020-12-01'], format='mixed')
            for string_dates in (False, True):
                values = dates.strftime('%Y-%m-%d %H:%M:%S') if string_dates else dates
                frame = pd.DataFrame({'date': values, 'symbol': ['a', 'b', 'c'],
                                      'forward_return': [0.1, 0.2, 0.3]})
                frame.to_parquet(root / 'part-0000.parquet', index=False)
                panel = evo_probe.load_panel(root, ['forward_return'], '2017-01-01', '2020-11-30')
                self.assertEqual(panel['forward_return'].tolist(), [0.1, 0.2], string_dates)


class TraceContractTests(unittest.TestCase):
    def test_trace_is_exposed_without_operator_example_or_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            trace_path = root / 'trace.json'
            trace_path.write_text(json.dumps(example_trace()), encoding='utf-8')
            loaded = RUNNER._load_execution_trace(trace_path)
            self.assertEqual(loaded['source_run'], 'R1_TEST')
            harness = evo_harness.default_harness(seed_features=['momentum_20', 'volatility_20'])
            reference = {'probe': {'metrics': {'pass_count': 1}, 'candidates': []},
                         'select': {'metrics': {'secret_metric': 123}}}
            RUNNER.write_prompt_pack(root / 'run', harness, reference, [], 'price_volume',
                                     'diagnostic', loaded)
            generation = root / 'run/generation'
            self.assertIn('execution_trace.json', (generation / 'README_CN.md').read_text())
            self.assertNotIn('"rank"', (generation / 'prompt.txt').read_text())
            self.assertNotIn('secret_metric', (generation / 'execution_trace.json').read_text())
            for key in ('select_metrics', 'forward_return', 'dev_holdout',
                        'child_result', 'childSelect', 'devHoldout', 'unexpected'):
                invalid = copy.deepcopy(example_trace())
                invalid['proposals'][0][key] = 123
                trace_path.write_text(json.dumps(invalid), encoding='utf-8')
                with self.assertRaises(evo_probe.GuardrailError):
                    RUNNER._load_execution_trace(trace_path)
            invalid = example_trace()
            invalid['proposals'][0]['edit']['child_result'] = {'mean_abs_ic': 9}
            trace_path.write_text(json.dumps(invalid), encoding='utf-8')
            with self.assertRaises(evo_probe.GuardrailError):
                RUNNER._load_execution_trace(trace_path)
            invalid = example_trace()
            invalid['proposals'] = ['not-a-proposal']
            trace_path.write_text(json.dumps(invalid), encoding='utf-8')
            with self.assertRaises(evo_probe.GuardrailError):
                RUNNER._load_execution_trace(trace_path)


class SummaryContractTests(unittest.TestCase):
    def test_export_only_preserves_input_trace_and_existing_result(self) -> None:
        spec = importlib.util.spec_from_file_location(
            'research_step_summary', ROOT / 'auto_research/scripts/summarize_research_step.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as raw:
            run = Path(raw) / 'run'
            (run / 'generation').mkdir(parents=True)
            reference = {'mean_abs_ic': 0.1, 'max_date_read': '2020-11-27'}
            candidate = {'mean_abs_ic': 0.2, 'max_date_read': '2020-11-27'}
            final_reference = {'mean_abs_ic': 0.15, 'max_date_read': '2020-11-27'}
            decision = {'edit': 'e1', 'kind': 'INV', 'component': 'skills.crossover_pairs',
                        'intent': 'test', 'delta': {}, 'support_reason': 'mean_abs_ic', 'verdict': 'kept'}
            record = {
                'run_id': 'r2', 'proposer': 'llm', 'prompt_feedback': 'diagnostic', 'model_id': 'test',
                'seed': 11, 'seed_semantics': 'test', 'trace_file': 'previous/execution_trace.json',
                'proposal_order': ['e1'],
                'edits': [{'id': 'e1', 'kind': 'INV', 'component': 'skills.crossover_pairs',
                           'op': 'set', 'value': 0, 'intent': 'test'}],
                'base_harness': {'operators': ['residual_size'], 'skills': {'crossover_pairs': 2}},
                'reference': {'probe': {'metrics': reference, 'candidates': []},
                              'select': {'metrics': reference, 'candidates': []}},
                'candidates': [{'edit': 'e1', 'metrics': candidate, 'candidates': []}],
                'composition': {'decisions': [decision], 'dropped': []},
                'adopted_probe': {'metrics': candidate}, 'adopted_select': {'metrics': candidate},
                'adopted_reference': {'probe': {'metrics': final_reference},
                                      'select': {'metrics': final_reference}},
            }
            manifest = {'status': 'COMPLETED', 'base_harness_hash': 'base', 'adopted_harness_hash': 'adopted',
                        'kept_edits': ['e1'], 'dev_window': ['2017-01-01', '2020-11-30'],
                        'command': 'test', 'runtime_seconds': 1.0, 'reflection_status': 'NO_ENTRY'}
            (run / 'step_record.json').write_text(json.dumps(record), encoding='utf-8')
            (run / 'run_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            input_trace = run / 'generation/execution_trace.json'
            input_trace.write_text('ORIGINAL_INPUT', encoding='utf-8')
            module.summarize(run)
            result_bytes = (run / 'result.json').read_bytes()
            exported = module.summarize(run, export_trace=True, export_only=True)
            self.assertEqual(input_trace.read_text(encoding='utf-8'), 'ORIGINAL_INPUT')
            self.assertEqual((run / 'result.json').read_bytes(), result_bytes)
            self.assertEqual(exported['trace_export'], 'generation/next_execution_trace.json')
            self.assertEqual(exported['primary']['reference'], 0.15)
            next_trace = (run / 'generation/next_execution_trace.json').read_text(encoding='utf-8')
            trace_payload = json.loads(next_trace)
            self.assertEqual(trace_payload['reference_probe_metrics']['mean_abs_ic'], 0.1)
            self.assertEqual(trace_payload['source_run'], 'r2')
            self.assertNotIn('adopted_select', next_trace)


class RunnerIntegrationTests(unittest.TestCase):
    def test_fixed_harness_uses_disjoint_seeded_splits(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            splits = []
            for seed in (11, 29):
                output = Path(raw) / str(seed)
                result = subprocess.run([
                    sys.executable, str(SCRIPT), '--demo', '--evaluate-only', '--dev-split',
                    'seeded-year-blocks', '--seed', str(seed), '--budget', '12',
                    '--coverage-log-only', '--output', str(output)],
                    capture_output=True, text=True, timeout=300)
                self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                manifest = json.loads((output / 'run_manifest.json').read_text())
                record = json.loads((output / 'step_record.json').read_text())
                split = json.loads((output / 'dev_split.json').read_text())
                self.assertEqual(manifest['proposer'], 'fixed')
                self.assertEqual(manifest['model_id'], 'none')
                self.assertEqual(record['candidates'], [])
                self.assertEqual(manifest['base_harness_hash'], manifest['adopted_harness_hash'])
                sets = [set(split[key]) for key in ('fit', 'probe', 'select')]
                self.assertTrue(sets[0].isdisjoint(sets[1]))
                self.assertTrue(sets[0].isdisjoint(sets[2]))
                self.assertTrue(sets[1].isdisjoint(sets[2]))
                splits.append(split)
            self.assertNotEqual(splits[0]['fit'], splits[1]['fit'])

    def test_fixed_pair_compare_uses_shared_probe_and_select_support(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            base = evo_harness.default_harness(seed_features=evo_probe.PREFERRED_SEEDS[:6])
            alternate = copy.deepcopy(base)
            alternate['skills']['crossover_pairs'] = 0
            base_path = root / 'base.json'
            alternate_path = root / 'alternate.json'
            base_path.write_text(json.dumps(base), encoding='utf-8')
            alternate_path.write_text(json.dumps(alternate), encoding='utf-8')
            output = root / 'run'
            result = subprocess.run([
                sys.executable, str(SCRIPT), '--demo', '--evaluate-only',
                '--harness', str(base_path), '--compare-harness', str(alternate_path),
                '--dev-split', 'seeded-year-blocks', '--seed', '11', '--budget', '6',
                '--coverage-log-only', '--disable-reflection-write', '--output', str(output)],
                capture_output=True, text=True, timeout=300)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            record = json.loads((output / 'step_record.json').read_text())
            manifest = json.loads((output / 'run_manifest.json').read_text())
            self.assertEqual(record['candidates'], [])
            self.assertNotEqual(manifest['base_harness_hash'], manifest['adopted_harness_hash'])
            self.assertEqual(manifest['compare_harness_hash'], manifest['adopted_harness_hash'])
            for split in ('probe', 'select'):
                self.assertEqual(record['reference'][split]['metrics']['common_support_sha256'],
                                 record[f'adopted_{split}']['metrics']['common_support_sha256'])

    def test_compose_and_select_share_support_in_parallel_and_sequential(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            for mode in ('parallel', 'sequential'):
                with self.subTest(mode=mode):
                    output = Path(raw) / mode
                    result = subprocess.run([
                        sys.executable, str(SCRIPT), '--demo', '--budget', '6', '--max-proposals', '2',
                        '--mode', mode, '--k', '2', '--coverage-log-only', '--disable-reflection-write',
                        '--output', str(output)], capture_output=True, text=True, timeout=300)
                    self.assertEqual(result.returncode, 0, result.stderr[-2000:])
                    record = json.loads((output / 'step_record.json').read_text())
                    probe_hashes = {item['metrics']['common_support_sha256']
                                    for item in [record['reference']['probe'], *record['candidates']]}
                    self.assertEqual(len(probe_hashes), 1)
                    for composition in [record['composition'],
                                        *[item['composition'] for item in record['compositions']]]:
                        for decision in composition['decisions']:
                            evidence = decision['evidence']
                            self.assertEqual(evidence['base_common_support_sha256'],
                                             evidence['candidate_common_support_sha256'])
                    select_hashes = {record['reference']['select']['metrics']['common_support_sha256'],
                                     record['adopted_select']['metrics']['common_support_sha256'],
                                     *[item['select_metrics']['common_support_sha256']
                                       for item in record['compositions']]}
                    self.assertEqual(len(select_hashes), 1)
                    self.assertEqual(
                        record['adopted_reference']['probe']['metrics']['common_support_sha256'],
                        record['adopted_probe']['metrics']['common_support_sha256'])
                    self.assertGreater(record['adopted_select']['metrics']['common_cells'], 0)

    def test_trace_prepare_and_resume_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / 'run'
            trace = Path(raw) / 'trace.json'
            trace.write_text(json.dumps(example_trace()), encoding='utf-8')
            base = [sys.executable, str(SCRIPT), '--demo', '--budget', '12', '--seed', '11',
                    '--dev-split', 'seeded-year-blocks', '--coverage-log-only', '--proposer', 'llm',
                    '--prompt-feedback', 'diagnostic', '--model-id', 'test-model',
                    '--trace-file', str(trace), '--research-question', 'bounded complementarity test',
                    '--output', str(output)]
            prepared = subprocess.run(base + ['--prepare'], capture_output=True, text=True, timeout=300)
            self.assertEqual(prepared.returncode, 0, prepared.stderr[-2000:])
            edits = [
                {'id': 'remove_weak', 'kind': 'INV', 'component': 'operators.remove', 'op': 'remove',
                 'value': ['delta4', 'delta13'], 'intent': 'focus bounded search on tested operators'},
                {'id': 'mutation_depth', 'kind': 'INV', 'component': 'skills.mutation_per_seed',
                 'op': 'set', 'value': 2, 'intent': 'spend released slots on a second operator per seed'},
                {'id': 'add_rank', 'kind': 'INV', 'component': 'operators', 'op': 'append',
                 'value': 'rank', 'intent': 'test cross-sectional normalization after budget repair'},
            ]
            edits_path = output / 'generation/edits.json'
            edits_path.write_text(json.dumps(edits), encoding='utf-8')
            wrong = subprocess.run(base + ['--edits', str(edits_path), '--research-question', 'changed'],
                                   capture_output=True, text=True, timeout=300)
            self.assertEqual(wrong.returncode, 2, wrong.stderr[-2000:])
            self.assertIn('research_question', wrong.stderr)
            completed = subprocess.run(base + ['--edits', str(edits_path)],
                                       capture_output=True, text=True, timeout=300)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            manifest = json.loads((output / 'run_manifest.json').read_text())
            record = json.loads((output / 'step_record.json').read_text())
            self.assertEqual(manifest['status'], 'COMPLETED')
            self.assertEqual(manifest['trace_file_sha256'], record['trace_file_sha256'])
            self.assertEqual(record['proposal_order'], ['remove_weak', 'mutation_depth', 'add_rank'])
            self.assertEqual(manifest['seed_semantics'], 'seeded_year_blocks_v1')
            self.assertEqual(manifest['research_question'], 'bounded complementarity test')

    def test_wrong_seed_or_budget_resume_preserves_every_existing_byte(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / 'run'
            base = [sys.executable, str(SCRIPT), '--demo', '--budget', '6', '--seed', '11',
                    '--dev-split', 'seeded-year-blocks', '--coverage-log-only', '--proposer', 'llm',
                    '--prompt-feedback', 'diagnostic', '--model-id', 'test-model',
                    '--output', str(output)]
            prepared = subprocess.run(base + ['--prepare'], capture_output=True, text=True, timeout=300)
            self.assertEqual(prepared.returncode, 0, prepared.stderr[-2000:])
            edits_path = output / 'generation/edits.json'
            edits_path.write_text(json.dumps([
                {'id': 'llm_rank', 'kind': 'INV', 'component': 'operators',
                 'op': 'append', 'value': 'rank', 'intent': 'test normalization'},
                {'id': 'llm_zscore', 'kind': 'INV', 'component': 'operators',
                 'op': 'append', 'value': 'zscore', 'intent': 'test scaling'}]), encoding='utf-8')

            def snapshot() -> dict:
                return {str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in output.rglob('*') if path.is_file()}

            for flag, value, expected in (('--seed', '12', 'seed'),
                                          ('--budget', '8', 'budget_candidates')):
                with self.subTest(flag=flag):
                    before = snapshot()
                    wrong_command = list(base)
                    wrong_command[wrong_command.index(flag) + 1] = value
                    wrong = subprocess.run(wrong_command + ['--edits', str(edits_path)],
                                           capture_output=True, text=True, timeout=30)
                    self.assertEqual(wrong.returncode, 2, wrong.stderr[-2000:])
                    self.assertIn(expected, wrong.stderr)
                    self.assertIn('identity mismatch', wrong.stderr)
                    self.assertEqual(snapshot(), before)

    def test_legacy_prepared_manifest_defaults_to_reflection_write(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / 'legacy'
            base = [sys.executable, str(SCRIPT), '--demo', '--budget', '12', '--seed', '11',
                    '--dev-split', 'seeded-year-blocks', '--coverage-log-only', '--proposer', 'llm',
                    '--prompt-feedback', 'diagnostic', '--model-id', 'test-model',
                    '--output', str(output)]
            prepared = subprocess.run(base + ['--prepare'], capture_output=True, text=True, timeout=300)
            self.assertEqual(prepared.returncode, 0, prepared.stderr[-2000:])
            manifest_path = output / 'run_manifest.json'
            manifest = json.loads(manifest_path.read_text())
            self.assertTrue(manifest.pop('reflection_write'))
            manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
            edits_path = output / 'generation/edits.json'
            edits_path.write_text(json.dumps([
                {'id': 'llm_rank', 'kind': 'INV', 'component': 'operators',
                 'op': 'append', 'value': 'rank', 'intent': 'test normalization'},
                {'id': 'llm_zscore', 'kind': 'INV', 'component': 'operators',
                 'op': 'append', 'value': 'zscore', 'intent': 'test scaling'}]), encoding='utf-8')
            completed = subprocess.run(base + ['--edits', str(edits_path)],
                                       capture_output=True, text=True, timeout=300)
            self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
            manifest = json.loads(manifest_path.read_text())
            self.assertTrue(manifest['reflection_write'])
            record = json.loads((output / 'step_record.json').read_text())
            self.assertNotEqual(record['reflection'].get('reason'),
                                'disabled_by_outer_route; see outer route_history')


if __name__ == '__main__':
    unittest.main()
