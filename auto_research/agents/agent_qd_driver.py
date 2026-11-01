#!/usr/bin/env python3
"""Resume the existing QD search after an Agent supplies a missing response."""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "auto_research"))
sys.path.insert(0, str(ROOT / "auto_research/scripts"))
os.environ["AUTO_RESEARCH_LLM_PROVIDER"] = "opencode"
os.environ["AUTO_RESEARCH_LLM_MODEL"] = "deepseek-v4.1-flash"
os.environ.pop("AUTO_RESEARCH_LLM_REASONING_EFFORT", None)

import numpy as np
import pandas as pd

import expr_search as es
from agents import llm, qd_search, sanitize
from agents.live_manifest import append_jsonl, read_json, sha256_file, utc_now, write_immutable_json


class CacheMiss(BaseException):
    pass


class BatchLimit(BaseException):
    pass


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python auto_research/agents/agent_qd_driver.py RUN_DIRECTORY")
    run_dir = Path(sys.argv[1]).resolve()
    protocol_path = run_dir / "protocol.json"
    protocol = read_json(protocol_path)
    result_path = ROOT / protocol["result_path"]
    if result_path.is_file():
        print(json.dumps({"status": "COMPLETED", "result_path": str(result_path)}, ensure_ascii=False))
        return
    code = {name: sha256_file(ROOT / name) for name in (
        "auto_research/agents/agent_qd_driver.py", "auto_research/agents/qd_search.py",
        "auto_research/agents/llm.py", "auto_research/scripts/expr_search.py")}
    manifest_path = run_dir / "manifest.json"
    if manifest_path.is_file():
        if read_json(manifest_path)["code"] != code or read_json(manifest_path)["protocol_sha256"] != sha256_file(protocol_path):
            raise RuntimeError("protocol or code changed; resume refused")
    else:
        write_immutable_json(manifest_path, {
            "schema": "AUTO-RESEARCH-AGENT-QD-DRIVER-1", "created_at": utc_now(),
            "run_id": protocol["run_id"], "code": code, "protocol_sha256": sha256_file(protocol_path),
            "resume_command": f"python auto_research/agents/agent_qd_driver.py {run_dir}",
            "score_window_read": False, "oos_window_read": False, "embargo_read": False,
            "primary_analysis_performed": False})
    llm.CACHE = run_dir / "cache"
    llm.CACHE.mkdir(exist_ok=True)
    original_evaluate = es.evaluate_expr

    def checked_evaluate(node, panel, dates):
        result = original_evaluate(node, panel, dates)
        coverage = result.get("coverage") if result else None
        n_dates = result.get("n_dates") if result else None
        if result is None:
            values = es.eval_tree(node, panel)
            if values is not None:
                values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
                _, n_dates = es.EP.ic_series(panel, values, dates)
                coverage = n_dates / len(dates)
        if coverage is not None:
            append_jsonl(run_dir / "coverage.jsonl", {
                "formula": es.canonical(node), "coverage": coverage, "n_dates": n_dates,
                "planned_dates": len(dates), "created_at": utc_now()})
            if coverage < 0.96 and protocol.get("coverage_stop", True):
                raise RuntimeError(f"coverage stop: {coverage:.6f} < 0.96")
        return result

    es.evaluate_expr = checked_evaluate

    def agent_response(messages, model=None, temperature=0.2, max_tokens=2400,
                       retries=2, response_format=None):
        payload = {"model": model or llm.MODEL, "messages": messages,
                   "temperature": float(temperature), "max_tokens": int(max_tokens)}
        if response_format:
            payload["response_format"] = response_format
        key = llm._key(payload)
        cache_path = llm.CACHE / f"{key}.json"
        if cache_path.is_file():
            return read_json(cache_path)["content"]
        requests = list(run_dir.glob("request_*.json"))
        if len(requests) >= protocol["max_batches"]:
            raise BatchLimit(f"generation batch limit reached: {len(requests)}")
        request_path = run_dir / f"request_{key}.json"
        write_immutable_json(request_path, {
            "schema": "AUTO-RESEARCH-AGENT-QD-REQUEST-1", "created_at": utc_now(),
            "request_key": key, "payload": payload, "model": "opencode-go/deepseek-v4.1-flash",
            "direct_api_call": False, "market_rows_in_prompt": False,
            "score_window_read": False, "oos_window_read": False, "embargo_read": False})
        raise CacheMiss(str(request_path))

    llm.chat = agent_response
    if protocol.get("expose_feature_names"):
        sanitize.alias_map = lambda features: None
    sys.argv = ["qd_search.py", *protocol["qd_arguments"]]
    started = time.perf_counter()
    try:
        qd_search.main()
    except CacheMiss as exc:
        append_jsonl(run_dir / "invocations.jsonl", {
            "created_at": utc_now(), "status": "NEED_AGENT_RESPONSE", "request": str(exc),
            "wall_seconds": time.perf_counter() - started})
        print(json.dumps({"status": "NEED_AGENT_RESPONSE", "request": str(exc)}, ensure_ascii=False))
        return
    except BatchLimit as exc:
        write_immutable_json(run_dir / "incomplete.json", {
            "created_at": utc_now(), "status": "INCOMPLETE_BATCH_LIMIT", "error": str(exc),
            "budget": protocol["budget"], "primary_analysis_performed": False})
        raise
    except RuntimeError as exc:
        status = "COVERAGE_STOP" if str(exc).startswith("coverage stop:") else "INCOMPLETE_ERROR"
        write_immutable_json(run_dir / "incomplete.json", {
            "created_at": utc_now(), "status": status, "error": str(exc),
            "budget": protocol["budget"], "primary_analysis_performed": False})
        raise
    append_jsonl(run_dir / "invocations.jsonl", {
        "created_at": utc_now(), "status": "COMPLETED", "result_path": str(result_path),
        "wall_seconds": time.perf_counter() - started})


if __name__ == "__main__":
    main()
