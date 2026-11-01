"""Fresh, loopback-only model requests for research hypotheses, never alpha claims.

Generation receives a field contract only. Novelty review is a separate request
with a score-free library catalogue. No model-generated Python is executed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import urllib.parse
import urllib.request
from pathlib import Path

FIELDS = {
    "ret": "close/previous close - 1; daily raw-price return; action windows excluded",
    "intraday": "close/open - 1; daily intraday return",
    "overnight": "open/previous close - 1; action windows excluded",
    "range_frac": "(high-low)/close; zero is a valid observed range",
    "close_location": "(close-low)/(high-low); flat range gives missing, not zero",
    "volume": "daily traded shares, not turnover or signed order flow",
    "amount": "daily traded CNY, not signed buying pressure",
    "turnover": "daily turnover as a fraction, vendor historical denominator conditional",
}
CONTRACT = {
    "market": "A-share daily quotes; cross-sectional signals computed after the last trading-session close of each calendar week",
    "label_horizon": "Predict return from the NEXT trading-session OPEN after the signal to the NEXT trading-session OPEN after the following week's signal (about one trading week). The intervening signal-close to next-open gap is NOT earned. This is NOT a prediction of the immediately next opening gap. All mechanism and falsification statements must concern this exact holding interval.",
    "direction": "expected_sign=+1 means larger raw expression predicts larger forward one-week open-to-open return; -1 means smaller raw expression predicts larger return. Direction is frozen before outcomes; do not imply both signs are success.",
    "fields": FIELDS,
    "operators": "Use +, -, *, div(a,b), abs(a), sign(a), lag(a,n), mean(a,n), std(a,n), sum(a,n), max(a,n), min(a,n), corr(a,b,n). No other functions. Windows integer 1..60, total lookback <=252. div returns missing on a zero denominator. Rolling windows require every session; missing sessions are never compressed or zero-filled.",
    "data_boundary": "No future returns, outcomes, historical scores, current factor library, prior chat or other scouts are supplied. Unknown news/financial/order-book data cannot be assumed. Mechanistic novelty is a hypothesis, not an originality or efficacy claim.",
    "timing": "Use only current and past sessions, complete exchange calendar; any corporate action in dependency window invalidates price-dependent signal. No same-close execution. Source universe/PIT completeness remains conditional.",
}
IDEA_KEYS = ("id", "name", "mechanism", "rationale", "expression", "fields", "expected_sign", "failure_mode", "falsification")
IDEA_SCHEMA = {"type": "object", "additionalProperties": False,
    "properties": {"ideas": {"type": "array", "minItems": 1, "maxItems": 4,
    "items": {"type": "object", "additionalProperties": False,
    "properties": {**{k: {"type": "string"} for k in IDEA_KEYS if k not in ("fields", "expected_sign")},
        "fields": {"type": "array", "items": {"type": "string", "enum": list(FIELDS)}},
        "expected_sign": {"type": "integer", "enum": [-1, 1]}}, "required": list(IDEA_KEYS)}}}, "required": ["ideas"]}
REVIEW_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "reviews": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {k: {"type": "string"} for k in ["id", "nearest_existing", "overlap", "semantic_problem", "empirical_test"]},
        "required": ["id", "nearest_existing", "overlap", "semantic_problem", "empirical_test"]}},
    "shortlist": {"type": "array", "maxItems": 3, "items": {"type": "string"}}}, "required": ["reviews", "shortlist"]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


def validate_endpoint(base):
    u = urllib.parse.urlsplit(base)
    if u.scheme != "http" or u.hostname not in ("127.0.0.1", "::1") or u.username or u.password or u.query or u.fragment or u.path.rstrip("/") != "/v1":
        raise ValueError("Only a literal loopback http://IP:port/v1 endpoint is allowed")
    if not u.port:
        raise ValueError("explicit local port required")
    return base.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("local model redirect refused")


def request_model(base, model, system, payload, output, *, schema=None, seed=1, max_tokens=3500):
    """One stateless request. No API key, proxies, redirects, tools or chat IDs."""
    base = validate_endpoint(base)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    body = {"model": model, "messages": [{"role": "system", "content": system},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        "temperature": 0.65, "seed": seed, "max_tokens": max_tokens, "stream": False}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "research_result", "strict": True, "schema": schema}}
    write_json(output/"request.json", body)
    evidence = {"endpoint": base, "request_sha256": digest(body), "requested_model": model,
        "fresh_messages": True, "message_count": 2, "tools": False, "cloud_fallback": False}
    write_json(output/"receipt.json", {**evidence, "status": "REQUESTED"})
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        request = urllib.request.Request(base+"/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
        with opener.open(request, timeout=300) as response:
            raw = json.load(response)
        write_json(output/"response.json", raw)
        choice = raw["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("incomplete model response: "+str(choice.get("finish_reason")))
        content = choice["message"]["content"]
        # Some runtimes emit an optional Markdown fence despite JSON instructions.
        if content.strip().startswith("```"):
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        result = json.loads(content)
        write_json(output/"result.json", result)
        write_json(output/"receipt.json", {**evidence, "status": "MODEL_RESPONSE_RECEIVED",
            "response_model": raw.get("model"), "usage": raw.get("usage"), "response_sha256": digest(raw)})
        return result
    except Exception as exc:
        write_json(output/"receipt.json", {**evidence, "status": "FAILED", "error": type(exc).__name__+": "+str(exc)[:400]})
        raise


def expression_info(expression):
    """Validate a small numerical AST; determine exact used fields and lookback."""
    if not isinstance(expression, str) or len(expression) > 2000:
        raise ValueError("bounded expression string required")
    root = ast.parse(expression, mode="eval")
    if len(list(ast.walk(root))) > 160:
        raise ValueError("expression too complex")
    fields = set()
    def walk(node, depth=0):
        if depth > 14: raise ValueError("expression too deep")
        if isinstance(node, ast.Name) and node.id in FIELDS:
            fields.add(node.id)
            return 1 if node.id in ("ret", "overnight") else 0
        if isinstance(node, ast.Constant) and type(node.value) in (int, float) and abs(node.value) <= 1e6:
            return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return walk(node.operand, depth+1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
            return max(walk(node.left, depth+1), walk(node.right, depth+1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            arities = {"abs": 1, "sign": 1, "div": 2, "lag": 2, "mean": 2, "std": 2, "sum": 2, "max": 2, "min": 2, "corr": 3}
            if name not in arities or len(node.args) != arities[name]: raise ValueError("unsupported function/arity")
            if name in ("abs", "sign", "div"):
                return max(walk(n, depth+1) for n in node.args)
            n = node.args[-1]
            if not isinstance(n, ast.Constant) or type(n.value) is not int or not 1 <= n.value <= 60:
                raise ValueError("window must be integer 1..60")
            return max(walk(a, depth+1) for a in node.args[:-1]) + (n.value if name == "lag" else n.value-1)
        raise ValueError("unsupported expression syntax")
    lookback = walk(root.body)
    if not fields or lookback > 252: raise ValueError("empty fields or excessive lookback")
    return {"fields": sorted(fields), "lookback_sessions": lookback,
        "fingerprint": hashlib.sha256(ast.dump(root, include_attributes=False).encode()).hexdigest()}


def validate_ideas(result, scout):
    if set(result) != {"ideas"} or not isinstance(result["ideas"], list) or not 1 <= len(result["ideas"]) <= 4:
        raise ValueError("invalid idea envelope")
    records = []
    for i, idea in enumerate(result["ideas"], 1):
        row = dict(idea)
        row["id"] = f"{scout}_{i:02d}"
        row["model_proposed_id"] = idea.get("id")
        try:
            if set(idea) != set(IDEA_KEYS) or any(not isinstance(idea[k], str) or not idea[k].strip() or idea[k].strip() in ('...', '…', 'TODO', 'TBD') for k in IDEA_KEYS if k not in ("fields", "expected_sign")):
                raise ValueError("incomplete idea schema")
            if type(idea["expected_sign"]) is not int or idea["expected_sign"] not in (-1, 1): raise ValueError("direction required")
            info = expression_info(idea["expression"])
            if not isinstance(idea["fields"], list) or set(idea["fields"]) != set(info["fields"]): raise ValueError("declared and computed fields differ")
            row.update(info, status="READY_FOR_EMPIRICAL_TEST", alpha_validated=False)
        except (ValueError, SyntaxError, TypeError) as exc:
            row.update(status="INVALID_EXPRESSION", reason=str(exc), alpha_validated=False)
        records.append(row)
    return records


def validate_evolution(result, parent_ids):
    """Use explicit lineage, never response order, to associate mutations."""
    if not isinstance(result, dict) or not isinstance(result.get('ideas'), list):
        raise ValueError('invalid evolution response')
    parents=[r.get('parent_id') for r in result['ideas']]
    if len(parents)!=len(parent_ids) or set(parents)!=set(parent_ids):
        raise ValueError('one child per declared parent required')
    clean={'ideas':[{k:v for k,v in row.items() if k!='parent_id'} for row in result['ideas']]}
    rows=validate_ideas(clean,'evolution')
    for row,parent in zip(rows,parents):row['parent_id']=parent
    return rows


def mechanical_findings(idea):
    """Bounded algebra checks; these do not certify an economic mechanism."""
    findings=[]
    try:
        info=expression_info(idea['expression'])
        if set(idea.get('fields',[]))!=set(info['fields']):findings.append('Declared fields differ from fields used in the expression.')
        root=ast.parse(idea['expression'],mode='eval')
        for n in ast.walk(root):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='sign' and len(n.args)==1 and isinstance(n.args[0],ast.Name) and n.args[0].id=='close_location':
                findings.append('close_location is in [0,1]: sign(close_location) cannot encode high versus low close position.')
    except (KeyError,ValueError,SyntaxError) as exc:findings.append(str(exc))
    if idea.get('status')=='INVALID_EXPRESSION':findings.append(idea.get('reason','Invalid input contract.'))
    return list(dict.fromkeys(findings))


def validate_review(review, idea_ids):
    if not isinstance(review,dict) or set(review)!= {'reviews','shortlist'}:
        raise ValueError('invalid review envelope')
    ids=[r['id'] for r in review['reviews']]
    if len(ids)!=len(idea_ids) or set(ids)!=set(idea_ids):raise ValueError('review must cover each idea exactly once')
    if len(review['shortlist'])>3 or len(set(review['shortlist']))!=len(review['shortlist']) or not set(review['shortlist'])<=set(idea_ids):
        raise ValueError('invalid review shortlist')
    return review


def evaluate_expression(expression, frame):
    """Interpret validated data-only AST on one calendar-aligned stock. Never eval."""
    import numpy as np
    import pandas as pd
    info = expression_info(expression)
    def series(x):
        return x if isinstance(x, pd.Series) else pd.Series(float(x), index=frame.index)
    def calc(node):
        if isinstance(node, ast.Name): return frame[node.id].astype(float)
        if isinstance(node, ast.Constant): return float(node.value)
        if isinstance(node, ast.UnaryOp): return -calc(node.operand) if isinstance(node.op, ast.USub) else calc(node.operand)
        if isinstance(node, ast.BinOp):
            a,b=calc(node.left),calc(node.right)
            if isinstance(node.op, ast.Add): return a+b
            if isinstance(node.op, ast.Sub): return a-b
            return a*b
        name=node.func.id
        args=[calc(a) for a in node.args]
        a=series(args[0])
        if name=="abs": return a.abs()
        if name=="sign": return np.sign(a)
        if name=="div": return a/series(args[1]).where(series(args[1]).ne(0))
        n=int(args[-1])
        if name=="lag": return a.shift(n)
        rolling=a.rolling(n,min_periods=n)
        if name=="corr": return rolling.corr(series(args[1]))
        return getattr(rolling,name)()
    with np.errstate(all="ignore"):
        result=series(calc(ast.parse(expression,mode="eval").body)).replace([np.inf,-np.inf],np.nan)
    # Require every input field throughout the entire dependency span. Conservative
    # masking prevents nested lag/rolling expressions from skipping missing sessions.
    complete=frame[info["fields"]].notna().all(axis=1).rolling(info["lookback_sessions"]+1,min_periods=info["lookback_sessions"]+1).sum().eq(info["lookback_sessions"]+1)
    return result.where(complete)
