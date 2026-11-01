#!/usr/bin/env python3
"""Evaluate the four initial task harnesses on probe splits and export safe initial traces."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_diagnostic_continuation import read_protocol
from run_research_loop import absolute, relative

COMPARE = SCRIPTS / 'compare_harnesses.py'


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_new(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=1) + '\n')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path,
                        default=ROOT / 'auto_research/config/diagnostic_continuation_v1/protocol.json')
    args = parser.parse_args()
    protocol_path = args.protocol if args.protocol.is_absolute() else ROOT / args.protocol
    protocol = read_protocol(protocol_path)
    prefix = protocol.get('screen_run_prefix', 'DIAGNOSTIC_CONTINUATION_TASK_SCREEN')
    summary = {'schema': 'AUTO-RESEARCH-DIAGNOSTIC-TASK-SCREEN-1',
               'protocol_version': protocol['protocol_version'], 'select_evaluated': False,
               'units': []}
    for seed in protocol['seeds']:
        output = absolute(protocol['run_root']) / f'{prefix}_{protocol["protocol_version"]}_S{seed}'
        result_path = output / 'result.json'
        if not result_path.is_file():
            command = [sys.executable, str(COMPARE), '--output', str(output),
                       '--contract', str(ROOT / protocol['continuation_contract']),
                       '--seed', str(seed), '--budget', str(protocol['candidate_budget']),
                       '--dev-split', protocol['dev_split'], '--probe-only', '--coverage-log-only']
            for task in protocol['tasks']:
                command += ['--arm', f'{task["task"]}={ROOT / task["initial_harness"]}']
            features = sorted({feature for task in protocol['tasks'] for feature in task['features']})
            command += ['--features', ','.join(features)]
            completed = subprocess.run(command, capture_output=True, text=True)
            output.mkdir(parents=True, exist_ok=True)
            (output / 'screen.stdout.log').write_text(completed.stdout or '', encoding='utf-8')
            (output / 'screen.stderr.log').write_text(completed.stderr or '', encoding='utf-8')
            if completed.returncode != 0:
                raise RuntimeError(f'task screen seed {seed} failed: {completed.stderr[-2000:]}')
        comparison = read(result_path)
        if comparison.get('primary_evaluation') != 'probe' or 'select_metrics' in next(
                iter(comparison['arms'].values())):
            raise RuntimeError(f'task screen seed {seed} unexpectedly evaluated select')
        for task in protocol['tasks']:
            name = task['task']
            probe = read(output / f'probe/{name}.json')
            harness = read(ROOT / task['initial_harness'])
            trace = {
                'schema': 'AUTO-RESEARCH-PROPOSER-TRACE-1',
                'source_run': f'{output.name}:{name}',
                'scope': 'Initial fixed-harness development probe; no proposal or select outcomes.',
                'seed_semantics': 'seeded_year_blocks_v1',
                'search_policy': {key: harness.get(key) for key in
                                  ('operators', 'seed_pool', 'skills', 'probe', 'protocol')},
                'reference_probe_metrics': probe['metrics'],
                'adopted_probe_metrics': probe['metrics'],
                'proposals': [], 'kept_edits': [],
            }
            trace_path = ROOT / task['initial_safe_trace'][str(seed)]
            if trace_path.is_file():
                if read(trace_path) != trace:
                    raise RuntimeError(f'initial trace differs from screening evidence: {trace_path}')
            else:
                write_new(trace_path, trace)
            metrics = probe['metrics']
            summary['units'].append({
                'task': name, 'seed': seed, 'screen_run': relative(output),
                'probe_metrics': {key: metrics.get(key) for key in
                                  ('mean_abs_ic', 'top_ic_mean', 'pass_rate', 'duplicate_rate', 'novelty',
                                   'coverage', 'turnover_proxy', 'icir_p25', 'year_ic_min')},
                'common_support_verified': comparison['common_support_verified'],
            })
    write_new(protocol_path.parent / protocol.get('screen_summary_file', 'initial_defects.json'), summary)
    print(json.dumps({'status': 'COMPLETED', 'units': len(summary['units']),
                      'select_evaluated': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
