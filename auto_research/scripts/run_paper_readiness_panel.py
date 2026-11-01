#!/usr/bin/env python3
"""Run remaining V003 units with bounded task-level parallelism and fail-fast dispatch."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / 'skills/current/factor-evolution-harness2/scripts'
sys.path.insert(0, str(SCRIPTS))
from run_research_loop import absolute


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def read_protocol(path: Path) -> dict:
    protocol = read_json(path)
    tasks = []
    for filename in protocol['task_files']:
        tasks.append(read_json(path.parent / filename))
    protocol['tasks'] = tasks
    return protocol


def unit_name(protocol: dict, task: str, seed: int, replicate: str) -> str:
    return f'PAPER_READINESS_{task}_S{seed}_R{replicate}_{protocol["protocol_version"]}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--protocol', type=Path, required=True)
    args = parser.parse_args()
    protocol_path = absolute(args.protocol)
    protocol = read_protocol(protocol_path)
    root = absolute(protocol['run_root'])
    log_root = root / 'panel_logs'
    log_root.mkdir(parents=True, exist_ok=True)
    runner = SCRIPTS / 'run_paper_readiness_v003.py'
    pending = [(task['task'], seed, replicate)
               for task in protocol['tasks'] for seed in protocol['seeds']
               for replicate in protocol['model_replicates']
               if not (root / unit_name(protocol, task['task'], seed, replicate) / 'result.json').is_file()]
    print(json.dumps({'remaining_units': len(pending), 'max_workers': protocol['max_parallel_units']}))

    def execute(item: tuple[str, int, str]) -> dict:
        task, seed, replicate = item
        name = unit_name(protocol, task, seed, replicate)
        command = [sys.executable, str(runner), '--protocol', str(protocol_path),
                   '--task', task, '--seed', str(seed), '--replicate', replicate]
        with (log_root / f'{name}.stdout.log').open('w', encoding='utf-8') as stdout, \
                (log_root / f'{name}.stderr.log').open('w', encoding='utf-8') as stderr:
            completed = subprocess.run(command, stdout=stdout, stderr=stderr)
        return {'task': task, 'seed': seed, 'replicate': replicate,
                'returncode': completed.returncode, 'run': name}

    failures = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=protocol['max_parallel_units']) as pool:
        futures = {pool.submit(execute, item): item for item in pending}
        try:
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                print(json.dumps(result), flush=True)
                if result['returncode'] != 0:
                    failures.append(result)
                    for waiting in futures:
                        waiting.cancel()
                    break
        finally:
            for future in futures:
                if not future.done():
                    future.cancel()
    if failures:
        raise RuntimeError(f'V003 dispatch stopped after failure: {failures[0]}')
    print(json.dumps({'status': 'COMPLETED', 'completed_units': len(pending)}))


if __name__ == '__main__':
    main()
