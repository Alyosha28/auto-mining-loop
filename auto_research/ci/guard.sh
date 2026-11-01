#!/usr/bin/env bash
# Legacy full guard for claims, paper and meta evidence workflows.
# Not the default exploratory check; use only for that legacy workflow or AUTO_RESEARCH_LEGACY_GUARD=1.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=".venv/bin/python"

echo "[1/8] claims"
"$PY" auto_research/scripts/claim_ledger.py verify | tail -1
"$PY" auto_research/scripts/claim_audit.py >/dev/null
"$PY" - <<'PY4'
import json
p=json.load(open("auto_research/ledger/claim_audit.json"))
if p.get("active_unbacked",0)>0 or p.get("missing_evidence",0)>0:
    print("CLAIM_AUDIT_FAIL", p)
    raise SystemExit(1)
print("CLAIM_AUDIT_OK active_unbacked=0")
PY4

echo "[2/8] paper numbers"
"$PY" auto_research/paper/check_paper_numbers.py >/dev/null && echo NUMBERS_OK

echo "[3/8] hygiene"
"$PY" auto_research/paper/check_submission_hygiene.py >/dev/null && echo HYGIENE_OK

echo "[4/8] benchmark CI"
bash auto_research/benchmark/ci.sh 2>&1 | tail -2

echo "[5/8] ledger snapshot tests"
"$PY" -m unittest discover -s auto_research/scripts -p 'test_claim_snapshot*.py' -v 2>&1 | tail -3

echo "[6/8] meta-loop tests"
"$PY" -m unittest discover -s auto_research/meta -p 'test_*.py' 2>&1 | tail -2

echo "[7/8] sample completeness"
"$PY" - <<'PY3'
import json
from pathlib import Path
bad=[]
for path in Path("auto_research/meta/runs").glob("*/manifest.json"):
    m=json.loads(path.read_text())
    if m.get("sample_complete") is False and m.get("adopted") and not m.get("patch_id","").startswith("META-TEST-"):
        bad.append(m["patch_id"])
if bad:
    print("SAMPLE_INCOMPLETE", bad)
    raise SystemExit(1)
print("SAMPLE_OK")
PY3

echo "[8/8] meta yield"
"$PY" - <<'PY2'
import json, subprocess, sys
out = subprocess.check_output([sys.executable, 'auto_research/meta/yield.py'], text=True)
payload = json.loads(out)
if payload.get('adopted_unverified', 0) > 0:
    print('UNVERIFIED_ADOPTIONS', payload['adopted_unverified'])
    raise SystemExit(1)
print(f"YIELD_OK unverified={payload.get('adopted_unverified',0)} ci_supported={payload.get('adopted_ci_supported')} point_only={payload.get('adopted_point_only')}")
PY2

external_v2_drift_only() {
  "$PY" - <<'PY'
import subprocess
import sys
allowed = {
    'config/active_mentordata_factor_library_v2_20261019.json',
    'config/mentordata_factor_library_v2_20261019.json',
}
paths = [
    'docs/CURRENT_STATE.md', 'config', 'skills/current',
    'scripts/render_current_state.py', 'scripts/render_library_registry.py',
    'scripts/manage_repository_results.py',
]
raw = subprocess.check_output(['git', 'status', '--porcelain', '-z', '--', *paths])
changed = {entry[3:] for entry in raw.decode().split('\0') if entry}
sys.exit(0 if changed and changed <= allowed else 1)
PY
}

echo "[preflight] workspace"
if bash scripts/preflight.sh > /tmp/dsh_preflight.log 2>&1; then
  tail -1 /tmp/dsh_preflight.log
else
  if grep -Eq "Active pointer identity mismatch|CURRENT_STATE_STALE|LIBRARY_REGISTRY_STALE" /tmp/dsh_preflight.log && external_v2_drift_only; then
    echo "PREFLIGHT_EXTERNAL_WARN: v2 active pointer drift (unrelated to auto_research)"
  else
    tail -5 /tmp/dsh_preflight.log
    exit 1
  fi
fi

echo GUARD_OK
