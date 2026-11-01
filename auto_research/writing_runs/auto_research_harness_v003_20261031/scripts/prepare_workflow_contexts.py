"""Convert source-bound V003 contexts into the local writing-workflow schema."""

from __future__ import annotations

import json
from pathlib import Path
import re


RUN = Path(__file__).resolve().parents[1]
PROJECT = RUN.parents[2]
PACKET_REL = "auto_research/writing_runs/auto_research_harness_v003_20261031/evidence/results_packet.json"
LITERATURE_REL = "auto_research/writing_runs/auto_research_harness_v003_20261031/evidence/literature.json"
PACKET = json.loads((RUN / "evidence/results_packet.json").read_text(encoding="utf-8"))
CLAIM_MAP = json.loads((RUN / "claim_map.json").read_text(encoding="utf-8"))["claims"]
UNITS = json.loads((RUN / "all_units.json").read_text(encoding="utf-8"))


def resolve(document, pointer: str):
    current = document
    for raw in pointer.removeprefix("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(part)] if isinstance(current, list) else current[part]
    return current


def source(source_id: str, pointer: str, path: str = PACKET_REL) -> dict:
    document = PACKET if path == PACKET_REL else json.loads((PROJECT / path).read_text(encoding="utf-8"))
    return {
        "id": source_id,
        "path": path,
        "locator": {"kind": "json_pointer", "pointer": pointer},
        "expected": resolve(document, pointer),
    }


def behavior_index(comparison: str, metric: str) -> int:
    rows = PACKET["tables"]["behavior_effects"]
    return next(index for index, row in enumerate(rows) if row["comparison"] == comparison and row["metric"] == metric)


def cost_index(arm: str) -> int:
    return next(index for index, row in enumerate(PACKET["tables"]["execution_costs"]) if row["arm"] == arm)


def custom_sources(claim: dict) -> list[dict]:
    cid = claim["claim_id"]
    if cid == "C04":
        return [source(cid, "/protocol/generation/proposal_schema")]
    if cid == "C20":
        return [source(cid, "/interpretation/complete_execution")]
    if cid == "C21":
        return [source(cid + "_unit", "/interpretation/experimental_unit"), source(cid + "_reps", "/protocol/model_replicates")]
    if cid == "C27":
        return [source(cid + "_random", "/protocol/random_model_id"), source(cid + "_calls", "/tables/execution_costs/3/model_calls")]
    if cid == "C28":
        return [source(cid, "/interpretation/market_scope")]
    return []


def sources_for_claim(claim: dict) -> list[dict]:
    special = custom_sources(claim)
    if special:
        return special
    records = []
    original_source = claim["source_id"]
    for index, locator in enumerate(claim["locators"]):
        sid = f"{claim['claim_id']}_{index}"
        if original_source == "V003-PROTOCOL":
            if isinstance(locator, dict):
                continue
            records.append(source(sid, "/protocol" + locator))
        elif original_source == "V003-RESULT":
            if locator == "/all_protocol_checks_passed":
                records.append(source(sid, "/evidence_status"))
            else:
                records.append(source(sid, locator))
        elif original_source in {"V001-RESULT", "V002-RESULT"}:
            version = original_source[:4]
            records.append(source(sid, f"/exploratory_panels/{version}{locator}"))
        elif original_source == "V003-BEHAVIOR-EFFECTS":
            row_index = behavior_index(locator["comparison"], locator["metric"])
            pointer = f"/tables/behavior_effects/{row_index}"
            if "field" in locator:
                pointer += "/" + locator["field"]
            records.append(source(sid, pointer))
        elif original_source == "V003-EXECUTION-COSTS":
            arms = locator.get("rows", ["scalar", "memory", "diagnostic", "random"])
            fields = locator.get("fields", [locator.get("field")])
            for arm in arms:
                for field in fields:
                    if field:
                        records.append(source(f"{sid}_{arm}_{field}", f"/tables/execution_costs/{cost_index(arm)}/{field}"))
        else:
            raise ValueError(f"Unmapped claim {claim['claim_id']} from {original_source}: {locator}")
    if not records:
        raise ValueError(f"No source records for {claim['claim_id']}")
    return records


def claim_from_sources(cid: str, statement: str, sources: list[dict], scope: str = "V003 development panel") -> dict:
    return {
        "id": cid,
        "object": statement,
        "value": statement,
        "unit": "source-bound registered claim",
        "comparison": statement,
        "scope": scope,
        "source_ids": [record["id"] for record in sources],
    }


def pointer_claim(cid: str, pointer: str, statement: str | None = None) -> tuple[dict, list[dict]]:
    record = source(cid, pointer)
    value = record["expected"]
    text = statement or f"{pointer} = {value}"
    return claim_from_sources(cid, text, [record]), [record]


def literature_claim(key: str) -> tuple[dict, list[dict]]:
    pointer = f"/entries/{key}/supported_claim"
    record = source(key, pointer, LITERATURE_REL)
    claim = claim_from_sources(key, record["expected"], [record], "Original bibliographic record and abstract")
    claim["unit"] = "source-scoped system description"
    claim["comparison"] = "prior research system"
    return claim, [record]


def extra_claims(unit_id: str) -> list[tuple[dict, list[dict]]]:
    extras = []
    if unit_id == "02_introduction":
        extras = [literature_claim("lu2026towards"), literature_claim("chandran2026production")]
    elif unit_id == "03_related":
        extras = [literature_claim(key) for key in [
            "lu2026towards", "xu2026harness2", "yang2026aris", "chandran2026production",
            "romera2023mathematical", "agrawal2025gepa", "huang2023mlagentbench",
            "novikov2025alphaevolve", "liu2024evolution", "jiang2025aide",
            "schmidgall2025agent", "wijk2024rebench", "chan2024mlebench",
            "yang2024sweagent", "chen2024scienceagentbench", "jansen2024discoveryworld",
            "liu2023agentbench", "liu2025cogalpha", "yu2023generating",
        ]]
    elif unit_id == "10_primary":
        for name, pointer in [
            ("random_t12", "/contrasts/memory_minus_random/task_medians/T12"),
            ("random_t13", "/contrasts/memory_minus_random/task_medians/T13"),
            ("fixed_t12", "/contrasts/memory_minus_fixed/task_medians/T12"),
            ("fixed_t9", "/contrasts/memory_minus_fixed/task_medians/T9"),
        ]:
            extras.append(pointer_claim(name, pointer))
    elif unit_id == "12_behavior":
        for name, pointer in [
            ("date_scalar_n", "/tables/date_block_summary/0/non_null_units"),
            ("date_scalar_median", "/tables/date_block_summary/0/median_concentration"),
            ("date_scalar_max", "/tables/date_block_summary/0/max_concentration"),
            ("date_diagnostic_n", "/tables/date_block_summary/1/non_null_units"),
            ("date_diagnostic_median", "/tables/date_block_summary/1/median_concentration"),
            ("date_diagnostic_max", "/tables/date_block_summary/1/max_concentration"),
        ]:
            extras.append(pointer_claim(name, pointer))
    elif unit_id == "13_execution":
        for name, pointer in [
            ("model_positions", "/usage/model_candidate_positions"),
            ("random_positions", "/usage/random_candidate_positions"),
            ("executed_decisions", "/usage/executed_decisions"),
            ("rep_scalar_median", "/tables/replicate_stability/0/median_absolute_A_minus_B"),
            ("rep_scalar_max", "/tables/replicate_stability/0/max_absolute_A_minus_B"),
            ("rep_scalar_identical", "/tables/replicate_stability/0/identical_pairs"),
            ("rep_diagnostic_identical", "/tables/replicate_stability/1/identical_pairs"),
        ]:
            extras.append(pointer_claim(name, pointer))
        for index, row in enumerate(PACKET["tables"]["auxiliary_execution_analysis"]):
            for field in ["complete_units", "median_delta"]:
                pointer = f"/tables/auxiliary_execution_analysis/{index}/{field}"
                extras.append(pointer_claim(f"complete_{index}_{field}", pointer))
    elif unit_id == "17_appendix":
        for index, contrast in enumerate(["memory_minus_scalar", "diagnostic_minus_memory", "memory_minus_random", "memory_minus_fixed"]):
            for field in ["min", "max"]:
                extras.append(pointer_claim(f"range_{index}_{field}", f"/contrasts/{contrast}/{field}"))
            for direction in ["positive", "zero", "negative"]:
                extras.append(pointer_claim(f"count_{index}_{direction}", f"/contrasts/{contrast}/delta_counts/{direction}"))
        for index, _row in enumerate(PACKET["tables"]["auxiliary_execution_analysis"]):
            for field in ["complete_units", "median_delta", "mean_delta", "median_delta_per_joint_executed_decision"]:
                extras.append(pointer_claim(f"appendix_complete_{index}_{field}", f"/tables/auxiliary_execution_analysis/{index}/{field}"))
        for name, pointer in [
            ("cli_version", "/protocol/generation/cli_version"),
            ("python_version", "/protocol/generation/python_version"),
            ("random_seed", "/protocol/random_seed"),
            ("bootstrap_seed", "/protocol/analysis/bootstrap_seed"),
            ("output_call_cap", "/protocol/proposer_environment/AUTO_RESEARCH_CLAUDE_MAX_OUTPUT_TOKENS"),
        ]:
            extras.append(pointer_claim(name, pointer))
    return extras


def preserved_text(draft: str) -> list[str]:
    preserved = []
    preserved.extend(re.findall(r"\\(?:section|subsection)\{[^}]+\}", draft))
    if "\\appendix" in draft:
        preserved.append("\\appendix")
    preserved.extend(re.findall(r"\\cite[tp]\{[^}]+\}", draft))
    preserved.extend(match.group(0) for match in re.finditer(r"\\begin\{(figure\*?|table\*?|equation)\}.*?\\end\{\1\}", draft, re.S))
    return preserved


def build() -> None:
    output = RUN / "workflow_contexts"
    output.mkdir(exist_ok=True)
    plan = []
    for unit in UNITS:
        uid = unit["id"]
        original = json.loads((RUN / f"contexts/{uid}.json").read_text(encoding="utf-8"))
        draft = (RUN / f"drafts/{uid}.tex").read_text(encoding="utf-8")
        claims = []
        sources = []
        for original_claim in original["claims"]:
            mapped = next(item for item in CLAIM_MAP if item["claim_id"] == original_claim["claim_id"])
            claim_sources = sources_for_claim(mapped)
            sources.extend(claim_sources)
            claims.append(claim_from_sources(mapped["claim_id"], mapped["statement"], claim_sources))
        for claim, claim_sources in extra_claims(uid):
            claims.append(claim)
            sources.extend(claim_sources)
        unique_sources = list({record["id"]: record for record in sources}.values())
        context = {
            "research_version": "V003",
            "sources": unique_sources,
            "claims": claims,
            "terminology": original["terminology"] + ["mean absolute IC", "factor expression candidate", "development panel"],
            "paragraph_task": original["paragraph_task"],
            "adjacent_context": original["adjacent_context"],
            "editor_instruction": (
                original.get("editor_instruction", "Rewrite the supplied source-bound draft as polished academic English. ")
                + " Preserve every protected LaTeX block, citation, number, comparison direction, and evidence scope. "
                + " Remove defensive writing, stacked hedging, repeated justification, preemptive clarification, imaginary objections, "
                + "mechanical signposts, repetitive sentence patterns, list-form prose, and uniform paragraph scaffolding. "
                + "Use coherent paragraphs and keep mean absolute IC attached to factor-expression candidates."
            ),
            "must_preserve": preserved_text(draft),
            "rewrite_from_draft": True,
            "draft_provenance": f"GPT source-bound draft auto_research/writing_runs/auto_research_harness_v003_20261031/drafts/{uid}.tex",
        }
        (output / f"{uid}.json").write_text(json.dumps(context, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        plan.append({"id": uid, "run_id": unit["run_id"], "purpose": "paragraph_revision", "context": f"workflow_contexts/{uid}.json", "draft": f"drafts/{uid}.tex"})
    (RUN / "workflow_run_plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"units": len(plan), "contexts": len(list(output.glob('[0-9][0-9]_*.json')))}))


if __name__ == "__main__":
    build()
