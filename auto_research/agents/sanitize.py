#!/usr/bin/env python3
"""Optional feature-alias mapping for external-LLM prompts.

Runs may expose feature names when authorized. Raw market rows and closed-window
outcomes must not be sent. Responses are mapped back locally before evaluation.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .expr_grammar import BINARY_OPS, UNARY_OPS

RESERVED = {op.lower() for op in (UNARY_OPS | BINARY_OPS)} | {'f'}

CACHE = Path(__file__).resolve().parent / 'cache'
MAP_PATH = CACHE / 'feature_aliases.json'


def alias_map(features: list[str]) -> dict[str, str]:
    """Stable alias map feature -> Fxx, ordered by name for reproducibility."""
    if MAP_PATH.is_file():
        data = json.loads(MAP_PATH.read_text(encoding='utf-8'))
        if set(data) == set(features):
            return data
    ordered = sorted(features)
    mapping = {feature: f'F{index:02d}' for index, feature in enumerate(ordered, start=1)}
    MAP_PATH.write_text(json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    return mapping


def to_alias(text: str, mapping: dict[str, str]) -> str:
    for feature in sorted(mapping, key=len, reverse=True):
        text = text.replace(feature, mapping[feature])
    return text


def scrub(text: str) -> str:
    """Replace unmapped feature-like tokens with deterministic placeholders."""
    def replace(match: re.Match) -> str:
        token = match.group(0)
        name = token.lower()
        if name in RESERVED or re.fullmatch(r'[A-Z]\d{2}', token) or token.startswith('CLAIM'):
            return token
        digest = hashlib.sha1(token.encode('utf-8')).hexdigest()[:4]
        return f'FEAT_{digest}'
    return re.sub(r'\b[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b', replace, text)


def from_alias(text: str, mapping: dict[str, str]) -> str:
    inverse = {alias: feature for feature, alias in mapping.items()}
    for alias in sorted(inverse, key=len, reverse=True):
        text = text.replace(alias, inverse[alias])
    return text


if __name__ == '__main__':
    print('module ok')
