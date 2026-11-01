"""Expand the manuscript bibliography from verified primary metadata."""

from __future__ import annotations

import json
from pathlib import Path


RUN = Path(__file__).resolve().parents[1]
METADATA = json.loads((RUN / "evidence/arxiv_metadata.json").read_text(encoding="utf-8"))["entries"]
LITERATURE_PATH = RUN / "evidence/literature.json"
BIB_PATH = RUN / "references.bib"

SPECS = {
    "agrawal2025gepa": {
        "arxiv": "2507.19457",
        "claim": "GEPA reflects on execution traces in natural language to propose and test prompt updates and combines complementary candidates from a Pareto frontier.",
    },
    "huang2023mlagentbench": {
        "arxiv": "2310.03302",
        "claim": "MLAgentBench evaluates language agents on 13 machine-learning experimentation tasks through actions including file editing, code execution, and output inspection.",
    },
    "novikov2025alphaevolve": {
        "arxiv": "2506.13131",
        "claim": "AlphaEvolve evolves code through an autonomous LLM pipeline with feedback from one or more evaluators.",
    },
    "liu2024evolution": {
        "arxiv": "2401.02051",
        "claim": "EoH evolves natural-language heuristic ideas and their executable code within an evolutionary search framework.",
    },
    "jiang2025aide": {
        "arxiv": "2502.13138",
        "claim": "AIDE formulates machine-learning engineering as code optimization and uses tree search to reuse and refine promising solutions.",
    },
    "schmidgall2025agent": {
        "arxiv": "2501.04227",
        "claim": "Agent Laboratory organizes LLM agents across literature review, experimentation, and report writing, while allowing human feedback at each stage.",
    },
    "wijk2024rebench": {
        "arxiv": "2411.15114",
        "claim": "RE-Bench compares language-model agents and human experts in seven open-ended ML research-engineering environments under varying time budgets.",
    },
    "chan2024mlebench": {
        "arxiv": "2410.07095",
        "claim": "MLE-bench evaluates agents on 75 Kaggle machine-learning engineering competitions and includes human leaderboard baselines.",
    },
    "yang2024sweagent": {
        "arxiv": "2405.15793",
        "claim": "SWE-agent studies how agent-computer interface design affects code editing, repository navigation, test execution, and agent performance.",
    },
    "chen2024scienceagentbench": {
        "arxiv": "2410.05080",
        "claim": "ScienceAgentBench evaluates 102 expert-validated data-driven discovery tasks using program quality, execution results, and cost.",
    },
    "jansen2024discoveryworld": {
        "arxiv": "2406.06769",
        "claim": "DiscoveryWorld provides 120 simulated tasks requiring hypotheses, experiment design and execution, analysis, and conclusions.",
    },
    "liu2023agentbench": {
        "arxiv": "2308.03688",
        "claim": "AgentBench evaluates LLM agents across eight interactive environments and analyzes reasoning, decision-making, and instruction-following failures.",
    },
    "liu2025cogalpha": {
        "arxiv": "2511.18850",
        "claim": "CogAlpha represents alphas as code and combines LLM reasoning, evolutionary mutations and recombination, multi-stage prompts, and financial feedback.",
    },
    "yu2023generating": {
        "arxiv": "2306.12964",
        "claim": "AlphaGen uses reinforcement learning to optimize a synergistic collection of formulaic alphas through downstream combination-model performance.",
        "container": "Proceedings of the 29th ACM SIGKDD Conference on Knowledge Discovery and Data Mining",
        "bib_type": "inproceedings",
        "pages": "5476--5486",
    },
}

FUNSEARCH = {
    "title": "Mathematical discoveries from program search with large language models",
    "authors": ["Bernardino Romera-Paredes", "Mohammadamin Barekatain", "Alexander Novikov", "Matej Balog", "M. Pawan Kumar", "Emilien Dupont", "Francisco J. R. Ruiz", "Jordan S. Ellenberg", "Pengming Wang", "Omar Fawzi", "Pushmeet Kohli", "Alhussein Fawzi"],
    "year": 2023,
    "container": "Nature",
    "volume": "625",
    "pages": "468--475",
    "doi": "10.1038/s41586-023-06924-6",
    "source_urls": ["https://api.crossref.org/works/10.1038/s41586-023-06924-6"],
    "supported_claim": "FunSearch pairs a pretrained LLM with a systematic evaluator in an evolutionary search over solution-generating programs.",
}


def latex_title(title: str) -> str:
    return title.replace("&", "\\&")


def bib_entry(key: str, record: dict, spec: dict) -> str:
    authors = " and ".join(record["authors"])
    title = latex_title(record["title"])
    year = str(record["year"] if "year" in record else record["published"][:4])
    doi = record.get("doi") or f"10.48550/arXiv.{record['id']}"
    if key == "romera2023mathematical":
        return f"@article{{{key},\n  author = {{{authors}}},\n  title = {{{{{title}}}}},\n  journal = {{Nature}},\n  volume = {{625}},\n  pages = {{468--475}},\n  year = {{{year}}},\n  doi = {{{doi}}}\n}}\n"
    if spec.get("bib_type") == "inproceedings":
        booktitle = spec["container"]
        return f"@inproceedings{{{key},\n  author = {{{authors}}},\n  title = {{{{{title}}}}},\n  booktitle = {{{booktitle}}},\n  pages = {{{spec['pages']}}},\n  year = {{{year}}},\n  doi = {{{doi}}}\n}}\n"
    return f"@misc{{{key},\n  author = {{{authors}}},\n  title = {{{{{title}}}}},\n  year = {{{year}}},\n  howpublished = {{arXiv:{record['id']}}},\n  doi = {{{doi}}}\n}}\n"


def main() -> None:
    literature = json.loads(LITERATURE_PATH.read_text(encoding="utf-8"))
    literature["entries"]["romera2023mathematical"] = FUNSEARCH
    bibliography = BIB_PATH.read_text(encoding="utf-8").rstrip() + "\n\n"
    new_entries = []
    for key, spec in SPECS.items():
        record = METADATA[spec["arxiv"]]
        doi = record.get("doi") or f"10.48550/arXiv.{record['id']}"
        urls = [record["primary_url"]]
        if record.get("doi"):
            urls.insert(0, f"https://doi.org/{doi}")
        literature["entries"][key] = {
            "title": record["title"],
            "authors": record["authors"],
            "year": int(record["published"][:4]),
            "container": spec.get("container", f"arXiv:{record['id']}"),
            "doi": doi,
            "source_urls": urls,
            "supported_claim": spec["claim"],
        }
        new_entries.append(bib_entry(key, record, spec))
    funsearch_record = dict(FUNSEARCH)
    funsearch_record["id"] = "2306.12964"
    new_entries.insert(0, bib_entry("romera2023mathematical", FUNSEARCH, {}))
    for key, entry in zip(["romera2023mathematical", *SPECS], new_entries):
        if f"{{{key}," not in bibliography:
            bibliography += entry + "\n"
    LITERATURE_PATH.write_text(json.dumps(literature, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    BIB_PATH.write_text(bibliography, encoding="utf-8")
    print(json.dumps({"status": "PASS", "literature_entries": len(literature["entries"]), "bibliography_entries": bibliography.count("@")}))


if __name__ == "__main__":
    main()
