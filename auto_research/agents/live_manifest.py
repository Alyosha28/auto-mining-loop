#!/usr/bin/env python3
"""Immutable manifests and append-only checkpoints for live QD smoke runs."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CODE_FILES = (
    "auto_research/agents/live_llm.py",
    "auto_research/agents/live_data.py",
    "auto_research/agents/live_warmup.py",
    "auto_research/agents/live_realdev.py",
    "auto_research/agents/live_formal.py",
    "auto_research/agents/live_analysis.py",
    "auto_research/agents/live_secondary.py",
    "auto_research/agents/live_evaluator.py",
    "auto_research/agents/live_manifest.py",
    "auto_research/agents/live_qd_runner.py",
    "auto_research/agents/expr_grammar.py",
    "auto_research/agents/qd_search.py",
    "auto_research/scripts/expr_search.py",
    "auto_research/benchmark/bench/core.py",
    "skills/current/factor-evolution-harness2/scripts/evo_probe.py",
)


class ResumeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value: Any):
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if hasattr(value, "item") and callable(value.item):
        return value.item()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=json_default)


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_hashes() -> dict[str, str]:
    return {name: sha256_file(ROOT / name) for name in CODE_FILES}


def write_immutable_json(path: Path, payload: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False, default=json_default) + "\n"
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


def append_jsonl(path: Path, payload: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(canonical_json(payload) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[Any]:
    path = Path(path)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def safe_component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"unsafe path component: {value!r}")
    return value


def create_run_directory(output_root: Path, run_id: str, test_mode: bool = True) -> Path:
    safe_component(run_id)
    if test_mode and not run_id.startswith("TEST_"):
        raise ValueError("TEST run_id must start with TEST_")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_dir = output_root / run_id
    run_dir.mkdir(exist_ok=False)
    return run_dir


def frozen_hashes(protocol: dict, model: dict, prompt: dict, data: dict) -> dict:
    return {
        "protocol": {"sha256": sha256_json(protocol), "value": protocol},
        "model": {"sha256": sha256_json(model), "value": model},
        "prompt": {"sha256": sha256_json(prompt), "value": prompt},
        "code": {"files": code_hashes()},
        "data": {"sha256": sha256_json(data), "value": data},
    }


def initialize_manifest(path: Path, kind: str, frozen: dict, **fields) -> Path:
    payload = {
        "schema": "AUTO-RESEARCH-LIVE-QD-MANIFEST-1",
        "kind": kind,
        "created_at": utc_now(),
        "status": "RUNNING",
        "immutable": True,
        "frozen_hashes": frozen,
        **fields,
    }
    return write_immutable_json(path, payload)


def validate_resume(manifest: dict, current_frozen: dict) -> None:
    frozen = manifest.get("frozen_hashes")
    if frozen != current_frozen:
        mismatches = []
        for key in sorted(set(frozen or {}) | set(current_frozen)):
            if (frozen or {}).get(key) != current_frozen.get(key):
                mismatches.append(key)
        raise ResumeError("frozen input/code/protocol changed; resume refused: " + ", ".join(mismatches))
    if manifest.get("immutable") is not True:
        raise ResumeError("manifest is not marked immutable")


def append_checkpoint(path: Path, state: dict) -> dict:
    rows = read_jsonl(path)
    previous = rows[-1]["checkpoint_sha256"] if rows else None
    payload = {
        "schema": "AUTO-RESEARCH-LIVE-QD-CHECKPOINT-1",
        "created_at": utc_now(),
        "sequence": len(rows),
        "previous_sha256": previous,
        "state": state,
    }
    record = {**payload, "checkpoint_sha256": sha256_json(payload)}
    append_jsonl(path, record)
    return record


def load_checkpoints(path: Path, require: bool = True) -> list[dict]:
    rows = read_jsonl(path)
    if require and not rows:
        raise ResumeError(f"no checkpoints under {path}")
    previous = None
    for index, row in enumerate(rows):
        checkpoint_hash = row.get("checkpoint_sha256")
        payload = {key: value for key, value in row.items() if key != "checkpoint_sha256"}
        if row.get("sequence") != index or row.get("previous_sha256") != previous:
            raise ResumeError(f"checkpoint chain break at sequence {index}")
        if sha256_json(payload) != checkpoint_hash:
            raise ResumeError(f"checkpoint hash mismatch at sequence {index}")
        previous = checkpoint_hash
    return rows


def validate_checkpoint_streams(checkpoint: dict, proposal_ids: list[str]) -> None:
    expected = checkpoint["state"].get("proposal_ids", [])
    if proposal_ids != expected:
        raise ResumeError(
            "proposal stream and checkpoint differ; uncheckpointed tail requires manual recovery, not replay"
        )


def file_hashes(directory: Path) -> dict[str, str]:
    directory = Path(directory)
    return {
        str(path.relative_to(directory)): sha256_file(path)
        for path in sorted(directory.rglob("*"))
        if path.is_file() and not path.name.startswith("._")
    }


def assert_finite_json(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value must be serialized as null")
