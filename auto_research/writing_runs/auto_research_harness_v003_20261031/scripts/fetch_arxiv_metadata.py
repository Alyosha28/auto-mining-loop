"""Fetch primary arXiv metadata for manuscript literature verification."""

from __future__ import annotations

import json
from pathlib import Path
import urllib.request
import xml.etree.ElementTree as ET


RUN = Path(__file__).resolve().parents[1]
IDS = [
    "2507.19457",
    "2310.03302",
    "2506.13131",
    "2401.02051",
    "2502.13138",
    "2501.04227",
    "2411.15114",
    "2410.07095",
    "2405.15793",
    "2410.05080",
    "2406.06769",
    "2308.03688",
    "2511.18850",
    "2306.12964",
]
NAMESPACE = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


def normalized(element) -> str:
    return " ".join((element.text or "").split()) if element is not None else ""


def main() -> None:
    url = "https://export.arxiv.org/api/query?id_list=" + ",".join(IDS) + f"&max_results={len(IDS)}"
    request = urllib.request.Request(url, headers={"User-Agent": "auto-research-literature-verification/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    entries = {}
    for entry in root.findall("atom:entry", NAMESPACE):
        arxiv_id = normalized(entry.find("atom:id", NAMESPACE)).rsplit("/", 1)[-1].split("v")[0]
        entries[arxiv_id] = {
            "id": arxiv_id,
            "title": normalized(entry.find("atom:title", NAMESPACE)),
            "authors": [normalized(author.find("atom:name", NAMESPACE)) for author in entry.findall("atom:author", NAMESPACE)],
            "published": normalized(entry.find("atom:published", NAMESPACE)),
            "updated": normalized(entry.find("atom:updated", NAMESPACE)),
            "summary": normalized(entry.find("atom:summary", NAMESPACE)),
            "doi": normalized(entry.find("arxiv:doi", NAMESPACE)),
            "journal_ref": normalized(entry.find("arxiv:journal_ref", NAMESPACE)),
            "primary_url": f"https://arxiv.org/abs/{arxiv_id}",
        }
    missing = sorted(set(IDS) - set(entries))
    if missing:
        raise ValueError(f"Missing arXiv entries: {missing}")
    ordered = {arxiv_id: entries[arxiv_id] for arxiv_id in IDS}
    output = RUN / "evidence/arxiv_metadata.json"
    output.write_text(json.dumps({"schema": "AUTO-RESEARCH-ARXIV-METADATA-1", "source": url, "entries": ordered}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": "PASS", "entries": len(ordered), "output": str(output)}))


if __name__ == "__main__":
    main()
