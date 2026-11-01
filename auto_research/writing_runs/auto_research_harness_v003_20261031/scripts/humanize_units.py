"""Polish manuscript prose with the local humanizer while preserving LaTeX and numbers."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import subprocess


RUN = Path(__file__).resolve().parents[1]
OUTPUT = RUN / "local_candidates"


def letters(index: int) -> str:
    result = ""
    value = index + 1
    while value:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def protect(draft: str) -> tuple[str, dict[str, str]]:
    mapping: dict[str, str] = {}
    patterns = [
        r"\\begin\{(figure\*?|table\*?|equation)\}.*?\\end\{\1\}",
        r"\\(?:section|subsection)\{[^}]+\}",
        r"\\appendix",
        r"\\label\{[^}]+\}",
        r"\\cite[tp]\{[^}]+\}",
        r"\\texttt\{[^}]+\}",
        r"\$[^$]*\$",
    ]
    combined = re.compile("|".join(f"(?:{pattern})" for pattern in patterns), re.S)

    def replace(match: re.Match) -> str:
        token = f"XQZPROTECT{letters(len(mapping))}QZX"
        mapping[token] = match.group(0)
        return token

    return combined.sub(replace, draft), mapping


def numeric_tokens(text: str) -> Counter:
    return Counter(re.findall(r"(?<![A-Za-z])\d[\d,]*(?:\.\d+)?", text))


def process(unit: str) -> None:
    source = RUN / f"drafts/{unit}.tex"
    protected_dir = OUTPUT / "protected"
    raw_dir = OUTPUT / "raw"
    protected_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    draft = source.read_text(encoding="utf-8")
    protected, mapping = protect(draft)
    protected_path = protected_dir / f"{unit}.txt"
    raw_path = raw_dir / f"{unit}.txt"
    candidate_path = OUTPUT / f"{unit}.tex"
    protected_path.write_text(protected, encoding="utf-8")
    text_paragraphs = [p for p in re.split(r"\n\s*\n", protected) if p.strip()]
    command = ["humanize", "--auto-up", "-f", str(protected_path), "-o", str(raw_path)]
    if len(text_paragraphs) > 3:
        command.append("--paragraphs")
    completed = subprocess.run(command, text=True, capture_output=True, timeout=900)
    (raw_dir / f"{unit}.log").write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise RuntimeError(f"humanize failed for {unit}: {completed.stdout}{completed.stderr}")
    candidate = raw_path.read_text(encoding="utf-8").strip() + "\n"
    missing = [token for token in mapping if token not in candidate]
    if missing:
        raise RuntimeError(f"Protected LaTeX markers missing for {unit}: {missing}")
    for token, original in mapping.items():
        candidate = candidate.replace(token, original)
    if "XQZPROTECT" in candidate:
        raise RuntimeError(f"Unresolved protected marker in {unit}")
    if numeric_tokens(draft) != numeric_tokens(candidate):
        raise RuntimeError(f"Numeric drift detected for {unit}")
    candidate_path.write_text(candidate, encoding="utf-8")
    manifest = {
        "schema": "AUTO-RESEARCH-LOCAL-CANDIDATE-1",
        "unit": unit,
        "model": "humanizer-12b-optiq",
        "source": str(source),
        "raw_response": str(raw_path),
        "candidate": str(candidate_path),
        "protected_latex_spans": len(mapping),
        "paragraph_mode": len(text_paragraphs) > 3,
        "numeric_multiset_preserved": True,
        "status": "GENERATED_AWAITING_REVIEW",
    }
    (OUTPUT / f"{unit}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"unit": unit, "status": manifest["status"], "protected_spans": len(mapping)}))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", action="append")
    args = parser.parse_args()
    units = args.unit or [path.stem for path in sorted((RUN / "drafts").glob("[0-9][0-9]_*.tex"))]
    for unit in units:
        process(unit)


if __name__ == "__main__":
    main()
