"""Build and verify the source-bound V003 manuscript preparation workspace."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


RUN = Path(__file__).resolve().parents[1]
PROJECT = RUN.parents[2]
MATERIALS = RUN / "evidence/tables"
RESULT = RUN / "evidence/source_result.json"
PROTOCOL = PROJECT / "auto_research/config/paper_readiness_v003/protocol.json"
V001 = RUN / "evidence/source_result_v001.json"
V002 = RUN / "evidence/source_result_v002.json"
CONTRASTS = ["memory_minus_scalar", "diagnostic_minus_memory", "memory_minus_random", "memory_minus_fixed"]


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_csv(name: str) -> list[dict]:
    with (MATERIALS / name).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def build() -> None:
    result = load_json(RESULT)
    protocol = load_json(PROTOCOL)
    v001 = load_json(V001)
    v002 = load_json(V002)
    csv_names = [
        "unit_effects.csv", "contrast_summary.csv", "task_effects.csv", "execution_costs.csv",
        "behavior_effects.csv", "auxiliary_execution_analysis.csv", "replicate_stability.csv",
        "date_block_summary.csv",
    ]
    tables = {name.removesuffix(".csv"): load_csv(name) for name in csv_names}
    assert result["paired_units"] and len(result["paired_units"]) == 24
    assert result["all_protocol_checks_passed"] is True
    assert result["evidence_status"] == "development-panel complete"
    assert result["score_window_read"] is False and result["independent_real_data_oos"] is False
    assert protocol["primary_estimands"] == CONTRASTS
    assert len(tables["unit_effects"]) == 24 and len(tables["task_effects"]) == 24
    assert len(tables["contrast_summary"]) == 4 and len(tables["execution_costs"]) == 4
    execution_rows = tables["execution_costs"]
    assert sum(int(row["decisions"]) - int(row["executed_decisions"]) for row in execution_rows) == result["failed_or_invalid_decisions"]
    usage = {
        **result["usage"],
        "model_candidate_positions": sum(int(row["candidate_positions"]) for row in execution_rows if row["arm"] != "random"),
        "random_candidate_positions": sum(int(row["candidate_positions"]) for row in execution_rows if row["arm"] == "random"),
        "executed_decisions": sum(int(row["executed_decisions"]) for row in execution_rows),
    }
    recoveries = []
    for recovery in result["execution_recoveries"]:
        arm = recovery["arm"]
        identity = (recovery["task"], recovery["seed"], recovery["model_replicate"])
        unit_result = next(
            unit for unit in result["paired_units"]
            if (unit["task"], unit["seed"], unit["model_replicate"]) == identity
        )
        unit_row = next(
            row for row in tables["unit_effects"]
            if (row["task"], int(row["seed"]), row["model_replicate"]) == identity
        )
        final_run = Path(unit_result[f"{arm}_run"])
        state = load_json(
            RUN / "evidence/recoveries"
            / f"{recovery['task']}_S{recovery['seed']}_R{recovery['model_replicate']}_{arm}_loop_state.json"
        )
        recoveries.append({
            **recovery,
            "final_run": str(final_run),
            "executed_decisions": int(unit_row[f"{arm}_executed_decisions"]),
            "candidate_positions": int(unit_row[f"{arm}_candidate_positions"]),
            "failed_decision_rationales": [
                entry.get("rationale") for entry in state.get("history", []) if entry.get("status") == "FAILED"
            ],
            "champion_decision": Path(unit_result[f"{arm}_champion"]).parents[3].name,
        })

    sources = [
        {"id": "V003-RESULT", "path": str(RESULT.relative_to(PROJECT)), "kind": "json", "role": "complete confirmatory development-panel result"},
        {"id": "V003-PROTOCOL", "path": str(PROTOCOL.relative_to(PROJECT)), "kind": "json", "role": "frozen tasks, budgets, model, and analysis"},
        {"id": "V001-RESULT", "path": str(V001.relative_to(PROJECT)), "kind": "json", "role": "exploratory diagnostic continuation panel"},
        {"id": "V002-RESULT", "path": str(V002.relative_to(PROJECT)), "kind": "json", "role": "exploratory cumulative-memory component panel"},
        {"id": "DESIGN", "path": "auto_research/docs/DESIGN_CN.md", "kind": "markdown", "role": "system architecture and information boundaries"},
        {"id": "EXECUTION-PLAN", "path": "auto_research/docs/PAPER_READINESS_V003_EXECUTION_PLAN_CN.md", "kind": "markdown", "role": "execution record and evidence-role summary"},
        {"id": "LITERATURE-LEADS", "path": "auto_research/docs/SURVEY_CN.md", "kind": "markdown", "role": "historical literature lead inventory; bibliographic claims require source verification"},
    ]
    for name in csv_names:
        sources.append({
            "id": "V003-" + name.removesuffix(".csv").replace("_", "-").upper(),
            "path": str((MATERIALS / name).relative_to(PROJECT)), "kind": "csv", "role": "paper-ready derived table",
        })
    dump(RUN / "evidence/sources.json", {"schema": "AUTO-RESEARCH-WRITING-SOURCES-1", "sources": sources})

    packet = {
        "schema": "AUTO-RESEARCH-RESULTS-PACKET-1",
        "evidence_status": result["evidence_status"],
        "model": result["model"],
        "task_count": result["task_count"],
        "development_seeds": result["seeds"],
        "model_replicates": result["model_replicates"],
        "paired_units": len(result["paired_units"]),
        "protocol": protocol,
        "contrasts": result["contrasts"],
        "usage": usage,
        "failed_or_invalid_decisions": result["failed_or_invalid_decisions"],
        "execution_recoveries": recoveries,
        "score_window_read": result["score_window_read"],
        "independent_real_data_oos": result["independent_real_data_oos"],
        "exploratory_panels": {
            "V001": {"median_paired_delta": v001["median_paired_delta"], "evidence_status": v001["evidence_status"], "usage": v001["usage"]},
            "V002": {"median_paired_delta": v002["median_paired_delta"], "evidence_status": v002["evidence_status"], "usage": v002["usage"]},
        },
        "tables": tables,
        "interpretation": {
            "primary_analysis": "fixed-call-budget paired end-to-end analysis; failed and invalid model decisions retained",
            "experimental_unit": "task by development seed by independent model-call replicate",
            "inference_cluster": "six tasks",
            "endpoint": "mean_abs_ic: mean across candidates of absolute per-candidate mean daily cross-sectional Spearman IC",
            "complete_execution": "auxiliary complete-case sensitivity analysis only",
            "date_block_metric": "mean_daily_abs_ic_delta used only for date-block diagnostics",
            "market_scope": "2017-01-01 through 2020-11-30; no independent real-data out-of-sample evaluation",
        },
    }
    dump(RUN / "evidence/results_packet.json", packet)

    claim_specs = [
        ("C01", "panel-completeness", "V003 contains 24 complete paired units and all protocol checks passed.", "V003-RESULT", ["/paired_units", "/all_protocol_checks_passed"]),
        ("C02", "panel-design", "V003 uses six tasks, development seeds 11 and 29, and model-call replicates A and B.", "V003-PROTOCOL", ["/task_files", "/seeds", "/model_replicates"]),
        ("C03", "model-identity", "The proposer model is kimi/k3.1-pro[1m].", "V003-PROTOCOL", ["/model_id", "/generation/cli_model_override"]),
        ("C04", "atomic-contract", "One atomic proposal contains two to five edits and produces one final harness candidate.", "EXECUTION-PLAN", [{"kind": "heading", "text": "## 5. 原子提案契约"}]),
        ("C05", "memory-scalar", "Memory minus scalar has median +0.0002895443706825082 and Holm-adjusted p=1.0.", "V003-RESULT", ["/contrasts/memory_minus_scalar"]),
        ("C06", "diagnostic-memory", "Diagnostic minus memory has median 0.0 and Holm-adjusted p=1.0.", "V003-RESULT", ["/contrasts/diagnostic_minus_memory"]),
        ("C07", "memory-random", "Memory minus random has median +0.0035336383723919666, six positive task medians, and exact task sign-flip p=0.03125.", "V003-RESULT", ["/contrasts/memory_minus_random"]),
        ("C08", "memory-fixed", "Memory minus fixed has median +0.010976872198680074, 24 positive units, and exact task sign-flip p=0.03125.", "V003-RESULT", ["/contrasts/memory_minus_fixed"]),
        ("C09", "memory-random-interval", "The memory-minus-random task-cluster 95% interval is +0.001623060462619938 to +0.005118746302635594.", "V003-RESULT", ["/contrasts/memory_minus_random/task_bootstrap_95pct_interval/0", "/contrasts/memory_minus_random/task_bootstrap_95pct_interval/1"]),
        ("C10", "memory-fixed-interval", "The memory-minus-fixed task-cluster 95% interval is +0.008079793378808216 to +0.015455539112667562.", "V003-RESULT", ["/contrasts/memory_minus_fixed/task_bootstrap_95pct_interval/0", "/contrasts/memory_minus_fixed/task_bootstrap_95pct_interval/1"]),
        ("C11", "memory-scalar-interval", "The memory-minus-scalar task-cluster 95% interval includes zero.", "V003-RESULT", ["/contrasts/memory_minus_scalar/task_bootstrap_95pct_interval/0", "/contrasts/memory_minus_scalar/task_bootstrap_95pct_interval/1"]),
        ("C12", "novelty-scalar", "Memory minus scalar has novelty median -0.125.", "V003-BEHAVIOR-EFFECTS", [{"comparison": "memory_minus_scalar", "metric": "novelty", "field": "median"}]),
        ("C13", "duplicates-scalar", "Memory minus scalar has duplicate-rate median +0.045454545454545456.", "V003-BEHAVIOR-EFFECTS", [{"comparison": "memory_minus_scalar", "metric": "duplicate_rate", "field": "median"}]),
        ("C14", "novelty-random", "Memory minus random has novelty median -0.09166666666666667.", "V003-BEHAVIOR-EFFECTS", [{"comparison": "memory_minus_random", "metric": "novelty", "field": "median"}]),
        ("C15", "duplicates-random", "Memory minus random has duplicate-rate median +0.03787878787878787.", "V003-BEHAVIOR-EFFECTS", [{"comparison": "memory_minus_random", "metric": "duplicate_rate", "field": "median"}]),
        ("C16", "model-calls", "The panel records 288 model calls and zero model calls for the random arm.", "V003-EXECUTION-COSTS", [{"field": "model_calls", "all_rows": True}]),
        ("C17", "tokens", "The panel uses 7,550,828 input tokens and 1,200,323 output tokens across model arms.", "V003-EXECUTION-COSTS", [{"fields": ["input_tokens", "output_tokens"], "rows": ["scalar", "memory", "diagnostic"]}]),
        ("C18", "failures", "The panel retains 37 failed or invalid decision records.", "V003-RESULT", ["/failed_or_invalid_decisions"]),
        ("C19", "recovery", "One no-usage timeout recovery is recorded without an additional valid research decision.", "V003-RESULT", ["/execution_recoveries"]),
        ("C20", "complete-case", "Complete-execution analyses are auxiliary and do not replace the fixed-call-budget primary analysis.", "EXECUTION-PLAN", [{"kind": "heading", "text": "### 9.4 执行敏感性与成本"}]),
        ("C21", "replicate-definition", "Model replicates are independent calls; provider-controlled model seeds are not claimed.", "EXECUTION-PLAN", [{"kind": "heading", "text": "## 4. 冻结设计"}]),
        ("C22", "data-boundary", "The study does not read the score window and provides no independent real-data out-of-sample evaluation.", "V003-RESULT", ["/score_window_read", "/independent_real_data_oos"]),
        ("C23", "v001-role", "V001 is an exploratory development-panel null for the diagnostic component.", "V001-RESULT", ["/median_paired_delta", "/evidence_status"]),
        ("C24", "v002-role", "V002 is an exploratory development-panel null for the cumulative-memory component.", "V002-RESULT", ["/median_paired_delta", "/evidence_status"]),
        ("C25", "inference", "The protocol uses 10,000 task-cluster bootstrap samples, exact task sign-flip tests, and Holm correction for component contrasts.", "V003-PROTOCOL", ["/analysis"]),
        ("C26", "budgets", "Each arm has four continuation decisions with twelve candidate positions per decision.", "V003-PROTOCOL", ["/continuation_decisions", "/candidate_budget"]),
        ("C27", "random-control", "Random legal atomic edits use the same editable surface and make zero model calls.", "EXECUTION-PLAN", [{"kind": "heading", "text": "## 5. 原子提案契约"}]),
        ("C28", "fresh-task-scope", "Fresh tasks within the development window do not constitute independent market-period out-of-sample evidence.", "EXECUTION-PLAN", [{"kind": "heading", "text": "## 3. 证据角色"}]),
    ]
    claims = [{"claim_id": cid, "object": obj, "statement": text, "source_id": sid, "locators": loc} for cid, obj, text, sid, loc in claim_specs]
    dump(RUN / "claim_map.json", {"schema": "AUTO-RESEARCH-CLAIM-MAP-1", "claims": claims, "literature_policy": "Only independently verified bibliographic sources may support related-work claims."})

    units = [
        ("00_title", "Title", ["C01", "C22"], ["V003-RESULT"]),
        ("01_abstract", "Abstract", ["C01", "C05", "C06", "C07", "C08", "C22"], ["V003-RESULT", "V003-PROTOCOL"]),
        ("02_introduction", "Introduction and research question", ["C01", "C04"], ["V003-RESULT", "DESIGN", "EXECUTION-PLAN"]),
        ("03_related", "Related work", [], ["LITERATURE-LEADS"]),
        ("04_problem", "Problem formulation and evidence scope", ["C22", "C28"], ["V003-RESULT", "DESIGN", "EXECUTION-PLAN"]),
        ("05_harness", "Research harness and atomic proposals", ["C04", "C27"], ["DESIGN", "EXECUTION-PLAN"]),
        ("06_information", "Scalar, cumulative-memory, and diagnostic information", ["C23", "C24"], ["V001-RESULT", "V002-RESULT", "DESIGN"]),
        ("07_protocol", "Paired V003 protocol and controls", ["C02", "C03", "C26", "C27"], ["V003-PROTOCOL", "EXECUTION-PLAN"]),
        ("08_metrics", "Outcomes and execution metrics", ["C01", "C22"], ["V003-RESULT", "V003-UNIT-EFFECTS"]),
        ("09_analysis", "Statistical analysis", ["C21", "C25"], ["V003-PROTOCOL", "EXECUTION-PLAN"]),
        ("10_primary", "Model-driven process versus random and fixed references", ["C07", "C08", "C09", "C10"], ["V003-RESULT", "V003-CONTRAST-SUMMARY", "V003-TASK-EFFECTS"]),
        ("11_components", "Cumulative-memory and diagnostic component contrasts", ["C05", "C06", "C11", "C23", "C24"], ["V003-RESULT", "V001-RESULT", "V002-RESULT"]),
        ("12_behavior", "Novelty, duplication, and date-block diagnostics", ["C12", "C13", "C14", "C15"], ["V003-BEHAVIOR-EFFECTS", "V003-DATE-BLOCK-SUMMARY"]),
        ("13_execution", "Execution losses, recovery, sensitivity, and cost", ["C16", "C17", "C18", "C19", "C20"], ["V003-RESULT", "V003-EXECUTION-COSTS", "V003-AUXILIARY-EXECUTION-ANALYSIS", "V003-REPLICATE-STABILITY", "EXECUTION-PLAN"]),
        ("14_discussion", "Discussion", ["C07", "C08", "C12", "C13", "C14", "C15"], ["V003-RESULT", "V003-BEHAVIOR-EFFECTS"]),
        ("15_limitations", "Limitations and evidence boundary", ["C21", "C22", "C28"], ["V003-RESULT", "EXECUTION-PLAN"]),
        ("16_conclusion", "Conclusion", ["C05", "C06", "C07", "C08", "C22"], ["V003-RESULT"]),
        ("17_appendix", "Reproduction and supplementary tables", ["C01", "C02", "C03", "C16", "C18", "C25"], ["V003-PROTOCOL", "V003-RESULT", "V003-EXECUTION-COSTS", "EXECUTION-PLAN"]),
    ]
    source_by_id = {source["id"]: source for source in sources}
    claim_by_id = {claim["claim_id"]: claim for claim in claims}
    registry = []
    for index, (uid, title, claim_ids, source_ids) in enumerate(units):
        unit_status = "drafted" if (RUN / f"drafts/{uid}.tex").is_file() else "planned"
        context = {
            "schema": "AUTO-RESEARCH-WRITING-CONTEXT-1",
            "unit_id": uid,
            "title": title,
            "research_version": "V003",
            "status": unit_status,
            "sources": [source_by_id[sid] for sid in source_ids],
            "claims": [claim_by_id[cid] for cid in claim_ids],
            "terminology": ["research harness", "active branch", "frozen champion", "probe-only exploration", "atomic proposal", "mean absolute IC", "development panel"],
            "paragraph_task": f"Draft the English manuscript unit '{title}' using only the registered evidence and verified literature sources.",
            "adjacent_context": {
                "before": units[index - 1][1] if index else "Document opening",
                "after": units[index + 1][1] if index + 1 < len(units) else "Document end",
            },
            "evidence_boundary": "Confirmatory development-panel evidence within 2017-01-01 through 2020-11-30; no independent real-data out-of-sample result.",
            "writing_status": f"Source-bound manuscript draft: drafts/{uid}.tex" if unit_status == "drafted" else "No manuscript prose has been drafted in this workspace.",
        }
        if uid == "03_related":
            context["editor_instruction"] = "Verify bibliographic metadata and claim scope at the original source before citing any literature lead. The historical survey alone does not authorize a citation."
        dump(RUN / f"contexts/{uid}.json", context)
        registry.append({
            "id": uid,
            "run_id": f"auto-research-v003-{uid}",
            "purpose": "source_bound_manuscript_unit",
            "status": unit_status,
            "draft": f"auto_research/writing_runs/{RUN.name}/drafts/{uid}.tex",
            "context": f"auto_research/writing_runs/{RUN.name}/contexts/{uid}.json",
        })
    dump(RUN / "all_units.json", registry)
    print(json.dumps({"writing_units": len(registry), "claims": len(claims), "sources": len(sources), "paired_units": 24}))


def resolve_pointer(value, pointer: str) -> None:
    current = value
    for raw_part in pointer.removeprefix("/").split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]


def verify_claim_locators(sources: dict[str, dict], claims: list[dict]) -> None:
    documents = {}
    for claim in claims:
        source = sources[claim["source_id"]]
        path = Path(source["path"])
        if not path.is_absolute():
            path = PROJECT / path
        if source["id"] not in documents:
            if source["kind"] == "json":
                documents[source["id"]] = load_json(path)
            elif source["kind"] == "csv":
                with path.open(newline="", encoding="utf-8") as stream:
                    documents[source["id"]] = list(csv.DictReader(stream))
            else:
                documents[source["id"]] = path.read_text(encoding="utf-8")
        document = documents[source["id"]]
        for locator in claim["locators"]:
            if isinstance(locator, str):
                assert source["kind"] == "json", (claim["claim_id"], locator)
                resolve_pointer(document, locator)
            elif source["kind"] == "markdown":
                assert locator["kind"] == "heading" and locator["text"] in document, (claim["claim_id"], locator)
            elif source["kind"] == "csv":
                rows = document
                for key in ["comparison", "metric", "contrast", "task", "arm"]:
                    if key in locator:
                        rows = [row for row in rows if row[key] == str(locator[key])]
                if "rows" in locator:
                    rows = [row for row in rows if row["arm"] in locator["rows"]]
                assert rows, (claim["claim_id"], locator)
                fields = locator.get("fields", [locator["field"]] if "field" in locator else [])
                for row in rows:
                    for field in fields:
                        assert field in row and row[field] != "", (claim["claim_id"], locator, field)
            else:
                raise AssertionError((claim["claim_id"], locator))


def check() -> None:
    packet = load_json(RUN / "evidence/results_packet.json")
    claim_map = load_json(RUN / "claim_map.json")
    registry = load_json(RUN / "all_units.json")
    sources = {source["id"]: source for source in load_json(RUN / "evidence/sources.json")["sources"]}
    assert len(registry) == 18 and len(claim_map["claims"]) == 28
    verify_claim_locators(sources, claim_map["claims"])
    assert packet["paired_units"] == 24 and packet["evidence_status"] == "development-panel complete"
    assert packet["score_window_read"] is False and packet["independent_real_data_oos"] is False
    for unit in registry:
        assert (PROJECT / unit["context"]).is_file()
        draft_exists = (PROJECT / unit["draft"]).is_file()
        assert unit["status"] == ("drafted" if draft_exists else "planned")
    for context_path in sorted(RUN.glob("contexts/[0-9][0-9]_*.json")):
        context = load_json(context_path)
        draft_exists = (RUN / f"drafts/{context['unit_id']}.tex").is_file()
        assert context["status"] == ("drafted" if draft_exists else "planned") and context["sources"]
        context_source_ids = {source["id"] for source in context["sources"]}
        for claim in context["claims"]:
            assert claim["source_id"] in context_source_ids, (context_path.name, claim["claim_id"])
    print(json.dumps({"status": "PASS", "writing_units": len(registry), "claims": len(claim_map["claims"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    check() if args.check else build()
