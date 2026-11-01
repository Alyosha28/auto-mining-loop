#!/usr/bin/env python3
"""Experiment manifest: binds a matrix result to provider/model/config/script hashes."""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXP = ROOT / 'auto_research/agents/experiments'
MANIFESTS = EXP / 'manifests'

SCRIPT_FILES = ['auto_research/agents/orchestrator.py', 'auto_research/agents/llm.py',
                'auto_research/agents/roles.py', 'auto_research/agents/compare.py',
                'auto_research/agents/run_matrix.py', 'auto_research/agents/sanitize.py']


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_manifest(result_path: Path, provider: str, model: str | None, config: dict) -> Path:
    MANIFESTS.mkdir(parents=True, exist_ok=True)
    manifest = {
        'schema': 'AUTO-RESEARCH-AGENT-MANIFEST-1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'provider': provider,
        'model': model,
        'config': config,
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'script_sha256': {name: sha256_file(ROOT / name) for name in SCRIPT_FILES if (ROOT / name).is_file()},
        'result_file': str(result_path.relative_to(ROOT)),
        'result_sha256': sha256_file(result_path),
        'score_window_read': False,
    }
    path = MANIFESTS / (result_path.stem + '.manifest.json')
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return path


def verify_manifest(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    result = ROOT / manifest['result_file']
    checks = {
        'result_exists': result.is_file(),
        'result_hash_ok': result.is_file() and sha256_file(result) == manifest['result_sha256'],
        'score_window_read_false': manifest.get('score_window_read') is False,
    }
    return {'manifest': str(manifest_path.relative_to(ROOT)), 'checks': checks,
            'ok': all(checks.values())}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', nargs='?', help='manifest path; omit to verify all')
    args = parser.parse_args()
    if args.manifest:
        print(json.dumps(verify_manifest(ROOT / args.manifest), ensure_ascii=False, indent=1))
    else:
        rows = [verify_manifest(path) for path in sorted(MANIFESTS.glob('*.json')) if not path.name.startswith('._')]
        print(json.dumps({'n': len(rows), 'failed': [row for row in rows if not row['ok']]}, ensure_ascii=False, indent=1))
