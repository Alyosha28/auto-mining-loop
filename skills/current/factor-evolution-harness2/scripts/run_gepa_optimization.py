#!/usr/bin/env python3
"""Run bounded GEPA optimization over sanitized factor-harness proposer guidance."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from gepa import optimize

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_probe import GuardrailError
from gepa_harness_adapter import HarnessGEPAAdapter
from claude_proposer_command import ClaudeCLIReflection
from proposer_command import BudgetedReflection
from research_contract import canonical_hash
from run_research_loop import absolute, write_json_new
from trace_contract import reject_sensitive_fields

CONFIG_SCHEMA = 'AUTO-RESEARCH-GEPA-CONFIG-1'
CONFIG_FIELDS = {'schema', 'seed_candidate', 'trainset', 'valset', 'proposer', 'model_id',
                 'proposer_backend', 'proposer_budget', 'reflection_budget', 'reflection_minibatch_size',
                 'max_metric_calls', 'seed', 'proposer_timeout_seconds',
                 'execution_timeout_seconds', 'comparison_timeout_seconds'}


def positive_integer(payload: dict, key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise GuardrailError(f'{key} must be a positive integer')
    return value


def load_config(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(payload, dict) or payload.get('schema') != CONFIG_SCHEMA:
        raise GuardrailError(f'GEPA config must use {CONFIG_SCHEMA}')
    if set(payload) != CONFIG_FIELDS:
        raise GuardrailError(f'GEPA config fields must be exactly {sorted(CONFIG_FIELDS)}')
    if not isinstance(payload['seed_candidate'], dict) or set(payload['seed_candidate']) != {
            'proposer_guidance'}:
        raise GuardrailError('GEPA seed candidate must contain only proposer_guidance')
    if not isinstance(payload['seed_candidate']['proposer_guidance'], str):
        raise GuardrailError('GEPA seed guidance must be text')
    for key in ('trainset', 'valset'):
        if not isinstance(payload[key], list) or not payload[key]:
            raise GuardrailError(f'GEPA {key} must be a non-empty list')
    train_ids = {item.get('data_id') for item in payload['trainset']}
    val_ids = {item.get('data_id') for item in payload['valset']}
    if train_ids & val_ids:
        raise GuardrailError('GEPA train and validation data ids must be disjoint')
    for key in ('reflection_minibatch_size', 'max_metric_calls', 'seed',
                'proposer_timeout_seconds', 'execution_timeout_seconds', 'comparison_timeout_seconds'):
        positive_integer(payload, key)
    if payload['proposer_backend'] not in ('openai-compatible', 'claude-cli'):
        raise GuardrailError('proposer_backend must be openai-compatible or claude-cli')
    for budget_key in ('proposer_budget', 'reflection_budget'):
        budget = payload[budget_key]
        if not isinstance(budget, dict) or set(budget) != {
                'max_model_calls', 'max_input_tokens', 'max_output_tokens'}:
            raise GuardrailError(f'{budget_key} needs calls, input tokens and output tokens')
        for key, value in budget.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise GuardrailError(f'{budget_key} {key} must be positive')
    if not isinstance(payload['model_id'], str) or not payload['model_id']:
        raise GuardrailError('GEPA model_id must be non-empty text')
    return payload


def run(args: argparse.Namespace) -> int:
    output = absolute(args.output)
    if output.exists() and any(output.iterdir()):
        raise GuardrailError('GEPA output must be a new or empty directory')
    config = load_config(args.config)
    if args.reflection_lm != config['model_id']:
        raise GuardrailError('reflection model and task proposer model must be identical')
    output.mkdir(parents=True, exist_ok=True)
    write_json_new(output / 'config.json', config)
    adapter = HarnessGEPAAdapter(
        output_root=output / 'adapter', proposer=config['proposer'], model_id=config['model_id'],
        proposer_budget=config['proposer_budget'],
        proposer_timeout_seconds=config['proposer_timeout_seconds'],
        execution_timeout_seconds=config['execution_timeout_seconds'],
        comparison_timeout_seconds=config['comparison_timeout_seconds'])
    reflection_class = ClaudeCLIReflection if config['proposer_backend'] == 'claude-cli' else BudgetedReflection
    reflection = reflection_class(
        output / 'reflection_usage.json', config['reflection_budget'], config['model_id'])
    options = {
        'seed_candidate': config['seed_candidate'],
        'trainset': config['trainset'], 'valset': config['valset'], 'adapter': adapter,
        'reflection_lm': reflection,
        'reflection_minibatch_size': config['reflection_minibatch_size'],
        'candidate_selection_strategy': 'pareto', 'frontier_type': 'instance',
        'use_merge': False, 'max_metric_calls': config['max_metric_calls'],
        'run_dir': str(output / 'engine'), 'use_wandb': False, 'use_mlflow': False,
        'track_best_outputs': False, 'display_progress_bar': False,
        'cache_evaluation': False,
        'seed': config['seed'], 'raise_on_exception': True,
    }
    result = optimize(**options)
    best_score = result.val_aggregate_scores[result.best_idx]
    payload = {
        'schema': 'AUTO-RESEARCH-GEPA-RESULT-1', 'status': 'COMPLETED',
        'config_sha256': canonical_hash(config), 'model_id': config['model_id'],
        'best_candidate': result.best_candidate, 'best_score': best_score,
        'adapter_usage': adapter.get_adapter_state(), 'reflection_usage': reflection.get_usage(),
        'gepa_result': result.to_dict(),
        'score_window_read': False, 'independent_real_data_oos': False,
    }
    reject_sensitive_fields(payload)
    write_json_new(output / 'result.json', payload)
    print(json.dumps({'status': 'COMPLETED', 'best_candidate': result.best_candidate,
                      'best_score': best_score, 'adapter_usage': adapter.get_adapter_state(),
                      'reflection_usage': reflection.get_usage(),
                      'output': str(output)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--reflection-lm', required=True,
                        help='GEPA reflection model identifier; must equal config model_id')
    args = parser.parse_args()
    try:
        return run(args)
    except (GuardrailError, ValueError, FileNotFoundError, FileExistsError) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
