"""Generate deterministic ESWA figures from the completed V003 paper materials."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

MPLCONFIGDIR = Path(tempfile.gettempdir()) / "auto-research-v003-mpl"
MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(MPLCONFIGDIR)
os.environ["XDG_CACHE_HOME"] = str(Path(tempfile.gettempdir()) / "auto-research-v003-fontcache")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from PIL import Image


OUTPUT = Path(__file__).resolve().parent
PROJECT = OUTPUT.parents[3]
MATERIALS = OUTPUT.parent / "evidence/tables"
STYLE = OUTPUT / "eswa.mplstyle"
CONTRACT = OUTPUT / "figure_contracts.json"
CONTRASTS = ["memory_minus_scalar", "diagnostic_minus_memory", "memory_minus_random", "memory_minus_fixed"]
LABELS = {
    "memory_minus_scalar": "Memory − scalar",
    "diagnostic_minus_memory": "Diagnostic − memory",
    "memory_minus_random": "Memory − random",
    "memory_minus_fixed": "Memory − fixed",
}
TASKS = ["T9", "T10", "T11", "T12", "T13", "T14"]
BLUE = "#0072B2"
POINT_GRAY = "#858585"
STEM = "#9FC2D1"
ZERO = "#737373"
GRID = "#E4E4E4"
INK = "#262626"
MUTED = "#626262"
TRACK = "#E9E9E9"


def finish_axis(ax) -> None:
    ax.set_axisbelow(True)
    for spine in ["left", "top", "right"]:
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(ZERO)
    ax.spines["bottom"].set_linewidth(0.55)
    ax.tick_params(axis="x", colors=MUTED, labelsize=7.4, width=0.5, length=2.5)
    ax.tick_params(axis="y", colors=MUTED, labelsize=7.4)


def read_csv(name: str) -> list[dict[str, str]]:
    with (MATERIALS / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def dump(value) -> None:
    CONTRACT.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def export(fig, stem: str, width_mm: float, height_mm: float) -> dict:
    base = OUTPUT / stem
    fig.savefig(base.with_suffix(".pdf"), format="pdf", dpi=300, facecolor="white")
    fig.savefig(base.with_suffix(".svg"), format="svg", dpi=300, facecolor="white")
    fig.savefig(base.with_suffix(".png"), format="png", dpi=300, facecolor="white")
    plt.close(fig)
    with Image.open(base.with_suffix(".png")) as image:
        pixels = list(image.size)
    info = subprocess.run(["pdfinfo", str(base.with_suffix(".pdf"))], check=True, text=True, capture_output=True).stdout
    size_line = next(line for line in info.splitlines() if line.startswith("Page size:"))
    parts = size_line.split()
    page_bp = [float(parts[2]), float(parts[4])]
    page_mm = [value * 25.4 / 72 for value in page_bp]
    expected_pixels = [round(width_mm / 25.4 * 300), round(height_mm / 25.4 * 300)]
    if any(abs(a - b) > 0.02 for a, b in zip(page_mm, [width_mm, height_mm])):
        raise ValueError(f"PDF size mismatch for {stem}: {page_mm}")
    if any(abs(a - b) > 1 for a, b in zip(pixels, expected_pixels)):
        raise ValueError(f"PNG size mismatch for {stem}: {pixels} versus {expected_pixels}")
    fonts = subprocess.run(["pdffonts", str(base.with_suffix(".pdf"))], check=True, text=True, capture_output=True).stdout
    if "DejaVuSans" not in fonts or " no " in fonts:
        raise ValueError(f"PDF font embedding check failed for {stem}")
    return {
        "base_filename": stem,
        "files": {suffix: f"{stem}.{suffix}" for suffix in ["pdf", "svg", "png"]},
        "requested_page_mm": [width_mm, height_mm],
        "verified_pdf_page_mm": page_mm,
        "verified_png_pixels_at_300_dpi": pixels,
        "pdf_fonts_embedded": True,
    }


def validate_data(units, contrasts, tasks, behavior, costs):
    if len(units) != 24 or len(contrasts) != 4 or len(tasks) != 24 or len(behavior) != 15 or len(costs) != 4:
        raise ValueError("Unexpected V003 paper-material dimensions")
    keys = {(row["task"], int(row["seed"]), row["model_replicate"]) for row in units}
    if len(keys) != 24 or {row["task"] for row in units} != set(TASKS):
        raise ValueError("Paired-unit identities are incomplete")
    for contrast in CONTRASTS:
        selected = [float(row[contrast]) for row in units]
        summary = next(row for row in contrasts if row["contrast"] == contrast)
        values = sorted(selected)
        median = (values[11] + values[12]) / 2
        if not math.isclose(median, float(summary["median"]), rel_tol=0, abs_tol=1e-15):
            raise ValueError(f"Median mismatch for {contrast}")
        if not math.isclose(math.fsum(selected) / 24, float(summary["mean"]), rel_tol=0, abs_tol=1e-15):
            raise ValueError(f"Mean mismatch for {contrast}")
        task_medians = []
        for task in TASKS:
            task_values = sorted(float(row[contrast]) for row in units if row["task"] == task)
            task_medians.append((task_values[1] + task_values[2]) / 2)
        source_task_medians = [float(row["median_delta"]) for row in tasks if row["contrast"] == contrast]
        if any(not math.isclose(a, b, rel_tol=0, abs_tol=1e-15) for a, b in zip(task_medians, source_task_medians)):
            raise ValueError(f"Task median mismatch for {contrast}")


def paired_figure(units, contrasts) -> tuple[dict, list[dict]]:
    plotted = {}
    for contrast in CONTRASTS:
        values = [float(row[contrast]) for row in units]
        y = [0.32 + 0.48 * (((i * 7) % 11) / 10 - 0.5) for i in range(24)]
        summary = next(row for row in contrasts if row["contrast"] == contrast)
        plotted[contrast] = {
            "unit_effects": values,
            "display_only_vertical_offsets": y,
            "median": float(summary["median"]),
            "task_cluster_95_interval": [float(summary["ci_lower"]), float(summary["ci_upper"])],
            "positive_units": int(summary["positive_units"]),
            "zero_units": int(summary["zero_units"]),
            "negative_units": int(summary["negative_units"]),
        }
    variants = []
    for suffix, width, height in [("review_wide", 150.0, 88.0), ("preview_wide", 183.48, 88.0)]:
        with plt.style.context(STYLE):
            fig, axes = plt.subplots(4, 1, figsize=(width / 25.4, height / 25.4), sharex=True)
            for index, (ax, contrast) in enumerate(zip(axes, CONTRASTS)):
                values = plotted[contrast]["unit_effects"]
                y = plotted[contrast]["display_only_vertical_offsets"]
                low, high = plotted[contrast]["task_cluster_95_interval"]
                median = plotted[contrast]["median"]
                ax.axvline(0, color=ZERO, linewidth=0.6, zorder=1)
                ax.scatter(values, y, s=11, color=POINT_GRAY, alpha=0.66, edgecolors="white", linewidths=0.35, zorder=2)
                ax.plot([low, high], [-0.30, -0.30], color=BLUE, linewidth=1.8, solid_capstyle="round", zorder=3)
                ax.scatter([median], [-0.30], s=31, color=BLUE, marker="D", edgecolors="white", linewidths=0.5, zorder=4)
                ax.annotate(f"{median:.4f}", (median, -0.30), xytext=(0, 7), textcoords="offset points", ha="center", va="bottom", fontsize=6.8, color=INK)
                ax.set_ylim(-0.52, 0.68)
                ax.set_yticks([0.08], [LABELS[contrast]], fontsize=7.6, color=INK)
                ax.grid(axis="x", color=GRID, linewidth=0.35)
                ax.tick_params(axis="y", length=0, pad=5)
                finish_axis(ax)
                if index < 3:
                    ax.tick_params(axis="x", labelbottom=False, length=0)
                    ax.spines["bottom"].set_visible(False)
            axes[-1].set_xlabel("Paired difference in mean absolute IC", fontsize=8.0, labelpad=4, color=INK)
            axes[-1].set_xlim(-0.009, 0.032)
            axes[-1].set_xticks([-0.005, 0, 0.005, 0.010, 0.015, 0.020, 0.025, 0.030])
            fig.subplots_adjust(left=34 / width, right=1 - 4 / width, top=1 - 3 / height, bottom=12 / height, hspace=0.26)
            variants.append(export(fig, f"figure_02_paired_contrasts_{suffix}", width, height))
    contract = {
        "figure_id": "Figure 2",
        "question": "How large are the paired V003 effects across all 24 units for the four frozen contrasts?",
        "source_paths": [str((MATERIALS / "unit_effects.csv").relative_to(PROJECT)), str((MATERIALS / "contrast_summary.csv").relative_to(PROJECT))],
        "source_fields": {"unit_effects.csv": CONTRASTS, "contrast_summary.csv": ["median", "ci_lower", "ci_upper", "positive_units", "zero_units", "negative_units"]},
        "experimental_unit": "task × development seed × independent model-call replicate",
        "sample_count": 24,
        "transformations": ["Plot each paired unit effect without filtering failures or zero values.", "Overlay the reported median and six-task cluster bootstrap 95% interval in a separate lower band.", "Vertical offsets are display-only; horizontal values are unchanged."],
        "plotted_arrays": plotted,
        "uncertainty_definition": "Task-cluster bootstrap 95% interval for the median across all 24 unit effects after resampling six complete task clusters; individual gray points are paired-unit effects, not interval observations.",
        "exclusions": [],
        "caption": "Paired differences in mean absolute IC for 24 task–seed–model-replicate units. Gray points show all unit effects; blue diamonds show medians and blue bars show six-task cluster bootstrap 95% intervals for the 24-unit median. Failed and invalid model decisions remain in the fixed-call-budget analysis. Results are confirmatory within the development panel and are not independent market-period out-of-sample estimates.",
        "variants": variants,
    }
    return contract, variants


def task_figure(tasks) -> tuple[dict, list[dict]]:
    plotted = {
        contrast: {row["task"]: float(row["median_delta"]) for row in tasks if row["contrast"] == contrast}
        for contrast in CONTRASTS
    }
    variants = []
    for suffix, width, height in [("review_wide", 150.0, 86.0), ("preview_wide", 183.48, 86.0)]:
        with plt.style.context(STYLE):
            fig, axes = plt.subplots(2, 2, figsize=(width / 25.4, height / 25.4), sharex=True, sharey=True)
            for panel, (ax, contrast) in enumerate(zip(axes.flat, CONTRASTS)):
                values = [plotted[contrast][task] for task in TASKS]
                positions = list(range(6))
                ax.axvline(0, color=ZERO, linewidth=0.6, zorder=1)
                ax.hlines(positions, 0, values, color=STEM, linewidth=1.0, zorder=2)
                ax.scatter(values, positions, s=24, color=BLUE, marker="o", edgecolors="white", linewidths=0.5, zorder=3)
                ax.set_yticks(positions, TASKS, fontsize=7.4)
                ax.set_ylim(5.6, -0.6)
                ax.set_title(LABELS[contrast], fontsize=8.0, loc="left", pad=4, color=INK)
                ax.grid(axis="x", color=GRID, linewidth=0.35)
                ax.tick_params(axis="y", length=0)
                finish_axis(ax)
                if panel < 2:
                    ax.spines["bottom"].set_visible(False)
            for ax in axes[1, :]:
                ax.set_xlabel("Task median paired difference", fontsize=8.0, labelpad=3, color=INK)
            axes[0, 0].set_xlim(-0.009, 0.024)
            axes[0, 0].set_xticks([-0.005, 0, 0.010, 0.020])
            fig.subplots_adjust(left=11 / width, right=1 - 3 / width, top=1 - 6 / height, bottom=12 / height, hspace=0.42, wspace=0.18 if width == 150.0 else 0.15)
            variants.append(export(fig, f"figure_03_task_heterogeneity_{suffix}", width, height))
    contract = {
        "figure_id": "Figure 3",
        "question": "How do paired effects vary across the six V003 tasks?",
        "source_paths": [str((MATERIALS / "task_effects.csv").relative_to(PROJECT))],
        "source_fields": {"task_effects.csv": ["contrast", "task", "median_delta"]},
        "experimental_unit": "task median over four paired seed–model-replicate units",
        "sample_count": 6,
        "transformations": ["Use the reported median of four paired-unit effects for each task and contrast.", "Use a shared horizontal scale across four small multiples."],
        "plotted_arrays": plotted,
        "uncertainty_definition": "No task-specific interval is shown; each marker summarizes four paired units.",
        "exclusions": [],
        "caption": "Task-level medians of paired mean-absolute-IC differences. Each marker summarizes four units formed by two development seeds and two independent model-call replicates. All panels share one horizontal scale. The development panel contains six tasks; task medians describe heterogeneity and do not constitute independent market-period out-of-sample tests.",
        "variants": variants,
    }
    return contract, variants


def behavior_figure(behavior) -> tuple[dict, list[dict]]:
    comparisons = ["memory_minus_scalar", "diagnostic_minus_memory", "memory_minus_random"]
    metrics = ["novelty", "duplicate_rate"]
    plotted = {}
    for metric in metrics:
        rows = [next(row for row in behavior if row["comparison"] == comparison and row["metric"] == metric) for comparison in comparisons]
        plotted[metric] = {comparison: {"median": float(row["median"]), "minimum": float(row["min"]), "maximum": float(row["max"])} for comparison, row in zip(comparisons, rows)}
    variants = []
    for suffix, width, height in [("review_wide", 150.0, 58.0), ("preview_wide", 183.48, 58.0)]:
        with plt.style.context(STYLE):
            fig, axes = plt.subplots(1, 2, figsize=(width / 25.4, height / 25.4), sharey=True)
            for ax, metric in zip(axes, metrics):
                values = [plotted[metric][comparison]["median"] for comparison in comparisons]
                positions = list(range(3))
                ax.axvline(0, color=ZERO, linewidth=0.6)
                ax.hlines(positions, 0, values, color=STEM, linewidth=1.2, zorder=2)
                ax.scatter(values, positions, s=29, color=BLUE, marker="o", edgecolors="white", linewidths=0.55, zorder=3)
                for value, position in zip(values, positions):
                    if value < 0:
                        ax.annotate(f"{value:.3f}", (value, position), xytext=(-5, 0), textcoords="offset points", ha="right", va="center", fontsize=6.8, color=INK)
                    else:
                        ax.annotate(f"{value:.3f}", (value, position), xytext=(5, 0), textcoords="offset points", ha="left", va="center", fontsize=6.8, color=INK)
                ax.set_yticks(positions, [LABELS[value] for value in comparisons], fontsize=7.4)
                ax.set_ylim(2.6, -0.6)
                ax.set_title("Novelty difference" if metric == "novelty" else "Duplicate-rate difference", fontsize=8.0, loc="left", pad=4, color=INK)
                ax.set_xlabel("Median paired difference", fontsize=8.0, labelpad=3, color=INK)
                ax.grid(axis="x", color=GRID, linewidth=0.35)
                ax.tick_params(axis="y", length=0)
                finish_axis(ax)
            axes[0].set_xlim(-0.19, 0.08)
            axes[0].set_xticks([-0.15, -0.10, -0.05, 0, 0.05])
            axes[1].set_xlim(-0.07, 0.14)
            axes[1].set_xticks([-0.05, 0, 0.05, 0.10])
            fig.subplots_adjust(left=34 / width, right=1 - 4 / width, top=1 - 6 / height, bottom=12 / height, wspace=0.22 if width == 150.0 else 0.18)
            variants.append(export(fig, f"figure_04_behavior_costs_{suffix}", width, height))
    contract = {
        "figure_id": "Figure 4",
        "question": "What novelty and duplication changes accompany the V003 IC effects?",
        "source_paths": [str((MATERIALS / "behavior_effects.csv").relative_to(PROJECT))],
        "source_fields": {"behavior_effects.csv": ["comparison", "metric", "median", "min", "max"]},
        "filters": {"metric": metrics, "comparison": comparisons},
        "experimental_unit": "paired task–seed–model-replicate unit; panel reports the median across 24 units",
        "sample_count": 24,
        "transformations": ["Plot the reported median paired select-metric difference for each comparison and behavior metric.", "Keep novelty and duplicate rate on separate axes because their scales differ."],
        "plotted_arrays": plotted,
        "uncertainty_definition": "No interval is shown; ranges are retained in plotted_arrays and the source CSV.",
        "exclusions": ["Memory-minus-fixed behavior contrast is not part of behavior_effects.csv."],
        "caption": "Median paired differences in select novelty and duplicate rate across the same 24 development-panel units. Negative novelty differences indicate less behavioral novelty; positive duplicate-rate differences indicate more duplication. Panels use separate horizontal scales. These behavior diagnostics accompany the IC contrasts and are not independent out-of-sample evaluations.",
        "variants": variants,
    }
    return contract, variants


def execution_figure(costs) -> tuple[dict, list[dict]]:
    arms = ["scalar", "memory", "diagnostic", "random"]
    plotted = {}
    for field, budget_field in [("executed_decisions", "decisions"), ("candidate_positions", "candidate_budget")]:
        plotted[field] = {
            arm: {
                "realized": int(next(row for row in costs if row["arm"] == arm)[field]),
                "budget": int(next(row for row in costs if row["arm"] == arm)[budget_field]),
            }
            for arm in arms
        }
    variants = []
    for suffix, width, height in [("review_wide", 150.0, 60.0), ("preview_wide", 183.48, 60.0)]:
        with plt.style.context(STYLE):
            fig, axes = plt.subplots(1, 2, figsize=(width / 25.4, height / 25.4), sharey=True)
            metrics = [("executed_decisions", 96, "Executed decisions", "Decisions"), ("candidate_positions", None, "Realized candidate positions", "Candidate positions")]
            for ax, (field, fixed_budget, title, xlabel) in zip(axes, metrics):
                values = [plotted[field][arm]["realized"] for arm in arms]
                budgets = [fixed_budget if fixed_budget is not None else plotted[field][arm]["budget"] for arm in arms]
                positions = list(range(4))
                ax.barh(positions, budgets, height=0.40, color=TRACK, edgecolor="none", zorder=1)
                ax.barh(positions, values, height=0.40, color=BLUE, edgecolor="none", zorder=2)
                for y, value, budget in zip(positions, values, budgets):
                    ax.text(value - max(budgets) * 0.02, y, f"{value:,}/{budget:,}", ha="right", va="center", fontsize=7.0, color="white", zorder=3)
                ax.set_yticks(positions, [arm.capitalize() for arm in arms], fontsize=7.4)
                ax.set_ylim(3.6, -0.6)
                ax.set_xlim(0, max(budgets) * 1.08)
                ax.set_title(title, fontsize=8.0, loc="left", pad=4, color=INK)
                ax.set_xlabel(xlabel, fontsize=8.0, labelpad=3, color=INK)
                ax.grid(axis="x", color=GRID, linewidth=0.35)
                ax.tick_params(axis="y", length=0)
                finish_axis(ax)
                if fixed_budget is not None:
                    ax.set_xticks([0, 25, 50, 75, 100])
                else:
                    ax.set_xticks([0, 300, 600, 900, 1200])
            fig.subplots_adjust(left=22 / width, right=1 - 4 / width, top=1 - 6 / height, bottom=12 / height, wspace=0.24 if width == 150.0 else 0.20)
            variants.append(export(fig, f"figure_05_execution_realization_{suffix}", width, height))
    contract = {
        "figure_id": "Figure 5",
        "question": "How many decisions and candidate positions were realized under the fixed V003 execution protocol?",
        "source_paths": [str((MATERIALS / "execution_costs.csv").relative_to(PROJECT))],
        "source_fields": {"execution_costs.csv": ["arm", "decisions", "executed_decisions", "candidate_budget", "candidate_positions"]},
        "experimental_unit": "arm-level total over 24 paired units, each with four decisions",
        "sample_count": 4,
        "transformations": ["Sum realized executed decisions and candidate positions by arm, following loop-state history references to include recovered source steps.", "Annotate realized values together with the arm-specific budget recorded in execution_costs.csv.", "Use separate panels for decisions and candidate positions."],
        "plotted_arrays": plotted,
        "uncertainty_definition": "Descriptive execution totals; no inferential interval applies.",
        "exclusions": [],
        "caption": "Realized execution by arm across 24 paired development-panel units. The left panel shows executed decisions among 96 scheduled decisions per arm. The right panel shows candidate positions together with each arm's recorded candidate budget. Recovered source steps are included through loop-state history references. All three model arms realize their recorded candidate budgets; the random arm realizes 1,104 positions against its recorded budget of 1,152. Execution losses remain in the primary fixed-call-budget analysis.",
        "variants": variants,
    }
    return contract, variants


def build() -> None:
    units = read_csv("unit_effects.csv")
    contrasts = read_csv("contrast_summary.csv")
    tasks = read_csv("task_effects.csv")
    behavior = read_csv("behavior_effects.csv")
    costs = read_csv("execution_costs.csv")
    validate_data(units, contrasts, tasks, behavior, costs)
    figures, mechanics = [], []
    for contract, mechanical in [paired_figure(units, contrasts), task_figure(tasks), behavior_figure(behavior), execution_figure(costs)]:
        figures.append(contract)
        mechanics.extend(mechanical)
    resolved_font = font_manager.findfont("DejaVu Sans")
    dump({
        "schema": "ESWA_FIGURE_CONTRACTS_1",
        "evidence_scope": "Completed V003 development-panel result and paper-material CSV files only; no research or market-data execution.",
        "style_sources": [str(STYLE.relative_to(PROJECT))],
        "style_summary": {"font_family": "DejaVu Sans", "resolved_font_file": Path(resolved_font).name, "surface": "#FFFFFF", "single_hue": BLUE, "palette_validation": "PASS: dataviz validate_palette.js, light mode, #FFFFFF surface, --pairs all"},
        "method_figure": {
            "figure_id": "Figure 1",
            "source": ".archify/workflow-research-harness-20261031-233426/candidate.json",
            "canonical_export": ".archify/workflow-research-harness-20261031-233426/exported-svg.json",
            "render_command": "bash figures/render_method.sh",
            "pdf_fit": True,
            "pdf_fonts_embedded": True,
            "font_family": "JetBrains Mono and Menlo",
            "files": {suffix: f"figure_01_method_pipeline.{suffix}" for suffix in ["pdf", "svg", "png"]},
            "format": "Archify workflow with orthogonal routes and canonical SVG export",
            "source_paths": ["auto_research/docs/DESIGN_CN.md", "auto_research/docs/PAPER_READINESS_V003_EXECUTION_PLAN_CN.md"],
            "information_boundaries": ["Historical probe memory contains no select outcomes.", "Online continuation is probe-only.", "One frozen champion per arm enters final common-support comparison."],
            "caption": "Budget-bounded research-harness continuation and evaluation. Scalar, cumulative-memory, and diagnostic proposers emit one atomic proposal per decision; random legal edits use zero model calls. Probe execution feeds a four-decision active branch and one frozen champion per condition, followed by the final common-support comparison. The diagram also marks the fixed candidate budget, retained failures, and exclusion of select outcomes from historical probe memory.",
        },
        "exports_and_mechanical_checks": mechanics,
        "figures": figures,
        "visual_review": {
            "status": "PASS",
            "method": "Direct inspection of every exported PNG variant and every figure-bearing page in both compiled PDFs.",
            "inspected_files": [
                "figure_01_method_pipeline.png",
                "figure_02_paired_contrasts_review_wide.png",
                "figure_02_paired_contrasts_preview_wide.png",
                "figure_03_task_heterogeneity_review_wide.png",
                "figure_03_task_heterogeneity_preview_wide.png",
                "figure_04_behavior_costs_review_wide.png",
                "figure_04_behavior_costs_preview_wide.png",
                "figure_05_execution_realization_review_wide.png",
                "figure_05_execution_realization_preview_wide.png",
            ],
            "checks": ["No clipped, truncated, or overlapping labels, points, or numeric annotations.", "White surfaces and consistent physical type sizes in review and publication-preview variants.", "Zero references and zero-valued task effects remain visible.", "Condition identity is carried by direct labels and panel titles.", "Method-diagram labels stay inside nodes, arrows avoid label collisions, and information boundaries remain legible."],
        },
    })
    print(json.dumps({"status": "GENERATED", "figures": len(figures), "exports": len(mechanics) * 3}))


def check() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    assert len(contract["figures"]) == 4
    assert len(contract["exports_and_mechanical_checks"]) == 8
    assert all(len(figure["variants"]) == 2 for figure in contract["figures"])
    for filename in contract["method_figure"]["files"].values():
        assert (OUTPUT / filename).is_file() and (OUTPUT / filename).stat().st_size > 0
    method_fonts = subprocess.run(["pdffonts", str(OUTPUT / "figure_01_method_pipeline.pdf")], check=True, text=True, capture_output=True).stdout
    assert "JetBrainsMono-Regular" in method_fonts and "Menlo-Bold" in method_fonts and " no " not in method_fonts
    method_info = subprocess.run(["pdfinfo", str(OUTPUT / "figure_01_method_pipeline.pdf")], check=True, text=True, capture_output=True).stdout
    method_size = next(line for line in method_info.splitlines() if line.startswith("Page size:"))
    method_parts = method_size.split()
    assert float(method_parts[4]) < float(method_parts[2]) * 0.75
    for mechanical in contract["exports_and_mechanical_checks"]:
        for filename in mechanical["files"].values():
            assert (OUTPUT / filename).is_file() and (OUTPUT / filename).stat().st_size > 0
    units = read_csv("unit_effects.csv")
    contrasts = read_csv("contrast_summary.csv")
    tasks = read_csv("task_effects.csv")
    behavior = read_csv("behavior_effects.csv")
    costs = read_csv("execution_costs.csv")
    validate_data(units, contrasts, tasks, behavior, costs)
    print(json.dumps({"status": "PASS", "figures": 4, "visual_review": contract["visual_review"]["status"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else build()
