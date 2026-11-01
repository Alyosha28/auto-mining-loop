"""Convert the verified Archify canonical SVG into publication PDF and PNG."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


RUN = Path(__file__).resolve().parents[1]
FIGURES = RUN / "figures"
EXPORT = RUN / ".archify/workflow-research-harness-20261031-233426/exported-svg.json"
WORK = EXPORT.parent
STEM = FIGURES / "figure_01_method_pipeline"


def chrome_executable() -> str:
    candidates = [
        os.environ.get("CHROME_BIN"),
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("Chrome or Chromium is required; set CHROME_BIN to the browser executable")


def main() -> None:
    svg = json.loads(EXPORT.read_text(encoding="utf-8"))["text"]
    match = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg)
    if not match:
        raise ValueError("Canonical SVG has no measurable viewBox")
    width, height = (float(value) for value in match.groups())
    STEM.with_suffix(".svg").write_text(svg, encoding="utf-8")
    wrapper = WORK / "print.html"
    wrapper.write_text(
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        f"@page{{size:{width}px {height}px;margin:0}}"
        f"html,body{{margin:0;padding:0;width:{width}px;height:{height}px;background:#fff}}"
        f"svg{{display:block;width:{width}px;height:{height}px}}"
        "</style></head><body>" + svg + "</body></html>",
        encoding="utf-8",
    )
    subprocess.run([
        chrome_executable(),
        "--headless",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={STEM.with_suffix('.pdf')}",
        wrapper.as_uri(),
    ], check=True)
    subprocess.run([
        "pdftoppm",
        "-singlefile",
        "-png",
        "-r",
        "300",
        str(STEM.with_suffix(".pdf")),
        str(STEM),
    ], check=True)
    print(json.dumps({"status": "PASS", "viewBox": [width, height], "files": [str(STEM.with_suffix(suffix)) for suffix in [".svg", ".pdf", ".png"]]}))


if __name__ == "__main__":
    main()
