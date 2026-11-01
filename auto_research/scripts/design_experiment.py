#!/usr/bin/env python3
"""Experiment Designer v0: turn a hypothesis into an executable batch spec."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENTS = ROOT / 'auto_research/experiments'
INDEX = EXPERIMENTS / 'index.jsonl'
LIBRARY_VERSION = 9  # evo_rsif/library snapshot for fair replay


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def next_experiment_id() -> str:
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    count = 0
    if INDEX.is_file():
        for line in INDEX.read_text(encoding='utf-8').splitlines():
            if line.strip():
                count += 1
    return f'EXP-{day}-{count + 1:03d}'


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hypothesis', required=True)
    args = parser.parse_args()
    hypothesis_path = ROOT / args.hypothesis if not Path(args.hypothesis).is_absolute() else Path(args.hypothesis)
    hypothesis = json.loads(hypothesis_path.read_text(encoding='utf-8'))
    experiment = hypothesis['proposed_experiment']
    panel = ROOT / experiment['panel']
    if not panel.is_dir():
        raise SystemExit(f'panel not found: {experiment["panel"]}')
    arms = experiment['arms']
    for arm, harness in arms.items():
        if not (ROOT / harness).is_file():
            raise SystemExit(f'harness not found for arm {arm}: {harness}')
    if int(experiment['max_candidates']) > 60:
        raise SystemExit('max_candidates exceeds budget cap 60')
    experiment_id = next_experiment_id()
    experiment_dir = EXPERIMENTS / experiment_id
    if experiment_dir.exists():
        raise SystemExit(f'experiment already exists: {experiment_dir}')
    experiment_dir.mkdir(parents=True)
    batches = {arm: f'p1_{experiment_id.split("-")[-1]}_{arm}' for arm in arms}
    spec = {
        'schema': 'AUTO-RESEARCH-EXPERIMENT-1',
        'experiment_id': experiment_id,
        'hypothesis_id': hypothesis['hypothesis_id'],
        'created_at': now(),
        'panel': experiment['panel'],
        'arms': arms,
        'batches': batches,
        'max_candidates': int(experiment['max_candidates']),
        'pools': experiment.get('pools', '000300.SH,000905.SH,000852.SH'),
        'control_arm': 'base' if 'base' in arms else next(iter(arms)),
        'fair_replay': {'baseline_version': experiment.get('fair_replay_baseline_version', LIBRARY_VERSION),
                        'enabled': bool(experiment.get('fair_replay_baseline_version', LIBRARY_VERSION))},
        'metrics': ['fair_replay_added', 'added_per_1000_evaluated', 'dev_passed'],
        'stop_rules': ['evaluable coverage < 0.96 -> STOP', 'no admission -> record negative result',
                       'score window not read inside the experiment'],
        'score_window_read': False,
    }
    (experiment_dir / 'spec.json').write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    with INDEX.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'experiment_id': experiment_id, 'hypothesis_id': hypothesis['hypothesis_id'],
                                 'path': str((experiment_dir / 'spec.json').relative_to(ROOT)),
                                 'created_at': spec['created_at'], 'status': 'designed'},
                                ensure_ascii=False) + '\n')
    print(json.dumps({'experiment_id': experiment_id, 'spec': str((experiment_dir / 'spec.json').relative_to(ROOT)),
                      'panel': spec['panel'], 'batches': batches}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
