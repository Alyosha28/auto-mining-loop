#!/usr/bin/env python3
"""Run one step of the v2 evolution loop (task-level recursion + reflection).

Pipeline: reference probe -> candidate edits (deterministic or supplied LLM proposals) -> matched
probes -> selective composition (kept/revised/dropped with evidence) -> optional
blind selection over k compositions (parallel) or chained steps (sequential) ->
optional reflection into the per-domain experience library -> append-only ledger.

The score window (2021-01-01..2023-12-31) is never loaded here; only the
development window and embargo are known to this script.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'skills/current/factor-evolution-harness2/scripts'))

from evo_harness import (EDIT_CATALOG, apply_edit, default_harness, editable_surface,  # noqa: E402
                         guardrail_snapshot, harness_hash, validate_proposal)
from evo_memory import ExperienceLibrary, Ledger, blind_select, load_cross_domain_lessons  # noqa: E402
from evo_probe import (CoverageStop, GuardrailError, check_guardrails, load_panel,  # noqa: E402
                       probe_round, probe_rounds, split_dates, split_dev_dates, synthetic_panel)
from trace_contract import validate_execution_trace  # noqa: E402

DEFAULT_PANEL = ROOT / 'data_processed/local_quote_library_2017_2025_v1'
PREFERRED = ['momentum_20', 'volatility_20', 'turnover_mean_20', 'reversal_5',
             'amihud_20', 'volume_ratio_5_20', 'momentum_60', 'volatility_60']


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')


def _pass_rate(metrics: dict) -> float:
    if 'pass_rate' in metrics:
        return float(metrics['pass_rate'])
    candidates = metrics.get('n_candidates', 0) or 0
    return float(metrics.get('pass_count', 0)) / candidates if candidates else 0.0


def _delta(reference: dict, candidate: dict, key: str) -> float:
    return float(candidate.get(key, 0.0)) - float(reference.get(key, 0.0))


def metrics_delta(reference: dict, candidate: dict) -> dict:
    return {
        'delta_pass_count': candidate['pass_count'] - reference['pass_count'],
        'delta_pass_rate': _pass_rate(candidate) - _pass_rate(reference),
        'delta_mean_abs_ic': _delta(reference, candidate, 'mean_abs_ic'),
        'delta_top_ic_mean': _delta(reference, candidate, 'top_ic_mean'),
        'delta_icir_median': _delta(reference, candidate, 'icir_median'),
        'delta_icir_p25': _delta(reference, candidate, 'icir_p25'),
        'delta_turnover_proxy': _delta(reference, candidate, 'turnover_proxy'),
        'delta_novelty': _delta(reference, candidate, 'novelty'),
        'delta_coverage': candidate['coverage'] - reference['coverage'],
        'delta_duplicate_rate': candidate['duplicate_rate'] - reference['duplicate_rate'],
    }


def support_reason(delta: dict) -> str | None:
    """M2 support rule: pass-rate first, then harder probes with a turnover guard."""
    if delta['delta_coverage'] < -1e-9:
        return None
    if delta['delta_pass_rate'] > 1e-9:
        return 'pass_rate'
    if delta['delta_pass_rate'] < -1e-9:
        return None
    if delta['delta_turnover_proxy'] > 0.02:
        return None
    if delta['delta_mean_abs_ic'] > 0.002:
        return 'mean_abs_ic'
    if delta['delta_icir_p25'] > 0.02 and delta['delta_mean_abs_ic'] >= -0.001:
        return 'icir_p25'
    if delta['delta_top_ic_mean'] > 0.003:
        return 'top_ic_mean'
    return None


def supported_delta(delta: dict) -> bool:
    return support_reason(delta) is not None


def composition_reject_reasons(delta: dict, policy: dict | None = None) -> list[str]:
    if not policy:
        return []
    reasons = []
    if ('min_delta_novelty' in policy
            and delta['delta_novelty'] < float(policy['min_delta_novelty'])):
        reasons.append('novelty_below_composition_guard')
    if ('max_delta_duplicate_rate' in policy
            and delta['delta_duplicate_rate'] > float(policy['max_delta_duplicate_rate'])):
        reasons.append('duplicate_rate_above_composition_guard')
    return reasons


def compose(reference: dict, probes: list[dict], edits: list[dict], max_edits: int = 3,
            policy: dict | None = None) -> dict:
    """Greedy selective composition from probe contrasts (no score-window evidence)."""
    decisions = []
    for edit, probe in zip(edits, probes):
        delta = metrics_delta(reference, probe['metrics'])
        reject_reasons = composition_reject_reasons(delta, policy)
        reason = support_reason(delta) if not reject_reasons else None
        verdict = 'kept' if reason else 'dropped'
        decisions.append({'edit': edit['id'], 'kind': edit['kind'], 'component': edit['component'],
                          'intent': edit['intent'], 'delta': delta, 'support_reason': reason,
                          'guard_reject_reasons': reject_reasons, 'verdict': verdict,
                          'evidence': {'base_pass': reference['pass_count'],
                                       'candidate_pass': probe['metrics']['pass_count'],
                                       'base_mean_abs_ic': reference['mean_abs_ic'],
                                       'candidate_mean_abs_ic': probe['metrics']['mean_abs_ic'],
                                       'coverage': probe['metrics']['coverage'],
                                       'base_common_support_sha256': reference.get('common_support_sha256'),
                                       'candidate_common_support_sha256': probe['metrics'].get('common_support_sha256'),
                                       'common_cells': probe['metrics'].get('common_cells'),
                                       'common_dates': probe['metrics'].get('common_dates')}})
    kept = [decision for decision in decisions if decision['verdict'] == 'kept']
    # round the continuous tie-breaker so numerically indistinguishable edits stay
    # in catalog order instead of being ranked by float noise
    kept.sort(key=lambda decision: (
        -round(decision['delta']['delta_pass_rate'], 3),
        -round(decision['delta']['delta_mean_abs_ic'], 4),
        -round(decision['delta']['delta_icir_p25'], 4),
        -round(decision['delta']['delta_top_ic_mean'], 4),
        round(decision['delta']['delta_turnover_proxy'], 4),
        -round(decision['delta']['delta_novelty'], 3)))
    selected, seen_components = [], set()
    for decision in kept:
        component = decision['component']
        family = ('skills.crossover' if component.startswith('skills.crossover')
                  or component == 'skills.pair_selection' else component)
        if family in seen_components:
            continue
        seen_components.add(family)
        selected.append(decision)
        if len(selected) >= max_edits:
            break
    return {'decisions': decisions, 'kept': selected, 'policy': policy,
            'dropped': [decision for decision in decisions if decision['verdict'] == 'dropped'],
            'keep_base': not selected}


def apply_kept(harness: dict, kept: list[dict], edits_by_id: dict) -> dict:
    composed = json.loads(json.dumps(harness))
    for decision in kept:
        edit = edits_by_id.get(decision['edit'])
        if edit is None:
            raise KeyError(f"unknown edit in composition: {decision['edit']}")
        composed = apply_edit(composed, edit)
    return composed


def library_load_lessons(harness: dict, domain: str, args) -> list[dict]:
    if not args.use_lessons:
        return []
    root = ROOT / harness['memory']['experience_dir']
    query = f'{domain} pass coverage duplicate turnover operators crossover'
    domains = ([name.strip() for name in args.lesson_domains.split(',') if name.strip()]
               if args.lesson_domains else [domain])
    if len(domains) == 1 and domains[0] == domain:
        return ExperienceLibrary(root, domain).load_lessons(
            query, top_k=int(harness['memory'].get('top_k_lessons', 3)))
    return load_cross_domain_lessons(root, domains, query,
                                     top_k=int(harness['memory'].get('top_k_lessons', 3)))


def rank_edits_by_structured_lessons(edits: list[dict], lessons: list[dict]) -> list[dict]:
    """Order edits by structured lesson matches (operator/target metric + confidence)."""
    weights = {'high': 2.0, 'medium': 1.0, 'low': 0.5}
    lesson_ops = []
    for lesson in lessons:
        payload = lesson.get('lesson') or {}
        operator = payload.get('operator')
        if operator:
            lesson_ops.append((operator, weights.get(lesson.get('confidence', 'low'), 0.5), payload))
    def score(edit: dict) -> float:
        value = str(edit.get('value', ''))
        component = str(edit.get('component', ''))
        total = 0.0
        for operator, weight, _payload in lesson_ops:
            if operator == value or (operator == 'crossover' and component == 'skills.crossover_pairs'):
                total += weight
        return total
    indexed = list(enumerate(edits))
    indexed.sort(key=lambda item: (-score(item[1]), item[0]))
    return [edit for _index, edit in indexed]


def rank_edits_by_lessons(edits: list[dict], lessons: list[dict]) -> list[dict]:
    """Deterministic lesson-guided proposal order (stable tie-break by catalog order)."""
    if not lessons:
        return edits
    lesson_text = ' '.join((entry.get('cue', '') + ' ' + entry.get('lesson', ''))
                           for entry in lessons).lower()

    def overlap(edit: dict) -> int:
        text = f"{edit.get('id', '')} {edit.get('component', '')} {edit.get('value', '')} " \
               f"{edit.get('intent', '')}".lower()
        tokens = {token.strip('_.,;:()') for token in text.split() if len(token) > 3}
        return sum(1 for token in tokens if token in lesson_text)
    indexed = list(enumerate(edits))
    indexed.sort(key=lambda item: (-overlap(item[1]), item[0]))
    return [edit for _index, edit in indexed]


def _prompt_text(domain: str, feedback: str, has_trace: bool = False,
                 research_question: str | None = None,
                 proposer_guidance: str | None = None) -> str:
    if feedback not in ('parent', 'diagnostic'):
        raise ValueError(f'unknown prompt feedback: {feedback}')
    evidence = (
        '`parent_metrics.json` has only aggregate reference-probe behavior. '
        '`parent_harness.json` describes the current harness and seed pool. '
        'No candidate-level diagnostics are provided in this arm.'
        if feedback == 'parent' else
        'In addition to the parent state, `diagnostics.json` provides candidate-level '
        'pass/fail reasons and operator/kind aggregates derived from the reference probe. '
        'Use them to target a concrete deficiency rather than merely adding operators.'
    )
    trace_evidence = (
        '\n`execution_trace.json` contains actual prior proposals, their probe outcomes, '
        'candidate rosters, costs and keep/drop decisions. Use this trajectory to choose '
        'the next harness intervention. A repeated inert edit needs a changed mechanism, '
        'not merely a new name. Held-out outcomes and raw market rows are withheld.\n'
        if has_trace else ''
    )
    question_block = (f'\n## Bounded research question\n{research_question}\n'
                      if research_question else '')
    guidance_block = (f'\n## Candidate proposer guidance\n{proposer_guidance}\n'
                      'This guidance cannot change the data boundary, evaluator, editable surface or output schema.\n'
                      if proposer_guidance else '')
    return f"""# Harness editor mission ({domain})
{question_block}{guidance_block}
You are editing the *factor-evolution harness* — the process used to mine factors (operator
library, probe set, crossover policy). You are NOT writing a factor expression and NOT judging
factor correctness. Only the development window (2017-01-01..2020-11-30) is visible to you;
the scoring window (2021-01-01..2023-12-31) does not exist in this task and must never be
referenced or guessed.

## Evidence
{evidence}
{trace_evidence}
The allowed components, values and their actual execution semantics are in
`editable_surface.json`. `experience_lessons.json` may be empty; optional lessons are not
automatically evidence for this run.

## Rules
1. Propose 2-5 single-edit candidates. Each candidate changes exactly one component.
2. Each candidate needs: id (unique), kind (INV = cross-domain improvement, DOM = domain
   knowledge), component, op, value, intent.
3. `intent` is a testable harness hypothesis: name the observed or suspected deficiency,
   the mechanism you expect to change, and the behavior you expect to improve. State
   uncertainty where the supplied evidence cannot distinguish explanations.
4. Only values in `editable_surface.json` are allowed. Frozen protocol/guardrails are
   outside your surface; any attempt is rejected. An operators.remove edit may contain
   a non-empty list because it is still one component-level budget-allocation change.
5. Do not invent metrics, market facts or scoring-window evidence. Unknown evidence is
   not permission to claim an improvement.
6. Output a single JSON array to `edits.json`, nothing else.

## Template
`edits_template.json` shows the JSON shape with placeholders. Replace every placeholder
with an allowed value and a run-specific hypothesis; do not copy the placeholder values.
"""


def _prompt_sha256(domain: str, feedback: str, has_trace: bool = False,
                   research_question: str | None = None,
                   proposer_guidance: str | None = None) -> str:
    import hashlib
    return hashlib.sha256(_prompt_text(
        domain, feedback, has_trace, research_question, proposer_guidance).encode()).hexdigest()


def _load_execution_trace(path: Path) -> dict:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    return validate_execution_trace(payload)


def _sha256_json(payload) -> str:
    import hashlib
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()


def _candidate_diagnostics(reference: dict) -> dict:
    import math
    from evo_probe import PASS_MIN_IC, PASS_MIN_ICIR
    rows, aggregates = [], {}
    for candidate in reference['probe']['candidates']:
        mean_ic, icir = candidate.get('mean_ic'), candidate.get('icir')
        reasons = []
        if mean_ic is None or not math.isfinite(float(mean_ic)):
            reasons.append('nan_ic')
        elif abs(float(mean_ic)) < PASS_MIN_IC:
            reasons.append('abs_mean_ic_below_threshold')
        if icir is None or not math.isfinite(float(icir)):
            reasons.append('nan_icir')
        elif abs(float(icir)) < PASS_MIN_ICIR:
            reasons.append('abs_icir_below_threshold')
        passed = bool(candidate.get('passed'))
        rows.append({'name': candidate['name'], 'kind': candidate.get('kind'),
                     'operator': candidate.get('operator'), 'parents': candidate.get('parents'),
                     'passed': passed, 'mean_ic': mean_ic, 'icir': icir,
                     'n_dates': candidate.get('n_dates'), 'failure_reasons': reasons,
                     'fit_pair_correlation': candidate.get('fit_pair_correlation'),
                     'fit_pair_strength': candidate.get('fit_pair_strength')})
        for group, value in (('operator', candidate.get('operator')), ('kind', candidate.get('kind'))):
            key = f'{group}:{value}'
            entry = aggregates.setdefault(key, {'n': 0, 'passed': 0, 'failure_reasons': {}})
            entry['n'] += 1
            entry['passed'] += int(passed)
            for reason in reasons:
                entry['failure_reasons'][reason] = entry['failure_reasons'].get(reason, 0) + 1
    return {'source': 'reference.probe.candidates; no select-split outcomes',
            'thresholds': {'abs_mean_ic': PASS_MIN_IC, 'abs_icir': PASS_MIN_ICIR},
            'candidates': rows, 'aggregates': aggregates}


def _validate_seed_features(features: list[str]) -> None:
    from evo_probe import META
    forbidden = sorted({name for name in features if name in META or name.startswith('label_')})
    if forbidden:
        raise GuardrailError(f'label/metadata columns cannot be seed features: {forbidden}')


def write_prompt_pack(output: Path, harness: dict, reference: dict, lessons: list[dict],
                      domain: str, feedback: str = 'diagnostic',
                      execution_trace: dict | None = None,
                      research_question: str | None = None,
                      proposer_guidance: str | None = None) -> None:
    generation = output / 'generation'
    generation.mkdir(parents=True, exist_ok=True)
    write_json(generation / 'parent_metrics.json', reference['probe']['metrics'])
    write_json(generation / 'parent_harness.json', {
        'operators': harness.get('operators'), 'seed_pool': harness.get('seed_pool'),
        'skills': harness.get('skills'), 'probe': harness.get('probe'),
        'protocol': harness.get('protocol'), 'guardrails': harness.get('guardrails')})
    write_json(generation / 'reference_metrics.json', reference['probe'])
    write_json(generation / 'editable_surface.json', editable_surface())
    write_json(generation / 'experience_lessons.json', lessons)
    if feedback == 'diagnostic':
        write_json(generation / 'diagnostics.json', _candidate_diagnostics(reference))
    if execution_trace is not None:
        write_json(generation / 'execution_trace.json', execution_trace)
    template = [{'id': 'edit_001', 'kind': 'INV', 'component': 'COMPONENT_FROM_EDITABLE_SURFACE',
                 'op': 'OPERATION_FROM_EDITABLE_SURFACE', 'value': 'VALUE_FROM_EDITABLE_SURFACE',
                 'intent': 'REPLACE_WITH_RUN_SPECIFIC_DEFICIENCY_MECHANISM_AND_FALSIFIABLE_EXPECTATION'}]
    write_json(generation / 'edits_template.json', template)
    prompt = _prompt_text(domain, feedback, has_trace=execution_trace is not None,
                          research_question=research_question, proposer_guidance=proposer_guidance)
    (generation / 'prompt.txt').write_text(prompt, encoding='utf-8')
    evidence = ['parent_metrics.json', 'parent_harness.json', 'editable_surface.json',
                'experience_lessons.json'] + (['diagnostics.json'] if feedback == 'diagnostic' else [])
    if execution_trace is not None:
        evidence.append('execution_trace.json')
    (generation / 'README_CN.md').write_text(
        '# LLM proposer 两阶段\n\n'
        f"1. 模型只读 `prompt.txt` 和以下证据文件：{', '.join(evidence)}；"
        '把候选编辑写到 `edits.json`；\n'
        '2. 运行 `run_evo_step.py --proposer llm --edits <run>/generation/edits.json`，'
        '脚本会校验每条编辑、probe、compose、反思并入账。\n', encoding='utf-8')


def load_llm_edits(path: Path) -> tuple[list[dict], dict]:
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    atomic = isinstance(payload, dict)
    if atomic:
        if (payload.get('schema') != 'AUTO-RESEARCH-ATOMIC-PROPOSAL-1'
                or set(payload) != {'schema', 'edits'}):
            raise ValueError('atomic proposal must contain only schema and edits')
        payload = payload['edits']
    if not isinstance(payload, list) or not 2 <= len(payload) <= 5:
        raise ValueError('edits file must be a JSON array containing 2-5 proposals')
    edits, ids = [], set()
    for item in payload:
        edit = validate_proposal(item)
        if edit['id'] in ids:
            raise ValueError(f"duplicate edit id: {edit['id']}")
        ids.add(edit['id'])
        edits.append(edit)
    if atomic:
        edits = [{'id': 'atomic_proposal', 'kind': 'DOM', 'component': 'atomic',
                  'op': 'compose', 'value': edits,
                  'intent': 'apply the validated proposal array as one final harness candidate'}]
    return edits, {'edits_file': str(path), 'edits_file_sha256': _sha256_file(path),
                   'n_edits': len(payload), 'proposal_semantics': 'atomic' if atomic else 'independent'}


def _root_relative(path: Path) -> str:
    """Repo-relative path without resolving symlinks (evo_rsif may live on Alba)."""
    absolute = Path(path).absolute()
    try:
        return str(absolute.relative_to(ROOT))
    except ValueError:
        return str(absolute)


def _sha256_file(path: Path) -> str:
    import hashlib
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _validate_prepared_resume(output: Path, args, execution_trace: dict | None,
                              proposer_guidance: str | None = None) -> dict:
    """Validate a prepared run using only its existing artifacts and CLI identity.

    This function is deliberately read-only. In particular it does not construct
    a Ledger, load a panel, create a cache, or probe before every identity field
    has matched, so a rejected resume cannot alter even one byte of the old run.
    """
    def read_object(relative: str):
        try:
            payload = json.loads((output / relative).read_text(encoding='utf-8'))
        except Exception as error:
            raise GuardrailError(f'prepared resume cannot read {relative}: {error}') from error
        if not isinstance(payload, dict):
            raise GuardrailError(f'prepared resume artifact must be an object: {relative}')
        return payload

    manifest = read_object('run_manifest.json')
    base = read_object('harness/base.json')
    split_record = read_object('dev_split.json')
    reference = read_object('probe/reference.json')
    try:
        reference_hash = _sha256_json({
            'probe': reference['probe']['candidates'],
            'select': reference['select']['candidates'] if reference.get('select') else None})
        split_mode = args.dev_split or base['protocol'].get('dev_split', 'legacy')
        if split_mode not in ('legacy', 'seeded-year-blocks'):
            raise ValueError(f'unsupported dev split: {split_mode}')
        seed_panel = Path(args.seed_panel).resolve() if args.seed_panel else DEFAULT_PANEL
        resume_domain = 'synthetic_demo' if args.demo and args.domain == 'price_volume' else args.domain
        if args.harness:
            requested_base = json.loads(Path(args.harness).read_text(encoding='utf-8'))
        else:
            seed_features = None if args.demo else [item['feature'] for item in base.get('seed_pool', [])]
            requested_base = default_harness(resume_domain, seed_features=seed_features)
        if args.experience_dir:
            requested_base['memory']['experience_dir'] = _root_relative(args.experience_dir)
        elif args.demo:
            requested_base['memory']['experience_dir'] = _root_relative(output / 'experience')
        requested_base['protocol']['dev_start'] = args.dev_start
        requested_base['protocol']['dev_end'] = args.dev_end
        requested_base['protocol']['dev_split'] = split_mode
        if args.budget:
            requested_base['protocol']['budget_candidates'] = int(args.budget)
        if args.demo:
            requested_base['seed_pool'] = [{'name': name, 'feature': name, 'transform': 'raw'}
                                           for name in PREFERRED[:6]]
        requested_base.setdefault('guardrails', {})['coverage_stop'] = not args.coverage_log_only
        expected = {
            'seed': int(args.seed),
            'base_harness_hash': harness_hash(requested_base),
            'budget_candidates': int(requested_base['protocol']['budget_candidates']),
            'dev_window': [args.dev_start, args.dev_end],
            'prompt_feedback': args.prompt_feedback,
            'prompt_sha256': _prompt_sha256(args.domain if not (args.demo and args.domain == 'price_volume')
                                            else 'synthetic_demo', args.prompt_feedback,
                                            execution_trace is not None, args.research_question,
                                            proposer_guidance),
            'prompt_guidance_file': (_root_relative(args.prompt_guidance_file)
                                     if args.prompt_guidance_file else None),
            'prompt_guidance_sha256': (_sha256_file(args.prompt_guidance_file)
                                       if args.prompt_guidance_file else None),
            'model_id': args.model_id,
            'coverage_log_only': bool(args.coverage_log_only),
            'reference_candidates_sha256': reference_hash,
            'dev_split': {
                'mode': split_mode,
                'seed': int(args.seed) if split_mode == 'seeded-year-blocks' else None,
                'block_size': 4 if split_mode == 'seeded-year-blocks' else None,
            },
            'trace_file': _root_relative(args.trace_file) if args.trace_file else None,
            'trace_file_sha256': _sha256_file(args.trace_file) if args.trace_file else None,
            'evaluate_only': bool(args.evaluate_only),
            'probe_only': bool(args.probe_only),
            'champion_harness': (_root_relative(args.champion_harness)
                                 if args.champion_harness else None),
            'champion_harness_hash': (harness_hash(json.loads(
                Path(args.champion_harness).read_text(encoding='utf-8')))
                                      if args.champion_harness else None),
            'research_question': args.research_question,
            'reflection_write': not args.disable_reflection_write,
            'domain': 'synthetic_demo' if args.demo and args.domain == 'price_volume' else args.domain,
            'seed_panel': (str(seed_panel.relative_to(ROOT)) if seed_panel != DEFAULT_PANEL else None),
        }
    except (KeyError, TypeError, ValueError) as error:
        raise GuardrailError(f'prepared resume artifacts are incomplete: {error}') from error
    identity_keys = (
        'seed', 'base_harness_hash', 'budget_candidates', 'dev_window',
        'prompt_feedback', 'prompt_sha256', 'prompt_guidance_file', 'prompt_guidance_sha256',
        'model_id', 'coverage_log_only',
        'reference_candidates_sha256', 'dev_split', 'trace_file',
        'trace_file_sha256', 'evaluate_only', 'probe_only', 'champion_harness',
        'champion_harness_hash', 'research_question', 'reflection_write',
        'domain', 'seed_panel',
    )
    mismatches = []
    for key in identity_keys:
        default = (True if key == 'reflection_write' else
                   False if key == 'probe_only' else None)
        if manifest.get(key, default) != expected.get(key):
            mismatches.append(key)
    if (manifest.get('data_source') == 'synthetic') != bool(args.demo):
        mismatches.append('data_source')
    if split_record.get('mode') != expected['dev_split']['mode']:
        mismatches.append('dev_split_artifact')
    if base['protocol'].get('dev_start') != args.dev_start or base['protocol'].get('dev_end') != args.dev_end:
        mismatches.append('base_dev_window')
    if int(base['protocol'].get('budget_candidates', -1)) != expected['budget_candidates']:
        mismatches.append('base_budget_candidates')
    if mismatches:
        raise GuardrailError('prepared resume identity mismatch: ' + ', '.join(dict.fromkeys(mismatches))
                             + '; use a new run directory')
    return {'manifest': manifest, 'harness': base, 'split_record': split_record,
            'reference': reference}


def run(args) -> int:
    run_id = args.run_id or f'evo_loop_v2_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")}'
    output = Path(args.output) if args.output else ROOT / f'evo_rsif/runs/{run_id}'
    if args.evaluate_only and (args.proposer != 'deterministic' or args.prepare or args.edits
                               or args.trace_file or args.k != 1):
        raise GuardrailError('evaluate-only requires fixed deterministic execution, k=1, and no edits/trace')
    if args.probe_only and (args.evaluate_only or args.compare_harness or args.k != 1
                            or args.mode != 'parallel'):
        raise GuardrailError('probe-only exploration requires LLM/deterministic execution with parallel k=1')
    if args.champion_harness and not args.probe_only:
        raise GuardrailError('a separate champion harness is only valid in probe-only exploration')
    if args.compare_harness and not args.evaluate_only:
        raise GuardrailError('compare-harness requires --evaluate-only')
    if args.trace_file and args.proposer != 'llm':
        raise GuardrailError('trace-driven proposals require --proposer llm')
    execution_trace = _load_execution_trace(args.trace_file) if args.trace_file else None
    proposer_guidance = None
    if args.prompt_guidance_file:
        if args.proposer != 'llm':
            raise GuardrailError('prompt guidance requires --proposer llm')
        proposer_guidance = Path(args.prompt_guidance_file).read_text(encoding='utf-8').strip()
        if not proposer_guidance or len(proposer_guidance) > 20000:
            raise GuardrailError('prompt guidance must contain 1-20000 characters')
    started = datetime.now(timezone.utc)
    seed = int(args.seed)
    domain = args.domain
    if args.demo and domain == 'price_volume':
        domain = 'synthetic_demo'  # keep demo lessons out of the real-domain library
    resume_manifest = None
    resume_state = None
    if (output / 'run_manifest.json').exists():
        try:
            existing = json.loads((output / 'run_manifest.json').read_text(encoding='utf-8'))
        except Exception:
            existing = {}
        if not (existing.get('status') == 'PREPARED' and args.proposer == 'llm' and args.edits):
            print(f'error: run manifest already exists (append-only runs): {output}', file=sys.stderr)
            return 3
        resume_manifest = existing
        resume_state = _validate_prepared_resume(output, args, execution_trace, proposer_guidance)
    output.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(output / 'ledger.jsonl', run_id)

    # ---------------------------------------------------------------- data + harness
    seed_panel = Path(args.seed_panel).resolve() if args.seed_panel else DEFAULT_PANEL
    if args.demo:
        import pandas as pd
        panel = synthetic_panel(seed=seed)
        panel = panel.loc[(panel['date'] >= pd.Timestamp(args.dev_start))
                          & (panel['date'] <= pd.Timestamp(args.dev_end))].copy()
        data_source = 'synthetic'
    else:
        import pyarrow.parquet as pq
        from evo_probe import _panel_files
        from evo_probe import META
        files = _panel_files(seed_panel)
        if not files:
            raise GuardrailError(f'no panel files under {seed_panel}')
        schema = set(pq.read_schema(files[0]).names)
        if resume_state is not None:
            seeds = [item['feature'] for item in resume_state['harness'].get('seed_pool', [])]
        elif args.features:
            seeds = [name.strip() for name in args.features.split(',') if name.strip() in schema]
        elif seed_panel == DEFAULT_PANEL:
            seeds = [name for name in PREFERRED if name in schema][:6]
        else:
            seeds = sorted(name for name in schema if name not in META and not name.startswith('label_'))[:6]
        _validate_seed_features(seeds)
        if len(seeds) < 2:
            print('error: fewer than 2 usable seed features in the seed panel', file=sys.stderr)
            return 2
        base = load_panel(DEFAULT_PANEL, ['research_mask', 'forward_return', 'log_market_cap'],
                          args.dev_start, args.dev_end)
        if seed_panel == DEFAULT_PANEL:
            panel = load_panel(DEFAULT_PANEL, ['research_mask', 'forward_return', 'log_market_cap'] + seeds,
                               args.dev_start, args.dev_end,
                               cache_path=output / 'cache' / f'dev_panel_{domain}.parquet')
        else:
            feature_frame = load_panel(seed_panel, seeds, args.dev_start, args.dev_end)
            panel = base.merge(feature_frame, on=['date', 'symbol'], how='left')
            (output / 'cache').mkdir(parents=True, exist_ok=True)
            panel.to_parquet(output / 'cache' / f'dev_panel_{domain}.parquet', index=False)
        data_source = f'{str(DEFAULT_PANEL.relative_to(ROOT))} + seeds from {str(seed_panel.relative_to(ROOT))}'
    detected_seeds = [name for name in seeds if name in panel.columns][:6] if not args.demo else None
    if resume_state is not None:
        harness = resume_state['harness']
        split_mode = harness['protocol'].get('dev_split', 'legacy')
    else:
        harness = default_harness(domain, seed_features=detected_seeds) \
            if not args.harness else json.loads(Path(args.harness).read_text(encoding='utf-8'))
        if args.experience_dir:
            harness['memory']['experience_dir'] = _root_relative(args.experience_dir)
        elif args.demo:
            harness['memory']['experience_dir'] = _root_relative(output / 'experience')
        harness['protocol']['dev_start'] = args.dev_start
        harness['protocol']['dev_end'] = args.dev_end
        split_mode = args.dev_split or harness['protocol'].get('dev_split', 'legacy')
        if split_mode not in ('legacy', 'seeded-year-blocks'):
            raise GuardrailError(f'unsupported dev split: {split_mode}')
        harness['protocol']['dev_split'] = split_mode
        if args.budget:
            harness['protocol']['budget_candidates'] = int(args.budget)
        if args.demo:
            harness['seed_pool'] = [{'name': name, 'feature': name, 'transform': 'raw'}
                                    for name in PREFERRED[:6] if name in panel.columns]
        harness.setdefault('guardrails', {})['coverage_stop'] = not args.coverage_log_only
    comparison_harness = None
    if args.compare_harness:
        comparison_harness = json.loads(Path(args.compare_harness).read_text(encoding='utf-8'))
        for key in ('dev_start', 'dev_end', 'dev_split', 'budget_candidates'):
            comparison_harness['protocol'][key] = harness['protocol'][key]
        comparison_harness.setdefault('guardrails', {})['coverage_stop'] = \
            harness['guardrails']['coverage_stop']
    champion_harness = None
    if args.champion_harness:
        champion_harness = json.loads(Path(args.champion_harness).read_text(encoding='utf-8'))
        if not isinstance(champion_harness, dict) or champion_harness.get('schema') != 'EVO-HARNESS-1':
            raise GuardrailError('champion must be an EVO-HARNESS-1 JSON file')
        for key in ('dev_start', 'dev_end', 'dev_split', 'budget_candidates'):
            if champion_harness['protocol'].get(key) != harness['protocol'].get(key):
                raise GuardrailError(f'champion protocol differs from active harness: {key}')
        champion_harness.setdefault('guardrails', {})['coverage_stop'] = \
            harness['guardrails']['coverage_stop']
    proposer = 'fixed' if args.evaluate_only else args.proposer
    manifest_base = {'run_id': run_id, 'schema': 'EVO-STEP-1', 'data_source': data_source,
                     'domain': domain, 'command': ' '.join([Path(sys.executable).name, 'run_evo_step.py'] + sys.argv[1:]),
                     'started_at': started.isoformat(), 'seed': seed, 'k': args.k, 'mode': args.mode,
                     'proposer': proposer,
                     'prompt_feedback': args.prompt_feedback if args.proposer == 'llm' else None,
                     'prompt_sha256': (_prompt_sha256(domain, args.prompt_feedback, execution_trace is not None,
                                                      args.research_question, proposer_guidance)
                                       if args.proposer == 'llm' else None),
                     'prompt_guidance_file': (_root_relative(args.prompt_guidance_file)
                                              if args.prompt_guidance_file else None),
                     'prompt_guidance_sha256': (_sha256_file(args.prompt_guidance_file)
                                                if args.prompt_guidance_file else None),
                     'model_id': (args.model_id if args.proposer == 'llm'
                                  else ('none' if args.evaluate_only else 'deterministic')),
                     'coverage_log_only': bool(args.coverage_log_only),
                     'reflection_write': not args.disable_reflection_write,
                     'seed_semantics': ('seeded_year_blocks_v1' if split_mode == 'seeded-year-blocks'
                                        else ('synthetic_panel' if args.demo else 'unused_by_current_probe')),
                     'panel_io': 'synthetic' if args.demo else 'typed_parquet_date_filters+scoped_cache_v2',
                     'evaluate_only': bool(args.evaluate_only),
                     'probe_only': bool(args.probe_only),
                     'champion_harness': (_root_relative(args.champion_harness)
                                          if args.champion_harness else None),
                     'champion_harness_hash': (harness_hash(champion_harness)
                                               if champion_harness is not None else None),
                     'research_question': args.research_question,
                     'trace_file': _root_relative(args.trace_file) if args.trace_file else None,
                     'trace_file_sha256': (_sha256_file(args.trace_file) if args.trace_file else None),
                     'preregistration': ({'path': _root_relative(args.preregistration),
                                          'sha256': _sha256_file(Path(args.preregistration).resolve())}
                                         if args.preregistration and Path(args.preregistration).is_file() else None),
                     'seed_panel': (str(seed_panel.relative_to(ROOT)) if seed_panel != DEFAULT_PANEL else None),
                     'lesson_domains': args.lesson_domains,
                     'max_proposals': args.max_proposals,
                     'budget_candidates': harness['protocol']['budget_candidates'],
                     'dev_window': [args.dev_start, args.dev_end],
                     'score_window_not_loaded': ['2021-01-01', '2023-12-31'],
                     'guardrails': guardrail_snapshot(harness),
                     'base_harness_hash': harness_hash(harness),
                     'base_harness': str((output / 'harness/base.json').relative_to(output)),
                     'compare_harness': (_root_relative(args.compare_harness)
                                         if args.compare_harness else None),
                     'compare_harness_hash': (harness_hash(comparison_harness)
                                              if comparison_harness else None)}
    write_json(output / 'harness/base.json', harness)
    if comparison_harness is not None:
        write_json(output / 'harness/compare.json', comparison_harness)
    ledger.append({'stage': 'INIT', 'base_harness_hash': harness_hash(harness),
                   'data_source': data_source, 'k': args.k, 'mode': args.mode})
    if split_mode == 'seeded-year-blocks':
        fit_dates, probe_dates, select_dates = split_dev_dates(panel, seed)
    else:
        probe_dates, select_dates = split_dates(panel, float(harness['protocol'].get('probe_split', 0.7)))
        fit_dates = probe_dates
    split_record = {'mode': split_mode, 'seed': seed if split_mode == 'seeded-year-blocks' else None,
                    'block_size': 4 if split_mode == 'seeded-year-blocks' else None,
                    'fit': [str(date.date()) for date in fit_dates],
                    'probe': [str(date.date()) for date in probe_dates],
                    'select': [str(date.date()) for date in select_dates]}
    write_json(output / 'dev_split.json', split_record)
    manifest_base['dev_split'] = {key: value for key, value in split_record.items()
                                  if key not in ('fit', 'probe', 'select')}
    print(f'data rows={len(panel)} fit_dates={len(fit_dates)} probe_dates={len(probe_dates)} '
          f'select_dates={len(select_dates)} seeds={[item["name"] for item in harness["seed_pool"]]}', flush=True)

    # ---------------------------------------------------------------- task-level recursion
    def probe(harness_config: dict, dates) -> dict:
        result = probe_round(harness_config, panel, fit_dates, dates, seed=seed)
        check_guardrails(harness_config, result)
        result['harness_hash'] = harness_hash(harness_config)
        return result

    def probe_pair(harness_config: dict) -> dict:
        return {'probe': probe(harness_config, probe_dates),
                'select': None if args.probe_only else probe(harness_config, select_dates)}

    def probe_group(harness_configs: list[dict], dates) -> list[dict]:
        results = probe_rounds(harness_configs, panel, fit_dates, dates, seed=seed)
        for harness_config, result in zip(harness_configs, results):
            check_guardrails(harness_config, result)
            result['harness_hash'] = harness_hash(harness_config)
        return results

    lessons_used = [] if args.evaluate_only else library_load_lessons(harness, domain, args)
    structured_used = []
    proposal_stage = None
    preparing = args.proposer == 'llm' and (args.prepare or not args.edits)
    if args.evaluate_only:
        edits = []
        proposal_record = {'mode': 'fixed_harness', 'n_edits': 0}
        proposal_stage = {'stage': 'FIXED_HARNESS', 'n_edits': 0}
    elif args.proposer == 'llm':
        if preparing:
            edits = []
            proposal_record = {'mode': 'prepared', 'n_edits': 0}
        else:
            edits, proposal_record = load_llm_edits(Path(args.edits))
            proposal_stage = {'stage': 'PROPOSE_LLM', **proposal_record}
    else:
        edits = [edit for edit in EDIT_CATALOG if edit['kind'] in ('INV', 'DOM')]
        if args.use_lessons and lessons_used:
            edits = rank_edits_by_lessons(edits, lessons_used)
        if args.structured_lessons and Path(args.structured_lessons).is_file():
            structured_used = json.loads(Path(args.structured_lessons).read_text(encoding='utf-8'))
            if isinstance(structured_used, list) and structured_used:
                edits = rank_edits_by_structured_lessons(edits, structured_used)
        if args.proposal_bias and Path(args.proposal_bias).is_file():
            bias = json.loads(Path(args.proposal_bias).read_text(encoding='utf-8'))
            order = {edit['id']: index for index, edit in enumerate(edits)}
            edits = sorted(edits, key=lambda edit: (-float(bias.get(edit['id'], 0.0)), order[edit['id']]))
        if args.max_proposals > 0:
            edits = edits[:args.max_proposals]

    if preparing:
        reference = probe_pair(harness)
        write_json(output / 'probe/reference.json', reference)
        manifest_base['reference_candidates_sha256'] = _sha256_json({
            'probe': reference['probe']['candidates'], 'select': reference['select']['candidates']})
        ledger.append({'stage': 'PROBE_REFERENCE', 'metrics': reference['probe']['metrics'],
                       'select_metrics': (reference['select']['metrics']
                                          if reference.get('select') else None)})
        write_prompt_pack(output, harness, reference, lessons_used, domain,
                          feedback=args.prompt_feedback, execution_trace=execution_trace,
                          research_question=args.research_question,
                          proposer_guidance=proposer_guidance)
        manifest = {**manifest_base, 'status': 'PREPARED',
                    'prompt_pack': str((output / 'generation/prompt.txt').relative_to(output)),
                    'finished_at': datetime.now(timezone.utc).isoformat()}
        write_json(output / 'run_manifest.json', manifest)
        ledger.append({'stage': 'PREPARE', 'prompt_pack': manifest['prompt_pack']})
        print(f"prepared prompt pack at {output / 'generation/prompt.txt'}\n"
              f"write your edits to {output / 'generation/edits.json'} and rerun with "
              f"--proposer llm --edits {output / 'generation/edits.json'}")
        return 0

    # Compose uses one intersection across the reference and every proposed harness.
    candidate_harnesses = ([comparison_harness] if comparison_harness is not None
                           else [apply_edit(harness, edit) for edit in edits])
    group_harnesses = [harness]
    include_champion = (args.probe_only and champion_harness is not None
                        and harness_hash(champion_harness) != harness_hash(harness))
    if include_champion:
        group_harnesses.append(champion_harness)
    group_harnesses.extend(candidate_harnesses)
    grouped = probe_group(group_harnesses, probe_dates)
    reference = {'probe': grouped[0]}
    champion_probe = grouped[1] if include_champion else reference['probe']
    candidate_offset = 2 if include_champion else 1
    probes = [] if comparison_harness is not None else grouped[candidate_offset:]
    ledger.append({'stage': 'PROBE_REFERENCE', 'metrics': reference['probe']['metrics'],
                   'select_metrics': None})
    if proposal_stage is not None:
        ledger.append(proposal_stage)
    print(f"reference pass={reference['probe']['metrics']['pass_count']} "
          f"cov={reference['probe']['metrics']['coverage']:.3f}", flush=True)
    edits_by_id = {edit['id']: edit for edit in edits}
    for edit, result in zip(edits, probes):
        result['edit'] = edit['id']
        write_json(output / 'probe/candidates' / f"{edit['id']}.json", result)
        ledger.append({'stage': 'PROBE_CANDIDATE', 'edit': edit['id'], 'kind': edit['kind'],
                       'metrics': result['metrics']})
        print(f"  probe {edit['id']}: pass={result['metrics']['pass_count']} "
              f"ic={result['metrics']['mean_abs_ic']:.4f} dup={result['metrics']['duplicate_rate']:.3f}", flush=True)
    composition = compose(reference['probe']['metrics'], probes, edits,
                          policy=harness.get('composition'))
    write_json(output / 'compose/decisions.json', composition)
    ledger.append({'stage': 'COMPOSE', 'decisions': composition['decisions'],
                   'keep_base': composition['keep_base']})

    # ---------------------------------------------------------------- k compositions
    compositions = []
    if args.probe_only:
        if not edits or len(probes) != len(edits):
            raise GuardrailError('probe-only active exploration needs executed single-edit candidates')
        best_index = max(range(len(probes)), key=lambda index: (
            probes[index]['metrics']['mean_abs_ic'], -index))
        adopted_harness = candidate_harnesses[best_index]
        adopted_probe = probes[best_index]
        active_edits = [edits[best_index]['id']]
        selection = {
            'selected_id': edits[best_index]['id'], 'selected_index': best_index,
            'rule': 'maximum probe mean_abs_ic among executed single-edit candidates; ties keep proposal order',
            'candidates': [{'edit': edit['id'], 'mean_abs_ic': probe['metrics']['mean_abs_ic']}
                           for edit, probe in zip(edits, probes)],
        }
        compositions = [{'id': edits[best_index]['id'], 'harness': adopted_harness,
                         'composition': composition}]
        write_json(output / 'harness/adopted.json', adopted_harness)
    elif args.mode == 'sequential' and args.k > 1:
        current = harness
        applied: list[str] = []
        for step_index in range(args.k):
            step_edits = [edit for edit in edits if edit['id'] not in applied]
            if step_index == 0:
                step_reference = reference['probe']
                step_composition = composition
            else:
                step_group = probe_group(
                    [current, *[apply_edit(current, edit) for edit in step_edits]], probe_dates)
                step_reference = step_group[0]
                step_composition = compose(step_reference['metrics'], step_group[1:], step_edits,
                                           policy=current.get('composition'))
            current = apply_kept(current, step_composition['kept'], edits_by_id)
            applied.extend(decision['edit'] for decision in step_composition['kept'])
            compositions.append({'id': f'sequential_{step_index}', 'harness': current,
                                 'reference_metrics': step_reference['metrics'],
                                 'composition': step_composition})
            ledger.append({'stage': 'SEQUENTIAL_STEP', 'step': step_index,
                           'kept': [decision['edit'] for decision in step_composition['kept']]})
        adopted_harness = current
        selection = {'selected_id': compositions[-1]['id'], 'rule': 'sequential last checkpoint',
                     'candidates': []}
        write_json(output / 'harness/adopted.json', adopted_harness)
    else:
        base_kept = composition['kept']
        variants = []
        if comparison_harness is not None:
            variants.append({'id': 'compare_harness', 'harness': comparison_harness,
                             'composition': {'decisions': [], 'kept': [], 'keep_base': False}})
        for index in range(max(1, args.k) if comparison_harness is None else 0):
            ordered = base_kept[index:] + base_kept[:index] if base_kept else []
            variants.append({'id': f'composition_{index}', 'harness': apply_kept(harness, ordered, edits_by_id),
                             'composition': {'decisions': composition['decisions'], 'kept': ordered,
                                             'keep_base': not ordered}})
        compositions = variants

    if args.probe_only:
        reference['select'] = None
        adopted_select = None
        select_metrics = None
        adopted_reference = {'probe': reference['probe'], 'select': None}
    else:
        # Blind selection and the final reference contrast share exact stock cells,
        # including every parallel variant or sequential checkpoint.
        select_group = probe_group([harness, *[item['harness'] for item in compositions]], select_dates)
        reference['select'] = select_group[0]
        for item, result in zip(compositions, select_group[1:]):
            item['select'] = result
        select_metrics = [item['select']['metrics'] for item in compositions]
        if args.mode == 'sequential' and args.k > 1:
            adopted_harness = compositions[-1]['harness']
            adopted_select = compositions[-1]['select']
        else:
            if args.k > 1:
                selection = blind_select(compositions, select_metrics)
            else:
                selection = {'selected_id': compositions[0]['id'], 'selected_index': 0,
                             'rule': 'k=1: no blind selection', 'candidates': []}
            adopted_harness = compositions[selection['selected_index']]['harness']
            adopted_select = compositions[selection['selected_index']]['select']
        write_json(output / 'harness/adopted.json', adopted_harness)
        final_probe_pair = probe_group([harness, adopted_harness], probe_dates)
        adopted_reference = {'probe': final_probe_pair[0], 'select': reference['select']}
        adopted_probe = final_probe_pair[1]
        active_edits = [decision['edit'] for decision in composition['kept']]
    write_json(output / 'probe/select_compositions.json', select_metrics)
    write_json(output / 'probe/reference.json', reference)
    manifest_base['reference_candidates_sha256'] = _sha256_json({
        'probe': reference['probe']['candidates'],
        'select': reference['select']['candidates'] if reference.get('select') else None})
    write_json(output / 'probe/adopted.json', {'probe': adopted_probe, 'select': adopted_select,
                                               'reference': adopted_reference})
    ledger.append({'stage': 'ADOPT', 'adopted_harness_hash': harness_hash(adopted_harness),
                   'kept': active_edits, 'selection': selection})

    # ---------------------------------------------------------------- reflection
    library = ExperienceLibrary(ROOT / harness['memory']['experience_dir'], domain)
    lessons_used = library_load_lessons(harness, domain, args)
    final_reference_metrics = adopted_reference['probe']['metrics']
    delta = metrics_delta(final_reference_metrics, adopted_probe['metrics'])
    coverage_ok = (adopted_probe['metrics']['coverage'] >= 0.96
                   or not harness['guardrails'].get('coverage_stop', True))
    if args.disable_reflection_write:
        reflection = {'status': 'NO_ENTRY', 'delta': delta,
                      'reason': 'disabled_by_outer_route; see outer route_history'}
    elif (supported_delta(delta) and coverage_ok
          and not composition_reject_reasons(delta, harness.get('composition'))):
        kept_ids = [decision['edit'] for decision in composition['kept']]
        improvement = '、'.join(kept_ids) if kept_ids else 'no_edit'
        entry = library.add(
            cue=f'domain={domain}; base_pass={final_reference_metrics["pass_count"]}/'
                f'{final_reference_metrics["n_candidates"]}; base_coverage='
                f'{final_reference_metrics["coverage"]:.2f}',
            lesson=(
                f'在开发窗固定预算下，加入 {improvement} 使通过数 '
                f'{final_reference_metrics["pass_count"]}→{adopted_probe["metrics"]["pass_count"]}'
                f'（Δ={delta["delta_pass_count"]:+d}），平均|IC| {final_reference_metrics["mean_abs_ic"]:.4f}'
                f'→{adopted_probe["metrics"]["mean_abs_ic"]:.4f}，top-quartile|IC| '
                f'{final_reference_metrics.get("top_ic_mean", float("nan")):.4f}→'
                f'{adopted_probe["metrics"].get("top_ic_mean", float("nan")):.4f}，turnover '
                f'{final_reference_metrics.get("turnover_proxy", float("nan")):.3f}→'
                f'{adopted_probe["metrics"].get("turnover_proxy", float("nan")):.3f}；保留这些编辑，其他编辑未获证据支持。'),
            evidence={'delta': delta, 'base_metrics': final_reference_metrics,
                      'adopted_metrics': adopted_probe['metrics'], 'kept_edits': kept_ids,
                      'probe_dates': [str(probe_dates[0].date()), str(probe_dates[-1].date())],
                      'select_dates': [str(select_dates[0].date()), str(select_dates[-1].date())]},
            confidence='high' if (delta['delta_pass_rate'] >= 0.05
                                  or delta['delta_mean_abs_ic'] >= 0.005) else 'medium',
            harness_hash=harness_hash(adopted_harness),
            source=('synthetic_demo' if args.demo else data_source))
        reflection = {'status': 'ENTRY_ADDED', 'entry_id': entry['id'], 'delta': delta}
    else:
        reflection = {'status': 'NO_ENTRY', 'delta': delta,
                      'reason': 'no supported improvement in the matched probe'}
    ledger.append({'stage': 'REFLECT', **reflection, 'lessons_used': [item['id'] for item in lessons_used]})

    # ---------------------------------------------------------------- artifacts + report
    step_record = {
        'schema': 'EVO-STEP-RECORD-1', 'run_id': run_id, 'domain': domain, 'mode': args.mode,
        'proposer': proposer,
        'preregistration': manifest_base['preregistration'],
        'proposal_order': [edit['id'] for edit in edits],
        'lessons_used': [item['id'] for item in lessons_used],
        'structured_lessons': ({'path': _root_relative(args.structured_lessons),
                                'sha256': _sha256_file(Path(args.structured_lessons).absolute()),
                                'ids': [item.get('id') for item in structured_used]}
                               if args.structured_lessons and Path(args.structured_lessons).is_file() else None),
        'proposal_bias': ({'path': _root_relative(args.proposal_bias),
                           'sha256': _sha256_file(Path(args.proposal_bias).resolve())}
                          if args.proposal_bias and Path(args.proposal_bias).is_file() else None),
        'prompt_feedback': manifest_base['prompt_feedback'],
        'model_id': manifest_base['model_id'],
        'coverage_log_only': bool(args.coverage_log_only),
        'seed_semantics': manifest_base['seed_semantics'],
        'dev_split': manifest_base['dev_split'],
        'evaluate_only': bool(args.evaluate_only),
        'probe_only': bool(args.probe_only),
        'champion_probe': champion_probe if args.probe_only else None,
        'active_branch': ({'rule': selection['rule'], 'edits': active_edits}
                          if args.probe_only else None),
        'trace_file': manifest_base['trace_file'],
        'trace_file_sha256': manifest_base['trace_file_sha256'],
        'reference_candidates_sha256': manifest_base['reference_candidates_sha256'],
        'llm_proposal': proposal_record if args.proposer == 'llm' else None,
        'fixed_harness': proposal_record if args.evaluate_only else None,
        'edits': edits,
        'k': args.k, 'seed': seed, 'base_harness': harness, 'adopted_harness': adopted_harness,
        'reference': reference, 'candidates': probes, 'composition': composition,
        'compositions': [{'id': item['id'], 'composition': item['composition'],
                          'reference_metrics': item.get('reference_metrics'),
                          'select_metrics': item.get('select', {}).get('metrics')}
                         for item in compositions],
        'selection': selection, 'adopted_reference': adopted_reference,
        'adopted_probe': adopted_probe, 'adopted_select': adopted_select,
        'reflection': reflection,
    }
    write_json(output / 'step_record.json', step_record)
    manifest = {**manifest_base,
                'finished_at': datetime.now(timezone.utc).isoformat(),
                'runtime_seconds': round((datetime.now(timezone.utc) - started).total_seconds(), 2),
                'adopted_harness_hash': harness_hash(adopted_harness),
                'kept_edits': active_edits,
                'reflection_status': reflection['status'],
                'probe_metrics': {'reference': final_reference_metrics,
                                  'adopted': adopted_probe['metrics']},
                'artifacts': [str(path.relative_to(output)) for path in sorted(output.rglob('*.json'))],
                'status': 'COMPLETED'}
    write_json(output / 'run_manifest.json', manifest)
    _write_report(output, step_record)
    ledger.append({'stage': 'DONE', 'status': 'COMPLETED'})
    print(json.dumps({'run_id': run_id, 'output': str(output),
                      'reference_pass': final_reference_metrics['pass_count'],
                      'adopted_pass': adopted_probe['metrics']['pass_count'],
                      'kept_edits': manifest['kept_edits'],
                      'reflection': reflection['status'],
                      'select_pass': (adopted_select['metrics']['pass_count']
                                      if adopted_select is not None else None)},
                     ensure_ascii=False, indent=1))
    return 0


def _write_report(output: Path, record: dict) -> None:
    lines = [f"# evo loop v2 — step record ({record['run_id']})", '',
             f"- domain: {record['domain']}; mode: {record['mode']}; k={record['k']}; seed={record['seed']}; "
             f"proposer={record.get('proposer', 'deterministic')}",
             f"- base harness: `{harness_hash(record['base_harness'])}`",
             f"- adopted harness: `{harness_hash(record['adopted_harness'])}`", '',
             '## Probe contrast（开发窗）', '',
             '| edit | kind | pass | Δpass | mean|IC| | ΔIC | top|IC| | ICIR p25 | turnover | dup | support | verdict |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|']
    reference = record['reference']['probe']['metrics']
    for probe, decision in zip(record['candidates'],
                               [item for item in record['composition']['decisions']]):
        metrics = probe['metrics']
        lines.append(f"| {probe.get('edit')} | {decision['kind']} | {metrics['pass_count']} | "
                     f"{metrics['pass_count'] - reference['pass_count']:+d} | "
                     f"{metrics['mean_abs_ic']:.4f} | {metrics['mean_abs_ic'] - reference['mean_abs_ic']:+.4f} | "
                     f"{metrics['top_ic_mean']:.4f} | {metrics['icir_p25']:.3f} | "
                     f"{metrics['turnover_proxy']:.3f} | {metrics['duplicate_rate']:.3f} | "
                     f"{decision.get('support_reason') or '-'} | {decision['verdict']} |")
    adopted_reference = record.get('adopted_reference', {}).get('probe', {}).get('metrics', reference)
    lines += ['', '## Adopted vs base', '',
              f"- pass: {adopted_reference['pass_count']} → {record['adopted_probe']['metrics']['pass_count']}",
              f"- mean|IC|: {adopted_reference['mean_abs_ic']:.4f} → {record['adopted_probe']['metrics']['mean_abs_ic']:.4f}",
              f"- coverage: {record['adopted_probe']['metrics']['coverage']:.3f}",
              f"- reflection: {record['reflection']['status']}", '',
              '## 说明', '',
              '- 本 step 只读取开发窗（2017-01-01..2020-11-30）；score window 未加载。',
              '- 覆盖率总是记录；coverage_stop 开启时低覆盖停止，关闭时仅诊断。失败不会静默记 0。',
              f"- 经验库：`{record['reflection'].get('entry_id', 'NO_ENTRY')}`"]
    (output / 'REPORT_CN.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--domain', default='price_volume')
    parser.add_argument('--demo', action='store_true', help='use the synthetic dev panel')
    parser.add_argument('--features', default=None, help='comma separated seed features (real data)')
    parser.add_argument('--seed-panel', type=Path, default=None,
                        help='panel directory providing the seed features (default: local_quote_library)')
    parser.add_argument('--lesson-domains', default=None,
                        help='comma separated domains to read experience lessons from (transfer experiments)')
    parser.add_argument('--structured-lessons', type=Path, default=None,
                        help='structured lesson index (cue metric/deficiency -> operator/target_metric/guard)')
    parser.add_argument('--proposal-bias', type=Path, default=None,
                        help='JSON mapping edit id -> bonus; deterministic proposer sorts by bonus (exploration)')
    parser.add_argument('--max-proposals', type=int, default=0,
                        help='deterministic proposer budget (0 = all catalog edits); lessons reorder proposals')
    parser.add_argument('--dev-start', default='2017-01-01')
    parser.add_argument('--dev-end', default='2020-11-30')
    parser.add_argument('--dev-split', choices=['legacy', 'seeded-year-blocks'], default=None,
                        help='seeded mode uses disjoint fit/probe/held-out dev date blocks')
    parser.add_argument('--budget', type=int, default=0)
    parser.add_argument('--k', type=int, default=1)
    parser.add_argument('--mode', choices=['parallel', 'sequential'], default='parallel')
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--harness', type=Path, default=None)
    parser.add_argument('--use-lessons', action='store_true')
    parser.add_argument('--proposer', choices=['deterministic', 'llm'], default='deterministic')
    parser.add_argument('--prompt-feedback', choices=['parent', 'diagnostic'], default='diagnostic',
                        help='evidence shown to an external LLM proposer')
    parser.add_argument('--model-id', default='unspecified-external-agent',
                        help='external model identity; this script never calls the model itself')
    parser.add_argument('--coverage-log-only', action='store_true',
                        help='record low coverage without stopping this exploratory run')
    parser.add_argument('--disable-reflection-write', action='store_true',
                        help='disable experience-library writes for isolated outer route comparisons')
    parser.add_argument('--edits', type=Path, default=None,
                        help='LLM-proposed edits JSON (phase 2 of --proposer llm)')
    parser.add_argument('--trace-file', type=Path, default=None,
                        help='sanitized prior proposal/probe trace for diagnostic feedback')
    parser.add_argument('--research-question', default=None,
                        help='optional bounded question included in the prompt and resume identity')
    parser.add_argument('--prompt-guidance-file', type=Path, default=None,
                        help='candidate proposer guidance text; content is pinned into resume identity')
    parser.add_argument('--preregistration', type=Path, default=None,
                        help='pre-registration file; sha256 is recorded in the run manifest')
    parser.add_argument('--prepare', action='store_true',
                        help='with --proposer llm: only write the prompt pack and exit')
    parser.add_argument('--evaluate-only', action='store_true',
                        help='evaluate the unchanged harness through the same pipeline; no model or edits')
    parser.add_argument('--probe-only', action='store_true',
                        help='run exploration without select evaluation; active branch uses the best probe candidate')
    parser.add_argument('--champion-harness', type=Path, default=None,
                        help='frozen probe champion included in the common-support group for probe-only exploration')
    parser.add_argument('--compare-harness', type=Path, default=None,
                        help='with --evaluate-only: compare this frozen harness against the base on shared cells')
    parser.add_argument('--experience-dir', type=Path, default=None,
                        help='override the experience library root (demo defaults to the run directory)')
    args = parser.parse_args()
    try:
        return run(args)
    except (CoverageStop, GuardrailError) as error:
        print(f'STOP: {error}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
