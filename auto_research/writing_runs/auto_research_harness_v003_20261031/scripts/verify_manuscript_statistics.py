"""Verify manuscript statistics with the frozen V003 analysis implementation."""

from __future__ import annotations

import json
from pathlib import Path
import random
import sys


RUN = Path(__file__).resolve().parents[1]
PROJECT = RUN.parents[2]
SCRIPTS = PROJECT / "skills/current/factor-evolution-harness2/scripts"
sys.path.insert(0, str(SCRIPTS))
import run_paper_readiness_v003 as v003


FIELDS = [
    "median", "mean", "min", "max", "task_medians", "positive_tasks",
    "task_bootstrap_95pct_interval", "exact_task_sign_flip_p", "delta_counts",
]


def main() -> None:
    packet = json.loads((RUN / "evidence/results_packet.json").read_text(encoding="utf-8"))
    protocol = packet["protocol"]
    tasks = [Path(filename).stem for filename in protocol["task_files"]]
    units = packet["tables"]["unit_effects"]
    rng = random.Random(protocol["analysis"]["bootstrap_seed"])
    indices = [
        tuple(rng.randrange(len(tasks)) for _ in tasks)
        for _ in range(protocol["analysis"]["task_bootstrap_samples"])
    ]
    report = {
        "schema": "AUTO-RESEARCH-MANUSCRIPT-STATISTICAL-VERIFICATION-1",
        "status": "PASS",
        "implementation": "skills/current/factor-evolution-harness2/scripts/run_paper_readiness_v003.py",
        "bootstrap_statistic": "median across all 24 unit effects after resampling six complete task clusters",
        "sign_flip_statistic": "median across all 24 unit effects after applying one sign to all four units within each task",
        "bootstrap_samples": protocol["analysis"]["task_bootstrap_samples"],
        "sign_assignments": 2 ** len(tasks),
        "contrasts": {},
    }
    component_p = {}
    for name, expected in packet["contrasts"].items():
        actual = v003.contrast_analysis(units, tasks, name, indices)
        for field in FIELDS:
            assert actual[field] == expected[field], (name, field, actual[field], expected[field])
        component_p[name] = actual["exact_task_sign_flip_p"]
        report["contrasts"][name] = {
            "task_bootstrap_95pct_interval": actual["task_bootstrap_95pct_interval"],
            "exact_task_sign_flip_p": actual["exact_task_sign_flip_p"],
            "source_fields_reproduced": True,
        }
    component_names = ["memory_minus_scalar", "diagnostic_minus_memory"]
    adjusted = v003.holm({name: component_p[name] for name in component_names})
    for name, value in adjusted.items():
        assert packet["contrasts"][name]["holm_adjusted_p"] == value
        report["contrasts"][name]["holm_adjusted_p"] = value
    (RUN / "statistical_verification.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "contrasts": len(report["contrasts"]), "sign_assignments": report["sign_assignments"]}))


if __name__ == "__main__":
    main()
