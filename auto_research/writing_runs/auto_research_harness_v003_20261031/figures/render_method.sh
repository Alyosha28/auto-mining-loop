#!/usr/bin/env bash
set -euo pipefail

RUN="$(cd "$(dirname "$0")/.." && pwd)"
python3 "$RUN/scripts/export_archify_method.py"
