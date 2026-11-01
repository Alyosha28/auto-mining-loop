#!/usr/bin/env python3
"""Local model registry helper (LM Studio OpenAI-compatible endpoint)."""
from __future__ import annotations

import json
import os
import urllib.request

BASE_URL = os.environ.get('AUTO_RESEARCH_LLM_BASE', 'http://127.0.0.1:1234/v1')


def list_models(base_url: str | None = None) -> list[str]:
    url = (base_url or BASE_URL).rstrip('/') + '/models'
    with urllib.request.urlopen(url, timeout=10) as response:
        data = json.loads(response.read().decode('utf-8'))
    return [item['id'] for item in data.get('data', [])]


def find_model(substring: str) -> str:
    matches = [model for model in list_models() if substring.lower() in model.lower()]
    if not matches:
        raise SystemExit(f'no local model matches {substring!r}; available: {list_models()}')
    return matches[0]


if __name__ == '__main__':
    print(json.dumps({'models': list_models()}, ensure_ascii=False, indent=1))
