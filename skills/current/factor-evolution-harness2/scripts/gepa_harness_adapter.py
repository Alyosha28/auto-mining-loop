#!/usr/bin/env python3
"""GEPA adapter for prompt-level factor research harness evolution.

Candidate text is injected into the existing two-phase proposer prompt. Candidate
scores come from frozen common-support probe comparisons; select outcomes are not
read by this adapter or included in reflective datasets.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from gepa import EvaluationBatch, GEPAAdapter

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_probe import GuardrailError
from research_contract import canonical_hash, validate_usage
from run_evo_step import load_llm_edits
from run_research_loop import absolute, file_hash, relative, write_json_new
from trace_contract import reject_sensitive_fields

EVO_STEP = SCRIPTS / 'run_evo_step.py'
COMPARISON = SCRIPTS / 'compare_harnesses.py'
SUMMARIZER = ROOT / 'auto_research/scripts/summarize_research_step.py'
DATA_FIELDS = {'data_id', 'harness', 'trace_file', 'contract', 'research_question',
               'seed', 'budget', 'dev_split', 'features', 'demo', 'seed_panel'}
INVALID_SCORE = -1000000.0


class HarnessGEPAAdapter(GEPAAdapter):
    def __init__(self, output_root: Path, proposer: Path, model_id: str,
                 proposer_budget: dict, proposer_timeout_seconds: int = 300,
                 execution_timeout_seconds: int = 300,
                 comparison_timeout_seconds: int = 1800):
        self.output_root = absolute(output_root)
        self.proposer = absolute(proposer)
        self.model_id = model_id
        self.proposer_budget = self._validate_budget(proposer_budget)
        self.proposer_timeout_seconds = self._positive_timeout(proposer_timeout_seconds)
        self.execution_timeout_seconds = self._positive_timeout(execution_timeout_seconds)
        self.comparison_timeout_seconds = self._positive_timeout(comparison_timeout_seconds)
        if not self.proposer.is_file() or not os.access(self.proposer, os.X_OK):
            raise GuardrailError('GEPA proposer must be an executable file')
        self.usage = {'model_calls': 0, 'input_tokens': 0, 'output_tokens': 0}
        self.output_root.mkdir(parents=True, exist_ok=True)
        usage_path = self.output_root / 'adapter_usage.json'
        if usage_path.is_file():
            persisted = json.loads(usage_path.read_text(encoding='utf-8'))
            if (not isinstance(persisted, dict)
                    or persisted.get('schema') != 'AUTO-RESEARCH-GEPA-USAGE-1'
                    or not isinstance(persisted.get('usage'), dict)
                    or set(persisted['usage']) != set(self.usage)):
                raise GuardrailError('invalid persisted GEPA adapter usage')
            self.set_adapter_state({'usage': persisted['usage']})

    def _persist_usage(self) -> None:
        path = self.output_root / 'adapter_usage.json'
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps({
            'schema': 'AUTO-RESEARCH-GEPA-USAGE-1', 'usage': self.usage,
        }, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        temporary.replace(path)

    @staticmethod
    def _positive_timeout(value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise GuardrailError('GEPA timeouts must be positive integers')
        return value

    @staticmethod
    def _validate_budget(payload: dict) -> dict:
        if not isinstance(payload, dict) or set(payload) != {
                'max_model_calls', 'max_input_tokens', 'max_output_tokens'}:
            raise GuardrailError('GEPA proposer budget needs calls, input tokens and output tokens')
        result = {}
        for key, value in payload.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise GuardrailError(f'GEPA proposer budget {key} must be positive')
            result[key] = value
        return result

    def get_adapter_state(self) -> dict[str, Any]:
        return {'usage': dict(self.usage)}

    def set_adapter_state(self, state: dict[str, Any]) -> None:
        if state == {}:
            return
        usage = state.get('usage')
        if not isinstance(usage, dict) or set(usage) != set(self.usage):
            raise GuardrailError('invalid GEPA adapter usage state')
        for key in self.usage:
            if isinstance(usage[key], bool) or not isinstance(usage[key], int) or usage[key] < 0:
                raise GuardrailError('invalid GEPA adapter usage counters')
        self.usage = dict(usage)
        self._persist_usage()

    @staticmethod
    def _candidate(candidate: dict[str, str]) -> tuple[str, str]:
        if not isinstance(candidate, dict) or set(candidate) != {'proposer_guidance'}:
            raise GuardrailError('GEPA candidates must contain only proposer_guidance')
        guidance = candidate['proposer_guidance']
        if not isinstance(guidance, str) or len(guidance) > 20000:
            raise GuardrailError('proposer_guidance must be text with at most 20000 characters')
        if guidance != guidance.strip():
            raise GuardrailError('proposer_guidance must not have surrounding whitespace')
        return canonical_hash(candidate), guidance

    @staticmethod
    def _data(data: dict) -> dict:
        if not isinstance(data, dict) or set(data) != DATA_FIELDS:
            raise GuardrailError(f'GEPA data fields must be exactly {sorted(DATA_FIELDS)}')
        if not re.fullmatch(r'[A-Za-z0-9_.-]+', data['data_id']):
            raise GuardrailError('GEPA data_id must be a simple unique name')
        if not isinstance(data['research_question'], str) or not data['research_question'].strip():
            raise GuardrailError('GEPA data needs a research question')
        if isinstance(data['seed'], bool) or not isinstance(data['seed'], int):
            raise GuardrailError('GEPA data seed must be an integer')
        if (isinstance(data['budget'], bool) or not isinstance(data['budget'], int)
                or data['budget'] < 1):
            raise GuardrailError('GEPA data budget must be positive')
        if data['dev_split'] not in ('legacy', 'seeded-year-blocks') or not isinstance(data['demo'], bool):
            raise GuardrailError('GEPA data has an invalid split or demo flag')
        return data

    def _run(self, command: list[str], timeout: int, log_stem: Path) -> None:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        write_json_new(log_stem.with_suffix('.command.json'), command)
        log_stem.with_suffix('.stdout.log').write_text(completed.stdout or '', encoding='utf-8')
        log_stem.with_suffix('.stderr.log').write_text(completed.stderr or '', encoding='utf-8')
        if completed.returncode != 0:
            raise GuardrailError(f'command failed with exit {completed.returncode}: {log_stem}')

    def _evo_command(self, data: dict, run_dir: Path, guidance_path: Path,
                     phase: str) -> list[str]:
        harness_path = absolute(data['harness'])
        harness = json.loads(harness_path.read_text(encoding='utf-8'))
        protocol = harness['protocol']
        command = [
            sys.executable, str(EVO_STEP), '--output', str(run_dir), '--run-id', run_dir.name,
            '--domain', harness.get('domain', 'price_volume'), '--harness', str(harness_path),
            '--proposer', 'llm', '--prompt-feedback', 'diagnostic', '--model-id', self.model_id,
            '--trace-file', str(absolute(data['trace_file'])),
            '--research-question', data['research_question'], '--seed', str(data['seed']),
            '--budget', str(data['budget']), '--dev-split', data['dev_split'],
            '--dev-start', protocol['dev_start'], '--dev-end', protocol['dev_end'],
            '--k', '1', '--disable-reflection-write', '--coverage-log-only',
        ]
        if data['features']:
            command += ['--features', data['features']]
        if data['seed_panel']:
            command += ['--seed-panel', str(absolute(data['seed_panel']))]
        if data['demo']:
            command.append('--demo')
        if guidance_path.stat().st_size:
            command += ['--prompt-guidance-file', str(guidance_path)]
        if phase == 'PREPARE':
            command.append('--prepare')
        else:
            command += ['--edits', str(run_dir / 'generation/edits.json')]
        return command

    def _charge_usage(self, usage_path: Path) -> dict:
        record = validate_usage(json.loads(usage_path.read_text(encoding='utf-8')), self.model_id)
        updated = {
            'model_calls': self.usage['model_calls'] + record['calls'],
            'input_tokens': self.usage['input_tokens'] + record['input_tokens'],
            'output_tokens': self.usage['output_tokens'] + record['output_tokens'],
        }
        self.usage = updated
        self._persist_usage()
        for key, budget_key in (('model_calls', 'max_model_calls'),
                                ('input_tokens', 'max_input_tokens'),
                                ('output_tokens', 'max_output_tokens')):
            if updated[key] > self.proposer_budget[budget_key]:
                raise GuardrailError(f'GEPA proposer budget exhausted: {key}')
        return record

    def _generate(self, data: dict, candidate: dict[str, str]) -> dict:
        candidate_key, guidance = self._candidate(candidate)
        data = self._data(data)
        run_dir = self.output_root / 'generations' / data['data_id'] / candidate_key
        metadata_path = run_dir / 'generation_result.json'
        if metadata_path.is_file():
            return json.loads(metadata_path.read_text(encoding='utf-8'))
        if run_dir.exists():
            raise GuardrailError(f'partial GEPA generation must be preserved and inspected: {run_dir}')
        run_dir.mkdir(parents=True)
        guidance_path = run_dir / 'proposer_guidance.txt'
        guidance_path.write_text(guidance, encoding='utf-8')
        self._run(self._evo_command(data, run_dir, guidance_path, 'PREPARE'),
                  self.execution_timeout_seconds, run_dir / 'prepare')
        generation = run_dir / 'generation'
        environment = os.environ.copy()
        environment.update({
            'AUTO_RESEARCH_RUN_ID': run_dir.name,
            'AUTO_RESEARCH_GENERATION': str(generation),
            'AUTO_RESEARCH_PROPOSAL': str(generation / 'edits.json'),
            'AUTO_RESEARCH_USAGE': str(generation / 'model_usage.json'),
            'AUTO_RESEARCH_MODE': 'local-only',
            'AUTO_RESEARCH_MODEL_ID': self.model_id,
        })
        attempt = generation / 'proposer_attempt.json'
        write_json_new(attempt, {'candidate_sha256': candidate_key,
                                 'proposer_sha256': file_hash(self.proposer)})
        completed = subprocess.run([self.proposer], capture_output=True, text=True,
                                   timeout=self.proposer_timeout_seconds, env=environment)
        (generation / 'proposer_stdout.log').write_text(completed.stdout or '', encoding='utf-8')
        (generation / 'proposer_stderr.log').write_text(completed.stderr or '', encoding='utf-8')
        usage_path = generation / 'model_usage.json'
        if completed.returncode != 0:
            if usage_path.is_file():
                self._charge_usage(usage_path)
            raise GuardrailError(f'GEPA proposer failed with exit {completed.returncode}: {run_dir}')
        if not (generation / 'edits.json').is_file() or not usage_path.is_file():
            if usage_path.is_file():
                self._charge_usage(usage_path)
            raise GuardrailError('GEPA proposer did not write edits and model usage')
        usage = self._charge_usage(usage_path)
        try:
            load_llm_edits(generation / 'edits.json')
        except ValueError as error:
            metadata = {
                'schema': 'AUTO-RESEARCH-GEPA-GENERATION-1', 'status': 'INVALID_PROPOSAL',
                'candidate_sha256': candidate_key, 'data_id': data['data_id'],
                'run_dir': relative(run_dir), 'adopted_harness': None, 'next_trace': None,
                'reference_probe_metrics': None, 'adopted_probe_metrics': None,
                'model_usage': usage, 'error': str(error), 'metric_available': False,
                'score_window_read': False,
            }
            reject_sensitive_fields(metadata)
            write_json_new(metadata_path, metadata)
            return metadata
        self._run(self._evo_command(data, run_dir, guidance_path, 'EXECUTE'),
                  self.execution_timeout_seconds, run_dir / 'execute')
        self._run([sys.executable, str(SUMMARIZER), str(run_dir), '--export-trace'],
                  90, run_dir / 'summarize')
        record = json.loads((run_dir / 'step_record.json').read_text(encoding='utf-8'))
        metadata = {
            'schema': 'AUTO-RESEARCH-GEPA-GENERATION-1',
            'status': 'COMPLETED', 'metric_available': True,
            'candidate_sha256': candidate_key, 'data_id': data['data_id'],
            'run_dir': relative(run_dir), 'adopted_harness': relative(run_dir / 'harness/adopted.json'),
            'next_trace': relative(run_dir / 'generation/next_execution_trace.json'),
            'reference_probe_metrics': record['adopted_reference']['probe']['metrics'],
            'adopted_probe_metrics': record['adopted_probe']['metrics'],
            'model_usage': usage, 'score_window_read': False,
        }
        reject_sensitive_fields(metadata)
        write_json_new(metadata_path, metadata)
        return metadata

    def _compare(self, data: dict, candidates: dict[str, dict[str, str]]) -> tuple[dict, dict[str, str], dict]:
        data = self._data(data)
        group_identity = {'data_id': data['data_id'], 'candidates': sorted(candidates)}
        group_key = canonical_hash(group_identity)
        output = self.output_root / 'comparisons' / data['data_id'] / group_key
        result_path = output / 'result.json'
        names = {key: f'c{index:04d}' for index, key in enumerate(sorted(candidates))}
        if result_path.is_file():
            generated = {key: self._generate(data, candidate) for key, candidate in candidates.items()}
            return json.loads(result_path.read_text(encoding='utf-8')), names, generated
        if output.exists():
            raise GuardrailError(f'partial GEPA comparison must be preserved and inspected: {output}')
        generated = {key: self._generate(data, candidate) for key, candidate in candidates.items()}
        valid = {key: value for key, value in generated.items() if value.get('status') == 'COMPLETED'}
        if not valid:
            empty = {'arms': {'base': {'harness': str(absolute(data['harness']))}}, 'comparisons': {}}
            return empty, names, generated
        command = [sys.executable, str(COMPARISON), '--contract', str(absolute(data['contract'])),
                   '--output', str(output), '--seed', str(data['seed']),
                   '--budget', str(data['budget']), '--dev-split', data['dev_split'],
                   '--coverage-log-only', '--probe-only']
        if data['features']:
            command += ['--features', data['features']]
        if data['seed_panel']:
            command += ['--seed-panel', str(absolute(data['seed_panel']))]
        if data['demo']:
            command.append('--demo')
        command += ['--arm', f'base={absolute(data["harness"])}']
        for key in sorted(valid):
            command += ['--arm', f'{names[key]}={absolute(generated[key]["adopted_harness"])}']
        self._run(command, self.comparison_timeout_seconds,
                  self.output_root / 'comparison_logs' / data['data_id'] / group_key)
        return json.loads(result_path.read_text(encoding='utf-8')), names, generated

    def _evaluate_items(self, items: list[tuple[dict[str, str], list[dict]]],
                        capture_traces: bool) -> list[EvaluationBatch]:
        by_data: dict[str, dict] = {}
        candidates_by_data: dict[str, dict[str, dict[str, str]]] = {}
        for _candidate, batch in items:
            for data in batch:
                data = self._data(data)
                key, _guidance = self._candidate(_candidate)
                if data['data_id'] in by_data and by_data[data['data_id']] != data:
                    raise GuardrailError(f'GEPA data_id has conflicting configurations: {data["data_id"]}')
                by_data[data['data_id']] = data
                candidates_by_data.setdefault(data['data_id'], {})[key] = _candidate
        evaluated = {}
        for data_id, candidates in candidates_by_data.items():
            data = by_data[data_id]
            comparison, names, generated = self._compare(data, candidates)
            for key, candidate in candidates.items():
                name = names[key]
                generation = generated[key]
                if generation.get('status') != 'COMPLETED':
                    score = INVALID_SCORE
                    outcome = {'status': generation.get('status', 'FAILED'),
                               'error': generation.get('error'), 'metric_available': False}
                    output = {'data_id': data_id, 'candidate_sha256': key,
                              'status': generation.get('status', 'FAILED'),
                              'error': generation.get('error'), 'probe_delta': None}
                    objectives = {metric: INVALID_SCORE for metric in (
                        'mean_abs_ic_delta', 'coverage_delta', 'novelty_delta',
                        'duplicate_rate_delta', 'turnover_proxy_delta')}
                else:
                    outcome = comparison['comparisons'][name]['probe']
                    delta = outcome['delta']
                    score = float(outcome['primary_delta'])
                    output = {
                        'data_id': data_id, 'candidate_sha256': key,
                        'adopted_harness': comparison['arms'][name]['harness'],
                        'probe_delta': delta,
                    }
                    objectives = {
                        'mean_abs_ic_delta': score,
                        'coverage_delta': float(delta['delta_coverage']),
                        'novelty_delta': float(delta['delta_novelty']),
                        'duplicate_rate_delta': -float(delta['delta_duplicate_rate']),
                        'turnover_proxy_delta': -float(delta['delta_turnover_proxy']),
                    }
                trajectory = None
                if capture_traces:
                    trajectory = {
                        'research_question': data['research_question'],
                        'proposer_guidance': candidate['proposer_guidance'],
                        'edits': json.loads((absolute(generation['run_dir']) /
                                             'generation/edits.json').read_text(encoding='utf-8')),
                        'model_usage': generation['model_usage'],
                        'reference_probe_metrics': generation['reference_probe_metrics'],
                        'adopted_probe_metrics': generation['adopted_probe_metrics'],
                        'comparison': outcome,
                    }
                    reject_sensitive_fields(trajectory)
                evaluated[(key, data_id)] = (score, output, trajectory, objectives)
        batches = []
        for candidate, batch in items:
            candidate_key, _guidance = self._candidate(candidate)
            scores, outputs, trajectories, objectives = [], [], [], []
            for data in batch:
                score, output, trajectory, objective = evaluated[(candidate_key, data['data_id'])]
                scores.append(score)
                outputs.append(output)
                objectives.append(objective)
                if capture_traces:
                    trajectories.append(trajectory)
            batches.append(EvaluationBatch(
                outputs=outputs, scores=scores,
                trajectories=trajectories if capture_traces else None,
                objective_scores=objectives, num_metric_calls=len(batch)))
        return batches

    def evaluate(self, batch: list[dict], candidate: dict[str, str],
                 capture_traces: bool = False) -> EvaluationBatch:
        return self._evaluate_items([(candidate, batch)], capture_traces)[0]

    def batch_evaluate(self, items: list[tuple[dict[str, str], list[dict]]], *,
                       capture_traces: bool = True) -> list[EvaluationBatch]:
        return self._evaluate_items(items, capture_traces)

    def make_reflective_dataset(self, candidate: dict[str, str], eval_batch: EvaluationBatch,
                                components_to_update: list[str]) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        self._candidate(candidate)
        if eval_batch.trajectories is None:
            raise GuardrailError('reflective datasets require captured trajectories')
        datasets = {}
        for component in components_to_update:
            if component != 'proposer_guidance':
                raise GuardrailError(f'unsupported reflective component: {component}')
            rows = []
            for trajectory, score in zip(eval_batch.trajectories, eval_batch.scores):
                rows.append({
                    'Inputs': {'research_question': trajectory['research_question'],
                               'proposer_guidance': trajectory['proposer_guidance']},
                    'Generated Outputs': {'edits': trajectory['edits']},
                    'Feedback': json.dumps({
                        'score': score,
                        'model_usage': trajectory['model_usage'],
                        'reference_probe_metrics': trajectory['reference_probe_metrics'],
                        'adopted_probe_metrics': trajectory['adopted_probe_metrics'],
                        'comparison': trajectory['comparison'],
                    }, ensure_ascii=False),
                })
            reject_sensitive_fields(rows)
            datasets[component] = rows
        return datasets
