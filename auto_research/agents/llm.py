#!/usr/bin/env python3
"""OpenAI-compatible local or remote LLM client with response caching."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE = Path(__file__).resolve().parent / 'cache'
STATS = {'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
LAST_USAGE = {'api_calls': 0, 'prompt_tokens': 0, 'completion_tokens': 0,
              'cached': False, 'available': False}
CACHE.mkdir(parents=True, exist_ok=True)
PROVIDER = os.environ.get('AUTO_RESEARCH_LLM_PROVIDER', 'local')
_key_file = Path(__file__).resolve().parent / '.deepseek_key'
if PROVIDER == 'deepseek':
    BASE_URL = os.environ.get('AUTO_RESEARCH_LLM_BASE', 'https://api.deepseek.com/v1')
    MODEL = os.environ.get('AUTO_RESEARCH_LLM_MODEL', 'deepseek-flash')  # DeepSeek-V4.1-Flash
    API_KEY = os.environ.get('DEEPSEEK_API_KEY') or (_key_file.read_text(encoding='utf-8').strip() if _key_file.is_file() else '')
    if not API_KEY:
        _fallback = Path.home() / '.config/codex-deepseek/api-key'
        if _fallback.is_file():
            API_KEY = _fallback.read_text(encoding='utf-8').strip()
elif PROVIDER == 'opencode':
    BASE_URL = os.environ.get('AUTO_RESEARCH_LLM_BASE', 'https://opencode.ai/zen/go/v1')
    MODEL = os.environ.get('AUTO_RESEARCH_LLM_MODEL', 'deepseek-v4.1-flash')
    _oc_key = Path(__file__).resolve().parent / '.opencode_key'
    API_KEY = os.environ.get('OPENCODE_API_KEY') or (_oc_key.read_text(encoding='utf-8').strip() if _oc_key.is_file() else '')
else:
    BASE_URL = os.environ.get('AUTO_RESEARCH_LLM_BASE', 'http://127.0.0.1:1234/v1')
    MODEL = os.environ.get('AUTO_RESEARCH_LLM_MODEL', 'sage-a1.5')
    API_KEY = ''


def _key(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]


def chat(messages: list[dict], model: str | None = None, temperature: float = 0.2,
         max_tokens: int = 2400, retries: int = 2, response_format: dict | None = None) -> str:
    global LAST_USAGE
    model = model or MODEL
    last_error = None
    current_max = max_tokens
    for attempt in range(retries + 1):
        payload = {'model': model, 'messages': messages, 'temperature': temperature,
                   'max_tokens': current_max}
        if response_format:
            payload['response_format'] = response_format
        if PROVIDER == 'local':
            if os.environ.get('AUTO_RESEARCH_LLM_THINKING', '0') != '1':
                payload['chat_template_kwargs'] = {'enable_thinking': False}
            effort = os.environ.get('AUTO_RESEARCH_LLM_REASONING_EFFORT', 'none')
            if effort:
                payload['reasoning_effort'] = effort
        elif os.environ.get('AUTO_RESEARCH_LLM_REASONING_EFFORT'):
            payload['reasoning_effort'] = os.environ['AUTO_RESEARCH_LLM_REASONING_EFFORT']
        cache_path = CACHE / f'{_key(payload)}.json'
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            usage = cached.get('usage') or {}
            LAST_USAGE = {'api_calls': 0,
                          'prompt_tokens': int(usage.get('prompt_tokens') or 0),
                          'completion_tokens': int(usage.get('completion_tokens') or 0),
                          'cached': True, 'available': bool(usage)}
            return cached['content']
        try:
            headers = {'Content-Type': 'application/json'}
            if API_KEY:
                headers['Authorization'] = f'Bearer {API_KEY}'
            if PROVIDER == 'opencode':
                headers['User-Agent'] = 'opencode/1.0.0'
                headers['x-opencode-session'] = 'factor-digging'
            request = urllib.request.Request(
                f'{BASE_URL}/chat/completions',
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers=headers)
            with urllib.request.urlopen(request, timeout=600) as response:
                data = json.loads(response.read().decode('utf-8'))
            message = data['choices'][0]['message']
            content = message.get('content') or ''
            if not content.strip():
                # reasoning-only response: do not cache; retry with more tokens
                current_max = current_max * 2
                last_error = RuntimeError('empty content (reasoning-only response)')
                continue
            usage = data.get('usage') or {}
            LAST_USAGE = {'api_calls': 1,
                          'prompt_tokens': int(usage.get('prompt_tokens') or 0),
                          'completion_tokens': int(usage.get('completion_tokens') or 0),
                          'cached': False, 'available': bool(usage)}
            STATS['api_calls'] += 1
            STATS['prompt_tokens'] += int(usage.get('prompt_tokens') or 0)
            STATS['completion_tokens'] += int(usage.get('completion_tokens') or 0)
            cache_path.write_text(json.dumps({'content': content, 'usage': data.get('usage'),
                                              'model': model}, ensure_ascii=False),
                                  encoding='utf-8')
            return content
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(2 + attempt * 3)
    raise RuntimeError(f'local LLM request failed: {last_error}')


def get_stats() -> dict:
    return dict(STATS)


def get_last_usage() -> dict:
    return dict(LAST_USAGE)


def extract_all_json(text: str) -> list:
    """Extract every top-level JSON object/array (handles concatenated objects)."""
    text = text.strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    results = []
    index = 0
    while index < len(text):
        start = min([pos for pos in (text.find('{', index), text.find('[', index)) if pos != -1], default=-1)
        if start == -1:
            break
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        results.append(value)
        index = start + end
    return results


def extract_json(text: str):
    """Extract the first JSON object/array from a model response."""
    text = text.strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    for start_char, end_char in (('[', ']'), ('{', '}')):
        start = text.find(start_char)
        end = text.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f'no JSON found in model output: {text[:300]}')


if __name__ == '__main__':
    print(chat([{'role': 'user', 'content': 'Return JSON {"ok": true} only.'}]))
