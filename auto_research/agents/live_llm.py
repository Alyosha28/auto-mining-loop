#!/usr/bin/env python3
"""Loopback-only LM Studio client with explicit cost and retry accounting."""
from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.error import HTTPError
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_manifest import sha256_json


class TransientLLMError(RuntimeError):
    pass


class LLMRequestError(RuntimeError):
    def __init__(self, message: str, attempts: list[dict], actual_calls: int, tokens: dict):
        super().__init__(message)
        self.attempts = attempts
        self.actual_calls = actual_calls
        self.tokens = tokens


@dataclass
class LLMResponse:
    content: str
    usage: dict
    raw_hash: str
    actual_calls: int
    cache_hits: int
    tokens: dict
    attempts: list[dict]
    raw: dict
    cached: bool = False

    @property
    def infrastructure_retries(self) -> int:
        return sum(1 for attempt in self.attempts if attempt.get("status") != "success"
                     and attempt.get("status") != "cache_hit")

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "usage": self.usage,
            "raw_hash": self.raw_hash,
            "actual_calls": self.actual_calls,
            "cache_hits": self.cache_hits,
            "tokens": self.tokens,
            "infrastructure_retries": self.infrastructure_retries,
            "attempts": self.attempts,
            "raw": self.raw,
            "cached": self.cached,
        }


Transport = Callable[[str, dict, dict, float], dict]


def validate_loopback_base(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("live LLM endpoint must be plain http on literal 127.0.0.1, without credentials")
    if parsed.port is None:
        raise ValueError("live LLM endpoint must include an explicit loopback port")
    return base_url.rstrip("/")


def _urlopen_transport(url: str, payload: dict, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise TransientLLMError(f"HTTP {exc.code}: {body[:2000]}") from exc


def _usage(raw: dict) -> dict:
    usage = raw.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _zero_tokens() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


@dataclass
class LiveLLMClient:
    model: str = "sage-a1.5"
    base_url: str = "http://127.0.0.1:1234/v1"
    timeout: float = 600.0
    max_retries: int = 2
    retry_backoff: float = 0.5
    cache_enabled: bool = False
    cache_scope: str | None = None
    transport: Transport | None = None
    supports_explicit_seed: bool = False
    seed: int | None = None
    reasoning_effort: str | None = None
    _cache: dict = field(default_factory=dict, init=False, repr=False)
    _stats: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self.base_url = validate_loopback_base(self.base_url)
        if self.cache_enabled and not self.cache_scope:
            raise ValueError("an explicit run/arm cache_scope is required when cache is enabled")
        if self.seed is not None and not self.supports_explicit_seed:
            raise ValueError("explicit LLM seed requested but backend support is not enabled")
        self.transport = self.transport or _urlopen_transport
        self._stats = {
            "logical_calls": 0,
            "actual_calls": 0,
            "non_cached_calls": 0,
            "cached_calls": 0,
            "cache_hits": 0,
            "successful_responses": 0,
            "failed_calls": 0,
            "infrastructure_retries": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }

    def seed_capabilities(self) -> dict:
        return {
            "explicit_seed_supported": self.supports_explicit_seed,
            "configured_seed": self.seed,
            "seed_will_be_sent": self.seed is not None and self.supports_explicit_seed,
            "deterministic_sampling_claimed": False,
            "prompt_seed_is_determinism": False,
            "reasoning_effort": self.reasoning_effort,
            "note": "Prompt seeds are not model seeds; matched live loops are not deterministic replay.",
        }

    def get_stats(self) -> dict:
        return dict(self._stats)

    def _add_tokens(self, tokens: dict) -> None:
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._stats[key] += int(tokens.get(key) or 0)

    def chat(self, messages: list[dict], model: str | None = None, temperature: float = 0.2,
             max_tokens: int = 1024, response_format: dict | None = None,
             seed: int | None = None) -> LLMResponse:
        effective_seed = self.seed if seed is None else seed
        if effective_seed is not None and not self.supports_explicit_seed:
            raise ValueError("explicit LLM seed requested but backend support is not enabled")
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": float(temperature),
            "max_tokens": int(max_tokens),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if response_format is not None:
            payload["response_format"] = response_format
        if effective_seed is not None:
            payload["seed"] = int(effective_seed)
        cache_key = sha256_json({"scope": self.cache_scope, "payload": payload})
        self._stats["logical_calls"] += 1
        if self.cache_enabled and cache_key in self._cache:
            cached = self._cache[cache_key]
            self._stats["cached_calls"] += 1
            self._stats["cache_hits"] += 1
            return LLMResponse(content=cached.content, usage=dict(cached.usage), raw_hash=cached.raw_hash,
                               actual_calls=0, cache_hits=1, tokens=_zero_tokens(),
                               attempts=[{"attempt": 0, "status": "cache_hit", "raw_hash": cached.raw_hash}],
                               raw=cached.raw, cached=True)
        self._stats["non_cached_calls"] += 1
        attempts: list[dict] = []
        tokens = _zero_tokens()
        last_error: Exception | None = None
        for attempt_index in range(self.max_retries + 1):
            if attempt_index:
                self._stats["infrastructure_retries"] += 1
            self._stats["actual_calls"] += 1
            actual_calls = attempt_index + 1
            raw = None
            usage = None
            try:
                raw = self.transport(f"{self.base_url}/chat/completions", payload,
                                     {"Content-Type": "application/json"}, self.timeout)
                usage = _usage(raw)
                for key in tokens:
                    tokens[key] += usage[key]
                self._add_tokens(usage)
                raw_hash = sha256_json(raw)
                message = raw["choices"][0]["message"]
                content = message.get("content") or ""
                if not content.strip():
                    raise TransientLLMError("empty content (possibly reasoning-only response)")
                attempts.append({"attempt": attempt_index, "status": "success", "usage": usage,
                                 "raw_hash": raw_hash, "actual_calls": actual_calls})
                response = LLMResponse(content=content, usage=usage, raw_hash=raw_hash,
                                       actual_calls=actual_calls, cache_hits=0, tokens=dict(tokens),
                                       attempts=list(attempts), raw=raw)
                self._stats["successful_responses"] += 1
                if self.cache_enabled:
                    self._cache[cache_key] = response
                return response
            except Exception as exc:
                last_error = exc
                attempts.append({"attempt": attempt_index, "status": "error", "error": str(exc)[:1000],
                                 "actual_calls": actual_calls, "usage": usage,
                                 "raw_hash": sha256_json(raw) if raw is not None else None,
                                 "raw_response": raw})
                if attempt_index < self.max_retries and self.retry_backoff > 0:
                    time.sleep(self.retry_backoff * (attempt_index + 1))
        self._stats["failed_calls"] += 1
        raise LLMRequestError(f"loopback LLM request failed after {len(attempts)} calls: {last_error}",
                              attempts, len(attempts), tokens)


def extract_all_json(text: str) -> list:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    decoder = json.JSONDecoder()
    results = []
    index = 0
    while index < len(text):
        starts = [position for position in (text.find("{", index), text.find("[", index)) if position >= 0]
        if not starts:
            break
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        results.append(value)
        index = start + end
    return results
