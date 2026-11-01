#!/usr/bin/env bash
# Load a local LM Studio model by substring, keeping the server alive.
set -euo pipefail
SUBSTR="${1:-sage}"
LMS="${HOME}/.lmstudio/bin/lms"
ID=$("$LMS" ls 2>/dev/null | awk -v s="$SUBSTR" 'tolower($0) ~ tolower(s) {print $1; exit}')
if [ -z "${ID}" ]; then
  echo "no model matching '$SUBSTR' yet; available:" >&2
  "$LMS" ls 2>&1 | head -20 >&2
  exit 1
fi
"$LMS" server start >/dev/null 2>&1 || true
"$LMS" load "$ID" -c 8192 --gpu max --ttl 3600 -y
"$LMS" ps | head -8
