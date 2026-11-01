#!/usr/bin/env python3
"""Confirm seed and GEPA-selected guidance on a fresh frozen development split."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from evo_probe import GuardrailError
from gepa_harness_adapter import HarnessGEPAAdapter
from run_research_loop import absolute, write_json_new

COMPARISON = SCRIPTS / 'compare_harnesses.py'


def run(args: argparse.Namespace) -> int:
    gepa_run = absolute(args.gepa_run)
    output = absolute(args.output)
    if output.exists() and any(output.iterdir()):
        raise GuardrailError('GEPA confirmation output must be new or empty')
    if output == gepa_run or gepa_run in output.parents:
        raise GuardrailError('GEPA confirmation must be outside the optimization run')
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads((gepa_run / 'config.json').read_text(encoding='utf-8'))
    optimization = json.loads((gepa_run / 'result.json').read_text(encoding='utf-8'))
    data = json.loads(Path(args.data).read_text(encoding='utf-8'))
    adapter = HarnessGEPAAdapter(
        output_root=output / 'adapter', proposer=config['proposer'], model_id=config['model_id'],
        proposer_budget=config['proposer_budget'],
        proposer_timeout_seconds=config['proposer_timeout_seconds'],
        execution_timeout_seconds=config['execution_timeout_seconds'],
        comparison_timeout_seconds=config['comparison_timeout_seconds'])
    candidates = {'baseline': config['seed_candidate'], 'gepa': optimization['best_candidate']}
    generated = {}
    for name, candidate in candidates.items():
        generation = adapter._generate(data, candidate)
        if generation.get('status') != 'COMPLETED':
            raise GuardrailError(f'{name} confirmation proposal is invalid: {generation.get("error")}')
        generated[name] = generation
    comparison_output = output / 'comparison'
    command = [sys.executable, str(COMPARISON), '--contract', str(absolute(data['contract'])),
               '--output', str(comparison_output), '--seed', str(data['seed']),
               '--budget', str(data['budget']), '--dev-split', data['dev_split'],
               '--coverage-log-only']
    if data['features']:
        command += ['--features', data['features']]
    if data['seed_panel']:
        command += ['--seed-panel', str(absolute(data['seed_panel']))]
    if data['demo']:
        command.append('--demo')
    for name in ('baseline', 'gepa'):
        command += ['--arm', f'{name}={absolute(generated[name]["adopted_harness"])}']
    command += ['--arm', f'base={absolute(data["harness"])}']
    completed = subprocess.run(command, capture_output=True, text=True)
    (output / 'comparison_stdout.log').write_text(completed.stdout or '', encoding='utf-8')
    (output / 'comparison_stderr.log').write_text(completed.stderr or '', encoding='utf-8')
    if completed.returncode != 0:
        raise GuardrailError('GEPA confirmation common-sample comparison failed')
    comparison = json.loads((comparison_output / 'result.json').read_text(encoding='utf-8'))
    result = {
        'schema': 'AUTO-RESEARCH-GEPA-CONFIRMATION-1', 'status': 'COMPLETED',
        'source_optimization': str(gepa_run), 'fresh_seed': data['seed'],
        'text_changed': candidates['baseline'] != candidates['gepa'],
        'candidates': candidates, 'adapter_usage': adapter.get_adapter_state(),
        'comparison': str(comparison_output), 'gepa_vs_baseline': comparison['comparisons']['gepa'],
        'score_window_read': False, 'independent_real_data_oos': False,
    }
    write_json_new(output / 'result.json', result)
    print(json.dumps({'status': 'COMPLETED', 'text_changed': result['text_changed'],
                      'select_primary_delta': result['gepa_vs_baseline']['select']['primary_delta'],
                      'select_supported': result['gepa_vs_baseline']['select']['supported'],
                      'output': str(output)}, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gepa-run', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True,
                        help='fresh confirmation data with the GEPA data-instance schema')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args)
    except (GuardrailError, ValueError, FileNotFoundError, FileExistsError) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
