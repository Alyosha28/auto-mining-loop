#!/usr/bin/env python3
"""Offline test of the external-provider path: mock OpenAI-compatible server + sanitizer check."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CAPTURED: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get('Content-Length', '0'))
        payload = json.loads(self.rfile.read(length).decode('utf-8'))
        CAPTURED.append(payload)
        content = json.dumps([
            {'expression': 'rank_minus(F01, F02)', 'rationale': 'mock alias mechanism',
             'expected_ic': 0.02, 'falsification': 'mock falsification'},
            {'expression': 'ratio(F02, F03)', 'rationale': 'mock alias mechanism 2',
             'expected_ic': 0.02, 'falsification': 'mock falsification 2'},
        ])
        body = json.dumps({'id': 'mock', 'model': 'mock', 'choices': [
            {'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 10}}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence
        pass


def main() -> None:
    server = HTTPServer(('127.0.0.1', 8123), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    features = ['momentum_20', 'volatility_60', 'turnover_cv_20', 'pv_range_expansion_20_60']
    code = ('import sys, json; sys.path.insert(0, "auto_research");'
            'from agents import roles;'
            f'out = roles.propose("mock brief", {features!r}, n=2);'
            'print(json.dumps(out))')
    env = dict(os.environ)
    env.update({'AUTO_RESEARCH_LLM_PROVIDER': 'deepseek',
                'AUTO_RESEARCH_LLM_BASE': 'http://127.0.0.1:8123/v1',
                'AUTO_RESEARCH_LLM_MODEL': 'mock-deepseek',
                'DEEPSEEK_API_KEY': 'test-key'})
    completed = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=120)
    server.shutdown()
    if completed.returncode != 0:
        raise SystemExit(f'proposer failed: {completed.stderr[-800:]}')
    out = json.loads(completed.stdout.strip().splitlines()[-1])
    prompts = [message['content'] for payload in CAPTURED for message in payload['messages']]
    leaked = [feature for feature in features for prompt in prompts if feature in prompt]
    statuses = [row.get('status') for row in out.get('proposals', [])]
    print(json.dumps({'n_prompts': len(prompts), 'leaked_features': sorted(set(leaked)),
                      'statuses': statuses, 'n_proposals': len(out.get('proposals', [])),
                      'sample_prompt': prompts[-1][:400] if prompts else None}, ensure_ascii=False, indent=1))
    if leaked:
        raise SystemExit('FAIL: real feature names leaked to external prompt')
    if 'PARSED' not in statuses:
        raise SystemExit('FAIL: de-aliased proposals did not parse')
    print('PROVIDER_SANITIZE_OK')


if __name__ == '__main__':
    main()
