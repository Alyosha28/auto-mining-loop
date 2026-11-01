#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
../../.venv/bin/python - <<'PY'
import json, subprocess, sys
cfg = json.load(open('configs/full.json'))
cmd = [sys.executable, '-m', 'bench.run', '--tasks', ','.join(cfg['tasks']),
       '--seeds', ','.join(map(str, cfg['seeds'])), '--budget', str(cfg['budget']),
       '--baselines', ','.join(cfg['baselines']), '--tag', cfg['tag']]
print('running:', ' '.join(cmd))
raise SystemExit(subprocess.call(cmd))
PY
