#!/usr/bin/env bash
# One-command CI: unit tests + quickstart + output checks + frozen manifest check.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PY:-../../.venv/bin/python}"
echo "[1/4] unit tests"
"$PY" -m unittest discover -s tests -v

echo "[2/4] quickstart"
bash examples/quickstart.sh > /tmp/bench_quickstart.log

echo "[3/4] output checks"
LATEST=$("$PY" - <<'PY'
from pathlib import Path
files = sorted(Path('results').glob('bench_quickstart_*.json'))
print(files[-1] if files else '')
PY
)
[ -n "$LATEST" ] || { echo "no quickstart result"; exit 1; }
"$PY" - "$LATEST" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1]))
assert 'summary' in payload and 'planted' in payload['summary']
for method in ('random', 'evolution', 'bo', 'qd'):
    assert method in payload['summary']['planted'], method
assert payload['score_window_read'] is False
print('CI_OK', sys.argv[1])
PY

echo "[4/4] frozen manifest check"
"$PY" - <<'PY'
import hashlib, json
from pathlib import Path
frozen = sorted(Path('results/frozen').glob('*/MANIFEST.json'))
if not frozen:
    print('FROZEN_SKIPPED (no frozen manifests; clean clone)')
    raise SystemExit(0)
for path in frozen:
    m = json.loads(path.read_text())
    target = path.parent / Path(m['source']).name
    assert target.is_file(), target
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    assert digest == m['sha256'], path
print('FROZEN_OK', len(frozen))
PY
