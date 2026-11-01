#!/usr/bin/env python3
"""Model-agnostic agentic research loop: propose -> critique -> evaluate -> Elo -> reflect -> evolve.

Smoke-test plumbing only until a capable local model is available; results are
recorded per model and must not be mixed across models.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from agents import knowledge, llm, models, roles  # noqa: E402

OUT = ROOT / 'auto_research/agents/runs'
PROGRESS = ROOT / 'auto_research/agents/runs/_progress'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def elo_update(rating_a: float, rating_b: float, score_a: float, k: float = 16.0) -> tuple[float, float]:
    expected_a = 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))
    new_a = rating_a + k * (score_a - expected_a)
    new_b = rating_b + k * ((1.0 - score_a) - (1.0 - expected_a))
    return new_a, new_b


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default=None)
    parser.add_argument('--generations', type=int, default=2)
    parser.add_argument('--per-generation', type=int, default=6)
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--tag', default='')
    parser.add_argument('--proposer', choices=['llm', 'mock'], default='llm',
                        help='mock = deterministic random expressions, for plumbing tests only')
    parser.add_argument('--no-critic', action='store_true')
    parser.add_argument('--no-reflection', action='store_true')
    parser.add_argument('--no-elo', action='store_true')
    args = parser.parse_args()
    model = args.model or llm.MODEL
    mock = args.proposer == 'mock'
    panel = es.build_panel()
    features = es.features(panel)
    all_dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(all_dates) * 0.7)))
    search_dates, holdout_dates = all_dates[:cut], all_dates[cut:]
    brief = ('Find factor expressions that improve dev-window cross-sectional rank IC / ICIR / turnover '
             'fitness beyond single-feature baselines. Combine signals economically; avoid re-testing known failures.')
    brief = brief + f'\n(Experiment seed: {args.seed}. Use it only to diversify mechanism choice.)'
    pool: list[dict] = []
    seen: set[str] = set()
    lessons = ''
    history = []
    evaluated = 0
    best_history: list[float] = []
    stagnation_events: list[dict] = []
    PROGRESS.mkdir(parents=True, exist_ok=True)
    progress_path = PROGRESS / f'{args.tag or "run"}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}.jsonl'

    def log_progress(payload: dict) -> None:
        with progress_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'ts': now(), **payload}, ensure_ascii=False) + '\n')

    generation = 0
    while evaluated < args.budget and generation < args.generations:
        n = min(args.per_generation, args.budget - evaluated)
        feedback = ''
        if pool:
            top = sorted(pool, key=lambda row: -row['search_fitness'])[:2]
            feedback = ('\n\nPrevious generation results (use them, do not repeat exact formulas):\n' +
                        '\n'.join(f"- {row['formula']}: search fitness {row['search_fitness']:.4f}, "
                                  f"fitness {row['search_fitness']:.4f}" for row in top))
            if lessons:
                feedback += f'\n\nReflections from previous generation:\n{lessons}'
        if mock:
            import random as _random
            rng = _random.Random(args.seed * 1000 + generation)
            proposal = {'proposals': []}
            for _ in range(n):
                expr = es.random_expr(rng, features)
                proposal['proposals'].append({'expression': es.canonical(expr), 'status': 'PARSED', 'node': expr,
                                              'rationale': 'mock', 'falsification': 'mock'})
        else:
            proposal = roles.propose(brief + feedback, features, n=n, model=model)
        parsed = [row for row in proposal['proposals'] if row.get('status') == 'PARSED']
        critique = {'decisions': []} if (mock or args.no_critic) else (roles.critique(brief, parsed, model=model) if parsed else {'decisions': []})
        decisions = {row.get('index'): str(row.get('decision', '')).lower() for row in critique.get('decisions') or []}
        for index, row in enumerate(parsed):
            formula = es.canonical(row['node'])
            if formula in seen:
                continue
            seen.add(formula)
            search = es.evaluate_expr(row['node'], panel, search_dates)
            if search is None:
                continue
            candidate = {'generation': generation, 'formula': formula, 'node': row['node'],
                         'rationale': row.get('rationale'), 'critic': decisions.get(index, 'unknown'),
                         'search_fitness': search['fitness'], 'search_ic': search['mean_ic'],
                         'search_icir': search['icir'], 'elo': 1000.0}
            # Elo against current elites (pairwise on search fitness)
            for elite in ([] if args.no_elo else sorted(pool, key=lambda item: -item['elo'])[:3]):
                score = 1.0 if search['fitness'] > elite['search_fitness'] else (0.5 if search['fitness'] == elite['search_fitness'] else 0.0)
                candidate['elo'], elite['elo'] = elo_update(candidate['elo'], elite['elo'], score)
            pool.append(candidate)
            evaluated += 1
        generation_best = max([row['search_fitness'] for row in pool if row['generation'] == generation], default=None)
        if generation_best is not None:
            stagnated = bool(best_history and generation_best <= max(best_history) + 1e-9)
            best_history.append(max(best_history + [generation_best]) if best_history else generation_best)
            if stagnated:
                stagnation_events.append({'generation': generation, 'best_fitness': generation_best,
                                          'action': 'redirect_prompt' if not mock else 'noop_mock'})
                lessons = (lessons + '\nSTAGNATION WARNING: no fitness improvement. Propose structurally '
                                     'different mechanisms (e.g., cross-sectional spread, downside tail, '
                                     'liquidity interaction) instead of small variants.') if not mock else lessons
        log_progress({'generation': generation, 'evaluated': evaluated, 'pool': len(pool),
                      'best_search': max([row['search_fitness'] for row in pool], default=None),
                      'llm_stats': llm.get_stats()})
        history.append({'generation': generation, 'n_proposed': len(proposal['proposals']),
                        'n_parsed': len(parsed), 'n_accepted': sum(1 for d in decisions.values() if d.startswith('accept')),
                        'n_evaluated': sum(1 for row in pool if row['generation'] == generation)})
        if evaluated < args.budget and generation < args.generations - 1 and not (mock or args.no_reflection):
            lesson_prompt = (f'Experiment seed: {args.seed}. Generation {generation}. Given these generation results, write 3 short lessons (one line each) about which '
                             'mechanisms worked or failed, to guide the next generation. Output plain text only.\n\n' +
                             '\n'.join(f"- {row['formula']} fitness={row['search_fitness']:.4f} critic={row['critic']}"
                                       for row in sorted(pool, key=lambda item: -item['search_fitness'])[:6]))
            try:
                lessons = llm.chat([{'role': 'user', 'content': lesson_prompt}], model=model, temperature=0.2,
                                   max_tokens=1500)
            except Exception as exc:  # noqa: BLE001
                lessons = f'(reflection failed: {exc})'
        generation += 1
    eligible = [row for row in pool if (mock or args.no_critic or str(row.get('critic', '')).startswith('accept'))] or pool
    for candidate in sorted(eligible, key=lambda row: -row['search_fitness'])[:8]:
        holdout = es.evaluate_expr(candidate['node'], panel, holdout_dates)
        candidate['holdout_fitness'] = holdout['fitness'] if holdout else None
        candidate['holdout_ic'] = holdout['mean_ic'] if holdout else None
    run_dir = OUT / f'agentic_{args.tag or "run"}_{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")}'
    run_dir.mkdir(parents=True, exist_ok=True)
    top_search = max([row['search_fitness'] for row in eligible], default=None)
    top_holdout = max([row.get('holdout_fitness') for row in eligible if row.get('holdout_fitness') is not None], default=None)
    metric_fixation_gap = round(top_search - top_holdout, 6) if (top_search is not None and top_holdout is not None) else None
    summary = {'run': str(run_dir.relative_to(ROOT)), 'model': 'mock' if mock else model,
               'provider': 'mock' if mock else llm.PROVIDER, 'created_at': now(),
               'llm_stats': llm.get_stats(), 'stagnation_events': stagnation_events,
               'metric_fixation_gap': metric_fixation_gap,
               'generations': args.generations, 'budget': args.budget, 'evaluated': evaluated,
               'n_valid': len(pool), 'lessons': lessons, 'history': history,
               'top_by_search': [{key: row.get(key) for key in ('formula', 'search_fitness', 'holdout_fitness',
                                                               'holdout_ic', 'elo', 'critic')}
                                 for row in sorted(eligible, key=lambda item: -item['search_fitness'])[:8]],
               'top_by_elo': [{key: row.get(key) for key in ('formula', 'search_fitness', 'holdout_fitness', 'elo')}
                              for row in sorted(eligible, key=lambda item: -item['elo'])[:8]],
               'proposer': args.proposer, 'ablations': {'no_critic': args.no_critic,
                                                          'no_reflection': args.no_reflection,
                                                          'no_elo': args.no_elo},
               'note': 'plumbing/plumbing-test only; never use for model comparison claims'}
    (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (run_dir / 'candidates.json').write_text(json.dumps(pool, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ('run', 'model', 'evaluated', 'n_valid', 'history')},
                     ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
