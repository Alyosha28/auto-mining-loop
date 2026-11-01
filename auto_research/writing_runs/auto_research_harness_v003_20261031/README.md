# Auto-research harness V003 writing workspace

Status: complete source-bound English working draft with editable LaTeX, review PDF, and publication-size preview.
Created: 2026-10-31.
Workspace: `auto_research/writing_runs/auto_research_harness_v003_20261031/`.

This workspace prepares an independent manuscript about budget-bounded LLM-driven research-harness evolution. Manuscript content and research evidence belong to `auto_research`. Shared writing, figure, and layout tooling required for reproduction is packaged in this workspace.

## Evidence roles

- V001 and V002 are exploratory development panels. Their null results, invalid proposals, and execution failures remain in the historical record.
- V003 is the completed confirmatory development panel: six tasks, two development splits, two independent model-call replicates, and 24 paired units.
- The market-data window is 2017-01-01 through 2020-11-30. The study provides no independent real-data out-of-sample result.
- Fresh tasks inside the development window support fresh-task development evidence only.

## Workspace contract

- `ARCHITECTURE_MAP.md` maps the existing writing, evidence, figure, layout, and review systems into this run.
- `all_units.json` registers eighteen source-bound writing units and their draft status.
- `contexts/` binds each unit to scoped sources, claims, terminology, and writing instructions.
- `evidence/results_packet.json` contains the compact quantitative packet used for drafting.
- `evidence/sources.json` records source paths and locators.
- `claim_map.json` binds manuscript claims to exact source fields or rows.
- `WRITING_PLAN.md` fixes the argument, evidence boundaries, and review sequence.
- `layout.json` fixes the A4 review draft and two-column publication-preview profiles.
- `.archify/workflow-research-harness-20261031-233426/candidate.json` is the Archify workflow source for the method diagram; `exported-svg.json` is its canonical light SVG export.
- `figures/figure_contracts.json` records plotted values, transformations, captions, exports, and visual checks.
- `figures/generate_figures.py` deterministically generates vector PDF, SVG, and 300 dpi PNG files from the V003 CSV files. Each data figure has `review_wide` and `preview_wide` variants with native physical dimensions; LaTeX selects the variant through `\ResearchFigureVariant`.

The primary endpoint is paired `mean_abs_ic`: the mean across candidates of the absolute per-candidate mean daily cross-sectional Spearman IC. Model calls and candidate budgets are fixed by the V003 protocol. Failed or invalid model decisions remain in the primary fixed-call-budget analysis.

## Manuscript

- `main.tex` is the editable anonymous manuscript entry point.
- `drafts/` contains all eighteen source-bound units; `content.tex` is their deterministic assembly.
- `main.pdf` is the eighteen-page A4 single-column review draft.
- `journal-preview.pdf` is the nine-page two-column publication-size preview.
- `references.bib` contains nineteen verified literature records.
- `manuscript_status.json` records provenance, review, authorization, and watermark status.
- `VERIFICATION.md` records mechanical, evidence, compilation, and visual checks.

The accepted manuscript text is controller-authored from the registered evidence. Local Cajal and humanizer candidates were reviewed but not adopted. Author review has not been recorded, submission has not been authorized, and watermark verification remains `UNVERIFIED`.

## Reproduction

Use Python 3.13 and install the declared Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

The figure and PDF checks also require Poppler (`pdftoppm`, `pdfinfo`, and `pdffonts`). Manuscript compilation requires `latexmk` with XeLaTeX. Regenerating the method PDF and PNG requires Chrome or Chromium; set `CHROME_BIN` when the browser is not on the executable search path. The committed PDF, SVG, and PNG exports can be checked without a browser.

Run the data-free evidence and manuscript checks from this directory:

```bash
python scripts/build_workspace.py --check
python scripts/verify_manuscript_statistics.py
python figures/generate_figures.py --check
python scripts/assemble_manuscript.py --check
```

Regenerate the method diagram, data figures, manuscripts, and local inspection renders with:

```bash
bash figures/render_method.sh
python figures/generate_figures.py
latexmk -xelatex main.tex
latexmk -xelatex journal-preview.tex
python scripts/render_manuscript.py
```

The figure generator reads only the packaged V003 result, protocol, and paper-material CSV files. It does not read raw market panels or execute research.
