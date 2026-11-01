#!/usr/bin/env bash
# One-command Sage evaluation: ensure model loaded, run pre-registered matrix, build report.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
SUBSTR="${1:-sage}"
"${HOME}/.lmstudio/bin/lms" server start >/dev/null 2>&1 || true
ID=$("${HOME}/.lmstudio/bin/lms" ls 2>/dev/null | awk -v s="$SUBSTR" 'tolower($0) ~ tolower(s) {print $1; exit}')
if [ -z "$ID" ]; then
  echo "no model matching '$SUBSTR'" >&2
  exit 1
fi
if ! "${HOME}/.lmstudio/bin/lms" ps 2>/dev/null | grep -q "$ID"; then
  "${HOME}/.lmstudio/bin/lms" load "$ID" -c 16384 --gpu max --ttl 3600 -y >/dev/null
fi
.venv/bin/python auto_research/agents/run_matrix.py --model "$ID" --seeds 7,11,17 --budget 12 --generations 6 --per-generation 6 --ablation-seeds 7 --claim
.venv/bin/python auto_research/agents/build_report.py
