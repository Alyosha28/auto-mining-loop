"""Select an already loaded LM Studio LLM without changing runtime state.

The only command is ``lms ps --json``. Disk inventories and model-name guesses
are not evidence of a loaded generator. No load/unload, downloads, server
startup, model invocation or cloud fallback is performed here.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess


def select_loaded_model(models, model=None):
    """Return an exact loaded LLM identifier, with ambiguity failing closed."""
    if not isinstance(models, list) or any(not isinstance(row, dict) for row in models):
        raise ValueError("lms ps --json must return a list of model objects")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError("--model must be a nonempty exact identifier or modelKey")
    loaded = [row for row in models if row.get("type") == "llm"]
    identifiers = [row.get("identifier") for row in loaded]
    if any(not isinstance(identifier, str) or not identifier.strip() for identifier in identifiers):
        raise ValueError("loaded LLM is missing a valid runtime identifier")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("loaded LLM runtime identifiers are ambiguous")
    if not loaded:
        raise ValueError("no loaded type=llm model is available; embedding models cannot generate cards")
    if model is None:
        if len(loaded) != 1:
            raise ValueError("multiple LLMs are loaded; --model must exactly match an identifier or modelKey")
        return identifiers[0]
    matches = [row for row in loaded if model == row["identifier"] or model == row.get("modelKey")]
    if len(matches) != 1:
        raise ValueError("--model must exactly match one loaded type=llm identifier or modelKey")
    return matches[0]["identifier"]


def resolve_loaded_model(model=None):
    """Read the current loaded-model inventory once and select a generator."""
    try:
        result = subprocess.run(["lms", "ps", "--json"], check=True, capture_output=True,
                                text=True, shell=False, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("cannot inspect loaded LM Studio models with lms ps --json") from exc
    try:
        models = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("lms ps --json returned invalid JSON") from exc
    return select_loaded_model(models, model)


def verify_response_model(output, identifier):
    """Require the saved response identity and retain a separate check record.

    The shared client's request, response and receipt files are preserved,
    including mismatches. A missing/invalid receipt also fails explicitly.
    """
    output = Path(output)
    receipt_hash, actual, error = None, None, None
    try:
        payload = (output / "receipt.json").read_bytes()
        receipt_hash = hashlib.sha256(payload).hexdigest()
        receipt = json.loads(payload)
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be an object")
        actual = receipt.get("response_model")
        if actual != identifier:
            error = "response model identity does not match the selected loaded identifier"
    except (OSError, ValueError) as exc:
        error = "response model identity cannot be verified: " + type(exc).__name__
    check = {"status": "FAIL" if error else "PASS", "expected_identifier": identifier,
             "response_model": actual, "receipt_sha256": receipt_hash, "error": error}
    with (output / "model_identity_check.json").open("x", encoding="utf-8") as handle:
        json.dump(check, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    if error:
        raise ValueError(error)
    return check
