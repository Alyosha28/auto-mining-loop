#!/usr/bin/env python3
"""Parse LLM-proposed expression strings into the expr_search AST grammar."""
from __future__ import annotations

import re

UNARY_OPS = {'rank', 'zscore', 'winsorize', 'vol_scaled', 'tanh', 'sign',
             'residual_size', 'delta4', 'delta13', 'delta_w', 'ema5', 'lag5', 'neg'}
BINARY_OPS = {'ratio', 'diff', 'rank_minus', 'product'}


def parse(expression: str, features: set[str]) -> dict:
    text = expression.strip()
    if not text or ';' in text or '=' in text:
        raise ValueError(f'not a single expression: {expression!r}')
    position = 0

    def parse_node() -> dict:
        nonlocal position
        while position < len(text) and text[position].isspace():
            position += 1
        match = re.match(r'([A-Za-z_][A-Za-z0-9_]*)', text[position:])
        if not match:
            raise ValueError(f'unexpected token at {position}: {text[position:position + 20]!r}')
        name = match.group(1)
        position += len(name)
        while position < len(text) and text[position].isspace():
            position += 1
        if position < len(text) and text[position] == '(':
            position += 1
            left = parse_node()
            while position < len(text) and text[position].isspace():
                position += 1
            if position < len(text) and text[position] == ',':
                position += 1
                right = parse_node()
                while position < len(text) and text[position].isspace():
                    position += 1
                if position >= len(text) or text[position] != ')':
                    raise ValueError('missing closing parenthesis')
                position += 1
                if name not in BINARY_OPS:
                    raise ValueError(f'unknown binary op: {name}')
                return {'kind': 'binary', 'op': name, 'left': left, 'right': right}
            if position >= len(text) or text[position] != ')':
                raise ValueError('missing closing parenthesis')
            position += 1
            if name not in UNARY_OPS:
                raise ValueError(f'unknown unary op: {name}')
            return {'kind': 'unary', 'op': name, 'arg': left}
        if name not in features:
            raise ValueError(f'unknown feature: {name}')
        return {'kind': 'leaf', 'feature': name}

    node = parse_node()
    if position != len(text):
        raise ValueError(f'trailing characters: {text[position:]!r}')
    return node


def validate(node: dict, features: set[str]) -> None:
    kind = node['kind']
    if kind == 'leaf':
        if node['feature'] not in features:
            raise ValueError(f"feature not in pool: {node['feature']}")
    elif kind == 'unary':
        if node['op'] not in UNARY_OPS:
            raise ValueError(f"bad unary op: {node['op']}")
        validate(node['arg'], features)
    elif kind == 'binary':
        if node['op'] not in BINARY_OPS:
            raise ValueError(f"bad binary op: {node['op']}")
        validate(node['left'], features)
        validate(node['right'], features)
    else:
        raise ValueError(f'bad node kind: {kind}')
