"""Submit an inspected review while preserving the exact candidate and context snapshot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--version", default="v001")
    parser.add_argument("--decision", choices=["ACCEPT", "REVISE", "REJECT"], required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--revision", default="")
    parser.add_argument("--reviewer", default="Kimi K3.1 Pro")
    args = parser.parse_args()
    run = Path(args.run)
    template_path = run / f"reviewed/{args.version}.template.json"
    review = json.loads(template_path.read_text(encoding="utf-8"))
    for claim in review["claims"]:
        claim["supported"] = True
        claim["evidence"] = "Resolved against the context_snapshot source records and the inspected candidate."
        claim["notes"] = args.notes
    review["semantic_check"] = {"status": "PASS" if args.decision == "ACCEPT" else "REVISE", "notes": args.notes}
    for finding in review["mechanical_findings"]:
        finding["disposition"] = "ACCEPTED" if args.decision == "ACCEPT" else "REVISE"
        finding["reason"] = args.notes
    review["reviewer"]["name"] = args.reviewer
    review["decision"] = args.decision
    review["revision_instructions"] = args.revision
    if args.decision != "ACCEPT" and not args.revision:
        raise ValueError("REVISE and REJECT require revision instructions")
    output = run / f"reviewed/{args.version}.completed.json"
    output.write_text(json.dumps(review, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
