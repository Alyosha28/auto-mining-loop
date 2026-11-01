#!/usr/bin/env python3
"""Compare two frozen benchmark versions and report per-task/per-baseline deltas."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FROZEN = ROOT / 'results/frozen'


def load_version(version: str) -> dict:
    directory = FROZEN / version
    manifests = list(directory.glob('MANIFEST.json'))
    if not manifests:
        raise SystemExit(f'missing manifest for {version}')
    manifest = json.loads(manifests[0].read_text())
    result = directory / Path(manifest['source']).name
    return json.loads(result.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version_a')
    parser.add_argument('version_b')
    args = parser.parse_args()
    a = load_version(args.version_a)['summary']
    b = load_version(args.version_b)['summary']
    rows = []
    for task in sorted(set(a) & set(b)):
        for baseline in sorted(set(a[task]) & set(b[task])):
            va = a[task][baseline]['mean']
            vb = b[task][baseline]['mean']
            if va is None or vb is None:
                continue
            rows.append({'task': task, 'baseline': baseline, 'a': va, 'b': vb, 'delta': round(vb - va, 6)})
    payload = {'a': args.version_a, 'b': args.version_b, 'deltas': rows}
    print(json.dumps(payload, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
