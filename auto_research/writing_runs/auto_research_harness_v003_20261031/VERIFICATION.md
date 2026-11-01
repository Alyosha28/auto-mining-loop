# Verification record

Date: 2026-10-31.
Workspace: `auto_research/writing_runs/auto_research_harness_v003_20261031/`.

## Workspace validation

Command:

```bash
python3 scripts/build_workspace.py --check
```

Result: `PASS`; eighteen writing units and twenty-eight quantitative or scope claims validated. Every registered context exists and has scoped sources. Every JSON pointer resolves to an actual result or protocol field, every CSV selector resolves to a nonempty source row, every Markdown locator resolves to an existing section, and every unit directly carries the source required by each of its claims. The workspace builder also checks that planned decisions minus executed steps in `execution_costs.csv` equal the retained failed or invalid decision count.

## Statistical verification

Command:

```bash
python3 scripts/verify_manuscript_statistics.py
```

Result: `PASS`. The script imports the frozen `run_paper_readiness_v003.py`, rebuilds the protocol bootstrap indices, recomputes all four contrasts from `unit_effects.csv`, and compares every reported median, mean, range, task median, direction count, cluster interval, exact p-value, and Holm-adjusted p-value with `results_packet.json`. The report is stored in `statistical_verification.json`.

The frozen analysis resamples six complete task clusters and summarizes the median across all 24 unit effects. Its exact test enumerates all 64 sign assignments, applies one sign to the four units within each task, and recomputes the same 24-unit median. The manuscript describes this statistic directly. Task medians are reported for descriptive heterogeneity only.

## Figure validation

Commands:

```bash
bash figures/render_method.sh
python figures/generate_figures.py
python figures/generate_figures.py --check
```

The project virtual environment was used for Matplotlib. Results:

- Four data figures generated in `review_wide` and `preview_wide` variants as PDF, SVG, and PNG: 24 exports. Their native dimensions prevent publication-preview upscaling from inflating figure heights or changing font sizes.
- One Archify method figure generated as PDF, SVG, and PNG. It uses four lanes, orthogonal routes, short node labels, and a canonical light SVG export. Probe-to-champion is a dedicated vertical route with no crossing; the active-to-proposer continuation is stated directly in the node label.
- Archify `finalize` passed schema validation, delivery, strict checking, and browser checking. `visual-check` captured light and dark viewports at two desktop sizes; the final receipt reports no route crossings, and the light captures were directly inspected.
- All 24 paired-unit effects and all task medians were recomputed from `unit_effects.csv` and checked against the summary CSV files.
- PDF physical dimensions and 300 dpi PNG dimensions passed for every data-figure variant.
- DejaVu Sans fonts are embedded in every data-figure PDF; JetBrains Mono and Menlo are embedded in the Archify method PDF.
- The method PDF is fitted to the diagram bounds.
- The single-hue categorical palette passed `dataviz/scripts/validate_palette.js` in light mode with all-pairs checking.
- Direct PNG inspection covered the method figure and all eight data-figure variants. Figure-bearing pages in both compiled PDFs were also inspected. No truncated, clipped, or overlapping labels remain; zero references, numeric annotations, and budget tracks are complete.

## Execution export verification

`PAPER_READINESS_RESULT_V003_PAPER_MATERIALS_003` regenerates the derived tables with corrected recovery accounting. The export now follows `loop_state.json` history references, so steps completed before a recovery remain counted. For T11, development seed 29, replicate B, the diagnostic arm keeps its three executed probe steps and 36 candidate positions; the fourth registered call failed on the per-call output budget, and the frozen champion from the first decision enters the final comparison.

Aggregated tables now report 347 executed probe steps, 3,060 model-arm candidate positions against 3,456, and 1,104 random candidate positions against 1,152. The 37 retained failed or invalid records equal 384 scheduled decisions minus 347 executed steps. The earlier `PAPER_MATERIALS_002` export remains in place as the superseded derived version. The frozen result file and all run directories are unchanged.

The shared aggregation defect was fixed in `run_paper_readiness_v003.py` and `report_paper_readiness_v003.py`. A regression test checks that completed child-step records referenced from recovery history are counted and that failed entries are not.

## Literature verification

`scripts/fetch_arxiv_metadata.py` retrieved fourteen primary arXiv records into `evidence/arxiv_metadata.json`. `scripts/expand_literature.py` combined those records with the Crossref-verified FunSearch article and the four existing literature records, producing nineteen bibliography entries and nineteen source-bound `supported_claim` records. The expanded related-work section cites all nineteen entries across research-system scope, interface and feedback adaptation, research-engineering evaluation, execution evidence, and factor discovery. `assemble_manuscript.py` rejects citation mismatches, missing bibliography entries, missing literature evidence, and empty source URLs.

## Manuscript assembly

Commands:

```bash
python3 scripts/build_workspace.py --check
python3 scripts/assemble_manuscript.py --check
python3 figures/generate_figures.py --check
```

Result: `PASS`. All eighteen writing units have source-bound LaTeX drafts. The assembled manuscript contains sixteen content sections, a 207-word abstract, nineteen cited bibliography entries, five figures, and twenty-eight unique labels. Citation coverage, literature-evidence coverage, figure paths, duplicate-label checks, list-environment checks, and banned-prose checks passed. The four data-figure exports also passed deterministic value and file checks.

## Compilation and visual review

Commands:

```bash
latexmk -xelatex -cd main.tex
latexmk -xelatex -cd journal-preview.tex
python3 scripts/render_manuscript.py
```

Both manuscripts compiled successfully. `main.pdf` has eighteen A4 pages. `journal-preview.pdf` has nine pages at 210 × 280 mm. Neither log contains undefined references, undefined citations, rerun requests, or overfull boxes. Charis SIL, STIX Two Math, DejaVu Sans, JetBrains Mono, Menlo, and monospaced fonts are embedded.

Visual inspection covered both complete contact sheets, every publication-preview page, and each review page containing a figure or appendix table. Method flow, equations, all five figures, all five tables, section hierarchy, appendix paths, and the nineteen references are legible and complete. No clipped labels, line crossings, table overflow, missing figures, or broken reference display was found. Publication-preview captions use 7 bp type, references use 6.8 bp type, section headings have a distinct 8.6 bp level, and multi-line figure captions are justified. Double-column float pages use fixed 12 bp separation, so the two appendix tables stack continuously at the top of page nine. Rendered pages and contact sheets are stored under `rendered/`.

## Independent evidence review

A read-only audit compared every manuscript number, comparison direction, statistical claim, execution statement, caption, and citation against the evidence packet, claim map, literature records, and figure contracts. Its findings led to the following accepted revisions.

- The statistical method now states the actual estimator: the median across all 24 unit effects under complete task-cluster resampling and task-internal sign flips. The frozen implementation reproduces every reported p-value and interval, so the table values are unchanged.
- Execution reporting now separates scheduled decisions, executed probe steps, model calls, and retained failures. The manuscript no longer calls all calls valid.
- Recovery description now states that three probe steps were preserved, the retried fourth call failed on the output budget, and the first-decision champion entered the comparison.
- Behavior claims are limited to the scalar and random comparisons that were measured; memory-minus-fixed behavior was not evaluated.
- The output-token ceiling is stated for output tokens only.
- Coverage is described as unplotted when it is reported as zero.
- Appendix paths point to the corrected derived tables.
- Two unsupported sentences were removed: the V002 semantic-mismatch claim and the ARIS trace-prototype claim.

A second read-only round checked only these revisions against the same evidence and the corrected `PAPER_MATERIALS_003` tables. It confirmed that all first-round issues were resolved and found no remaining substantive error within the reviewed scope. The second round also independently reproduced the four exact p-values and enumerated all 46,656 possible six-task cluster samples, obtaining the four reported intervals.

## Writing-plan application

`auto_research/docs/WRITING_IMPROVEMENT_PLAN.md` now defines the core question, evidence-strength contract, section responsibilities, figure contracts, and acceptance requirements. `skills/current/auto-research-paper-writing/SKILL.md` and `references/STYLE_RULES.md` load and enforce the plan. The workspace `WRITING_PLAN.md` links to the same rules and retains the corrected recovery semantics.

The introduction now states the shared question behind the reference and component comparisons. Related work is organized by adaptation object, evidence available during adaptation, and final evaluation controls. The harness section connects the fixed process to the three information conditions. No research value, statistical definition, execution count, or evidence role changed.

The workspace contexts, assembled manuscript, statistical report, figure checks, and both PDFs were regenerated after these revisions. All eighteen contexts and 28 registered claims passed; the four frozen contrasts passed exact recomputation; all seven V003 regression tests passed; `skill-up validate` accepted all six writing-skill cases. The review PDF has eighteen pages and the publication preview has nine pages. Final log checks found no undefined references, undefined citations, rerun requests, or overfull boxes. Both contact sheets and the changed preview pages were inspected, including the page transition after related work.

A third read-only review was restricted to the three changed manuscript units and the new writing rules. Its findings were resolved by separating reference IC gains from component estimates and behavior effects, restoring citation wording to the registered literature support, identifying date-block concentration as an auxiliary component diagnostic, describing the random reference as bounded legal edits over the same editable surface, distinguishing requested proposals from successful outputs, and adding the fixed model, proposal interface, decision count, and candidate budget to the core question. The Harness² wording now matches the registered support, and the method caption describes visible diagram facts. The final text was checked against the registered evidence and adjacent units, then rebuilt and visually inspected with the results above.

## Manuscript provenance and authorization

The accepted text is controller-authored from the registered evidence. Local Cajal and humanizer candidates were reviewed and not adopted. Their candidate records remain separate from the accepted manuscript. The writing workflow did not execute research or read a raw market panel.

`manuscript_status.json` records the final content-review state and output locations. Author review is `NOT_RECORDED`, submission is not authorized, and watermark verification is `UNVERIFIED`.
