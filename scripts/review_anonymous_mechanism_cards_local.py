#!/usr/bin/env python3
"""Ask an already loaded local LLM to review anonymous mechanism cards."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from research.local_idea_generation import request_model, write_json
from research.local_model_runtime import resolve_loaded_model, verify_response_model

REVIEW = {
    "type": "object", "additionalProperties": False,
    "properties": {"reviews": {"type": "array", "minItems": 1, "items": {
        "type": "object", "additionalProperties": False,
        "properties": {
            "anonymous_id": {"type": "string"}, "field_timing": {"type": "string", "enum": ["pass", "fail"]},
            "mechanism_overlap": {"type": "string"}, "simplest_alternative": {"type": "string"},
            "required_control": {"type": "string"}, "refutation_quality": {"type": "string", "enum": ["strong", "weak", "invalid"]},
            "recommendation": {"type": "string", "enum": ["compile", "reject", "revise"]}, "reason": {"type": "string"}},
        "required": ["anonymous_id", "field_timing", "mechanism_overlap", "simplest_alternative", "required_control", "refutation_quality", "recommendation", "reason"]}}},
    "required": ["reviews"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", help="Exact loaded LM Studio identifier or modelKey; optional only with one loaded LLM")
    parser.add_argument("--max-tokens", type=int, default=5000)
    args = parser.parse_args(argv)
    if args.max_tokens <= 0:
        parser.error("--max-tokens must be positive")
    model = resolve_loaded_model(args.model)
    package = json.loads(args.package.read_text())
    expected = [card["anonymous_id"] for card in package["cards"]]
    result = request_model("http://127.0.0.1:1234/v1", model,
                           "You are a skeptical quantitative research reviewer. Review only the anonymous cards supplied. No empirical outcomes are available. Return strict JSON.",
                           package, args.output, schema=REVIEW, seed=20260929, max_tokens=args.max_tokens)
    verify_response_model(args.output, model)
    returned = [review["anonymous_id"] for review in result.get("reviews", [])]
    if sorted(returned) != sorted(expected) or len(returned) != len(set(returned)):
        raise ValueError("review must cover every anonymous card exactly once")
    write_json(args.output / "validated_review.json", result)
    print("ANONYMOUS_REVIEWS_RECEIVED")


if __name__ == "__main__":
    main()
