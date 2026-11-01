#!/usr/bin/env bash
# Minimal benchmark run: one task, one seed, budget 6 (~10s).
set -euo pipefail
cd "$(dirname "$0")/.."
../../.venv/bin/python -m bench.run --tasks planted --seeds 1 --budget 6 \
  --baselines random,evolution,bo,qd --tag quickstart
