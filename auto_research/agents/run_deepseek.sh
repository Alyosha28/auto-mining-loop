#!/usr/bin/env bash
# DeepSeek control arm: sanitized prompts only; all evaluation stays local.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
if [ ! -f auto_research/agents/.deepseek_key ] && [ -z "${DEEPSEEK_API_KEY:-}" ] && [ ! -f "${HOME}/.config/codex-deepseek/api-key" ]; then
  echo "missing auto_research/agents/.deepseek_key, DEEPSEEK_API_KEY, or ~/.config/codex-deepseek/api-key" >&2
  exit 1
fi
export AUTO_RESEARCH_LLM_PROVIDER=deepseek
export AUTO_RESEARCH_LLM_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"
.venv/bin/python auto_research/agents/run_matrix.py --model "$AUTO_RESEARCH_LLM_MODEL" --seeds 7,11,17 \
  --budget 12 --generations 6 --per-generation 6 --ablation-seeds 7 --claim
.venv/bin/python auto_research/agents/build_report.py
