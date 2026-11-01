"""Render manuscript pages and build visual-inspection contact sheets."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

from PIL import Image, ImageDraw


RUN = Path(__file__).resolve().parents[1]


def render(pdf_name: str, folder_name: str, columns: int, thumb_width: int) -> dict:
    output = RUN / "rendered" / folder_name
    output.mkdir(parents=True, exist_ok=True)
    prefix = output / "page"
    subprocess.run(["pdftoppm", "-png", "-r", "144", str(RUN / pdf_name), str(prefix)], check=True, capture_output=True)
    pages = sorted(output.glob("page-*.png"))
    if not pages:
        raise RuntimeError(f"No pages rendered for {pdf_name}")
    thumbnails = []
    for index, path in enumerate(pages, start=1):
        image = Image.open(path).convert("RGB")
        height = round(image.height * thumb_width / image.width)
        image = image.resize((thumb_width, height), Image.Resampling.LANCZOS)
        tile = Image.new("RGB", (thumb_width, height + 28), "white")
        tile.paste(image, (0, 28))
        ImageDraw.Draw(tile).text((8, 7), f"Page {index}", fill="black")
        thumbnails.append(tile)
    rows = (len(thumbnails) + columns - 1) // columns
    tile_width = thumb_width + 12
    tile_height = max(tile.height for tile in thumbnails) + 12
    sheet = Image.new("RGB", (columns * tile_width, rows * tile_height), "#D8D8D8")
    for index, tile in enumerate(thumbnails):
        x = (index % columns) * tile_width + 6
        y = (index // columns) * tile_height + 6
        sheet.paste(tile, (x, y))
    sheet_path = RUN / "rendered" / f"{folder_name}-contact.png"
    sheet.save(sheet_path)
    return {"pdf": pdf_name, "pages": len(pages), "contact_sheet": str(sheet_path), "page_directory": str(output)}


def main() -> None:
    report = {
        "review": render("main.pdf", "review", 3, 420),
        "preview": render("journal-preview.pdf", "preview", 2, 570),
    }
    (RUN / "rendered/render_manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
