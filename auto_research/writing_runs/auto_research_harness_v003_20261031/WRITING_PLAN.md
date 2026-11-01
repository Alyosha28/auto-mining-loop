# Writing plan

## Research question

Under a fixed model, fixed proposal interface, fixed decision count, and bounded candidate evaluation budget, can an LLM improve a factor-research harness beyond random legal edits and a fixed initial harness, and do cumulative memory or execution diagnostics add a separable benefit?

## Manuscript argument

The central result is the controlled comparison of complete research processes. V003 supports a positive development-panel effect for the memory condition over random legal edits and the fixed initial harness. The component contrasts do not establish a separable improvement from cumulative memory or diagnostic information. Behavioral results accompany the IC contrasts: memory reduces novelty and increases duplication relative to both scalar and random conditions.

The introduction should motivate research-process adaptation and the need for execution-aware controls, then state the shared question behind the reference and component comparisons. The methods should make the active branch, frozen champion, probe-only exploration, atomic proposal semantics, and single final common-support comparison reproducible. The results should distinguish fixed-call-budget primary estimates from complete-execution sensitivity analyses. Reference gains, component nulls, behavioral costs, and execution losses remain visible throughout the argument.

Expression and evidence organization follow `auto_research/docs/WRITING_IMPROVEMENT_PLAN.md` and `skills/current/auto-research-paper-writing/references/STYLE_RULES.md`.

## Quantitative narrative

- Memory minus random: 24 paired units, median +0.0035336384, all six task medians positive, task-cluster 95% interval +0.0016230605 to +0.0051187463, exact task sign-flip p=0.03125.
- Memory minus fixed: 24 paired units, median +0.0109768722, all units positive, task-cluster 95% interval +0.0080797934 to +0.0154555391, exact task sign-flip p=0.03125.
- Memory minus scalar: median +0.0002895444, task-cluster 95% interval −0.0011740211 to +0.0048623041, Holm-adjusted p=1.0.
- Diagnostic minus memory: median 0.0, with 17 zero among 24 paired units, Holm-adjusted p=1.0.
- Memory minus scalar novelty median −0.125 and duplicate-rate median +0.0454545455.
- Memory minus random novelty median −0.0916666667 and duplicate-rate median +0.0378787879.
- Execution: 288 model calls, 7,550,828 input tokens, 1,200,323 output tokens, 347 executed probe steps, and 37 failed or invalid decision records retained.

The two non-component reference contrasts are reported with their exact task-cluster sign-flip p-values. Each sign assignment flips all four unit effects within a task and recomputes the median across all 24 signed effects. Bootstrap intervals likewise resample complete four-unit task clusters and summarize the 24-unit median. Holm correction applies to the two component contrasts, as frozen in the protocol. Six tasks provide limited task-level inferential resolution; interval estimates and observed heterogeneity remain central.

## Figures

- Figure 1: method pipeline and information boundaries.
- Figure 2: all 24 paired effects for four contrasts, with medians and task-cluster intervals.
- Figure 3: task-level medians in four small multiples.
- Figure 4: novelty and duplicate-rate behavior costs for three applicable contrasts.
- Figure 5: executed decisions and realized candidate positions by arm.

Every caption identifies the compared conditions, experimental unit, sample size, aggregation, interval meaning, and development-panel scope. Zero-valued contrasts and failed decisions remain visible in the underlying tables.

## Tables

- Main contrast summary, including median, mean, range, cluster interval, exact task test, and direction counts.
- Protocol and execution budget, including realized model calls, tokens, candidate positions, and recovery classification.
- Complete-execution sensitivity and replicate stability as auxiliary analyses.
- Date-block concentration as a diagnostic, using `mean_daily_abs_ic_delta` only within that analysis.

## Literature boundary

Related-work prose may use only entries whose bibliographic metadata and supported claims have been checked against an accessible source. `auto_research/docs/SURVEY_CN.md` is a lead inventory and historical synthesis; it is not sufficient bibliographic evidence by itself. No citation key or quotation may be invented. Literature claims stay scoped to the cited systems and do not imply that V003 reproduces those systems.

## Writing and review sequence

1. Draft each unit from its registered context and adjacent-unit contract.
2. Check every number and causal verb against `claim_map.json` before local candidate acceptance.
3. Review cross-unit terminology, endpoint definitions, task and replicate counting, and evidence labels.
4. Run independent evidence review for unsupported claims, omitted nulls, citation scope, and figure-caption fidelity.
5. Apply targeted revisions and repeat checks only for changed units.
6. Keep content approval, author approval, and any watermark status as separate decisions.
7. Export through the existing deterministic layout system after the manuscript content is approved.

## Scope statements required in the manuscript

- The evidence is confirmatory within a development panel, using tasks and splits inside 2017-01-01 through 2020-11-30.
- The study has no untouched independent market-period out-of-sample evaluation.
- Model replicates are independent API calls; provider-controlled model seeds are not claimed.
- Random edits share the atomic proposal schema and editable surface and make zero model calls.
- Execution recovery replays a persisted proposal or retries an identical prepared input within the registered model-call budget; completed pre-recovery steps remain attached through loop-state history.
- Component null results do not prove zero component effects under every task, budget, or harness.
