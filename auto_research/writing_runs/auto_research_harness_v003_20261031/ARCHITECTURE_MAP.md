# Reused writing architecture

## Source and unit layer

The existing `skills/eswa-paper-writing` workflow is represented by `all_units.json`, `contexts/`, `evidence/`, and `claim_map.json`.

- A writing unit owns one rhetorical function and one scoped context.
- A context contains source records, permitted claims, terminology, adjacent-unit information, and the drafting task.
- A quantitative statement must resolve to a JSON pointer, a CSV row selector, or an exact document section.
- The unit registry keeps drafting, evidence review, and later export on the same manuscript structure.
- V001 and V002 remain exploratory inputs; V003 supplies the confirmatory development-panel estimates.

The Cajal manuscript supplies an architectural reference only. Its prose, claims, citations, drafts, and review records are not copied into this workspace.

## Evidence layer

`evidence/results_packet.json` is the compact drafting packet. It contains the frozen protocol, four contrasts, execution usage, recovery classification, exploratory-panel outcomes, eight paper-material tables, metric definitions, and evidence boundaries.

`evidence/sources.json` distinguishes complete results, the frozen protocol, derived CSV files, architecture documents, execution records, and literature leads. `claim_map.json` binds twenty-eight core statements to those sources. Related-work claims require original-source bibliographic verification; the historical survey serves as a lead inventory.

## Figure layer

The existing `skills/eswa-research-figures` workflow is represented by `figures/figure_contracts.json` and deterministic generators.

- Figure 1 uses an Archify workflow with orthogonal routes to show proposal, probe-only continuation, active-branch and frozen-champion roles, and final common-support comparison. Its canonical light SVG export is converted to the publication PDF and PNG.
- Figure 2 shows every paired-unit effect, the median, and the six-task cluster interval.
- Figure 3 uses small multiples for task heterogeneity on a shared scale.
- Figure 4 separates novelty and duplicate-rate panels because their scales differ.
- Figure 5 reports executed decisions and realized candidate positions with their recorded budgets.

Each data-figure contract records source fields, filters, transformations, plotted values, sample size, experimental unit, uncertainty meaning, exclusions, caption, and export checks. The source CSV files provide the table-view equivalent for every plotted value.

## Layout layer

`layout.json` binds the shared `writing_skills/style_parameters.json` to two output profiles.

- Review draft: A4, one column, 25 mm margins, 12 bp body text, and 18 bp leading.
- Publication preview: 210 × 280 mm, two columns, 6.32 mm column gap, and the observed ESWA column widths.
- Figures use vector PDF in the manuscript and 300 dpi PNG for visual inspection.
- Content review, author review, and watermark status remain separate decisions.

## Writing governance

`WRITING_WORKFLOW.md`, `WRITING_WORKFLOW_GOAL.md`, `writing_skills/COMMON.md`, `writing_skills/STYLE_PROFILE.md`, and `writing_skills/evolution/active_rules.json` govern later drafting and review. The planned sequence is source-bound drafting, local evidence checks, cross-unit consistency review, independent evidence review, targeted revision, and deterministic export.

The current workspace contains the complete source-bound manuscript, verified references, compiled outputs, and reproduction checks.
