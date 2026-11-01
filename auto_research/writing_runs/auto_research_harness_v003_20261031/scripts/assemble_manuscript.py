"""Assemble the accepted controller draft and verify manuscript-wide mechanical contracts."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re


RUN = Path(__file__).resolve().parents[1]
CONTENT_UNITS = [
    "02_introduction", "03_related", "04_problem", "05_harness", "06_information",
    "07_protocol", "08_metrics", "09_analysis", "10_primary", "11_components",
    "12_behavior", "13_execution", "14_discussion", "15_limitations", "16_conclusion", "17_appendix",
]
BANNED = re.compile(
    r"(?i)it is important to note|it should be noted|it is worth noting|we must emphasize|"
    r"one might argue|may potentially|might possibly|as previously mentioned|this section will|in summary"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def build() -> None:
    parts = []
    for unit in CONTENT_UNITS:
        path = RUN / f"drafts/{unit}.tex"
        if not path.is_file():
            raise FileNotFoundError(path)
        parts.append(read(path).strip())
    content = "\n\n".join(parts) + "\n"
    (RUN / "content.tex").write_text(content, encoding="utf-8")
    check()


def check() -> None:
    content = read(RUN / "content.tex")
    abstract = read(RUN / "drafts/01_abstract.tex")
    bibliography = read(RUN / "references.bib")
    main = read(RUN / "main.tex")
    word_count = len(re.findall(r"\b[\w'-]+\b", re.sub(r"\$[^$]*\$", "", abstract)))
    if word_count > 250:
        raise ValueError(f"Abstract has {word_count} words")
    citations = []
    for group in re.findall(r"\\cite[tp]\{([^}]+)\}", content):
        citations.extend(key.strip() for key in group.split(","))
    bib_keys = re.findall(r"@\w+\{([^,]+),", bibliography)
    literature = json.loads(read(RUN / "evidence/literature.json"))["entries"]
    if set(bib_keys) != set(literature):
        raise ValueError(f"Literature evidence mismatch: bibliography={sorted(set(bib_keys) - set(literature))}, evidence={sorted(set(literature) - set(bib_keys))}")
    incomplete_literature = sorted(key for key, record in literature.items() if not record.get("supported_claim") or not record.get("source_urls"))
    if incomplete_literature:
        raise ValueError(f"Incomplete literature evidence: {incomplete_literature}")
    missing = sorted(set(citations) - set(bib_keys))
    uncited = sorted(set(bib_keys) - set(citations))
    if missing or uncited:
        raise ValueError(f"Citation mismatch: missing={missing}, uncited={uncited}")
    labels = re.findall(r"\\label\{([^}]+)\}", content)
    duplicates = sorted(key for key, count in Counter(labels).items() if count > 1)
    if duplicates:
        raise ValueError(f"Duplicate labels: {duplicates}")
    figures = re.findall(r"\\includegraphics\[[^]]+\]\{([^}]+)\}", content)
    missing_figures = []
    for path in figures:
        if "\\ResearchFigureVariant" in path:
            for variant in ["review_wide", "preview_wide"]:
                resolved = path.replace("\\ResearchFigureVariant", variant)
                if not (RUN / resolved).is_file():
                    missing_figures.append(resolved)
        elif not (RUN / path).is_file():
            missing_figures.append(path)
    if missing_figures:
        raise ValueError(f"Missing figures: {missing_figures}")
    if BANNED.search(content):
        raise ValueError(f"Mechanical or defensive phrase found: {BANNED.search(content).group(0)}")
    if "\\begin{itemize}" in content or "\\begin{enumerate}" in content:
        raise ValueError("List-form manuscript prose detected")
    if len(re.findall(r"\\section\{", content)) != 16:
        raise ValueError("Unexpected section count")
    if content.count("\\appendix") != 1:
        raise ValueError("Expected one appendix marker")
    if "ResearchWideWidth" not in main or "drafts/01_abstract.tex" not in main:
        raise ValueError("Main manuscript contract is incomplete")
    report = {
        "schema": "AUTO-RESEARCH-MANUSCRIPT-MECHANICAL-CHECK-1",
        "status": "PASS",
        "content_units": len(CONTENT_UNITS),
        "sections": 16,
        "abstract_words": word_count,
        "citations": sorted(set(citations)),
        "bibliography_entries": len(bib_keys),
        "labels": len(labels),
        "figures": len(figures),
        "list_environments": 0,
        "banned_style_phrases": 0,
    }
    (RUN / "mechanical_check.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else build()
