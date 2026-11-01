#!/usr/bin/env python3
"""Freeze a benchmark result into results/frozen/<version>/ with a SHA-256 manifest."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / 'results'


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--result', required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    source = Path(args.result)
    if not source.is_absolute():
        source = ROOT / source
    target_dir = RESULTS / 'frozen' / args.version
    if target_dir.exists():
        raise SystemExit(f'refusing to overwrite {target_dir}')
    target_dir.mkdir(parents=True)
    target = target_dir / source.name
    shutil.copy2(source, target)
    manifest = {'schema': 'AUTO-RESEARCH-BENCH-FROZEN-1',
                'created_at': datetime.now(timezone.utc).isoformat(),
                'version': args.version, 'source': str(source.relative_to(ROOT)),
                'sha256': sha256(target), 'bytes': target.stat().st_size}
    (target_dir / 'MANIFEST.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
