#!/usr/bin/env python3
"""MAP-Elites worker with optional mechanism-constrained LLM mutations."""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'auto_research'))
sys.path.insert(0, str(ROOT / 'auto_research/scripts'))
import expr_search as es  # noqa: E402
from agents import knowledge, llm, sanitize  # noqa: E402
from agents.expr_grammar import parse, validate  # noqa: E402

OUT = ROOT / 'auto_research/agents/experiments'
LIB = ROOT / 'auto_research/expr_library/top_expressions_seed1.json'


def depth(node: dict) -> int:
    if node['kind'] == 'leaf':
        return 1
    if node['kind'] == 'unary':
        return 1 + depth(node['arg'])
    return 1 + max(depth(node['left']), depth(node['right']))


def root_family(node: dict) -> str:
    return node['op'] if node['kind'] != 'leaf' else 'leaf'


def turnover_bucket(value: float | None) -> str:
    if value is None:
        return 'unknown'
    if value < 0.5:
        return 'low'
    if value < 0.8:
        return 'mid'
    return 'high'


def ast_features(node: dict) -> list[float]:
    counts = {op: 0 for op in ['rank', 'zscore', 'winsorize', 'vol_scaled', 'tanh', 'sign', 'residual_size',
                               'delta4', 'delta13', 'delta_w', 'ema5', 'lag5', 'neg', 'ratio', 'diff',
                               'rank_minus', 'product']}
    leaves = [0]
    def walk(n):
        if n['kind'] == 'leaf':
            leaves[0] += 1
        elif n['kind'] == 'unary':
            counts[n['op']] = counts.get(n['op'], 0) + 1
            walk(n['arg'])
        else:
            counts[n['op']] = counts.get(n['op'], 0) + 1
            walk(n['left']); walk(n['right'])
    walk(node)
    return [depth(node), leaves[0]] + [counts.get(op, 0) for op in counts]


def fit_ridge(rows: list[dict]):
    if len(rows) < 6:
        return None
    X = np.asarray([ast_features(row['node']) for row in rows], dtype=float)
    y = np.asarray([row['search_fitness'] for row in rows], dtype=float)
    X = (X - X.mean(0)) / (X.std(0) + 1e-9)
    X = np.column_stack([np.ones(len(X)), X])
    try:
        coef = np.linalg.lstsq(X, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    return (X.mean(0), X.std(0) + 1e-9, coef)


def predict_ridge(model, node: dict) -> float:
    if model is None:
        return 0.0
    mean, std, coef = model
    x = (np.asarray(ast_features(node), dtype=float) - mean[1:]) / std[1:]
    return float(coef[0] + x @ coef[1:])


def skeleton(node: dict) -> str:
    if node['kind'] == 'leaf':
        return 'L'
    if node['kind'] == 'unary':
        return f"U{node['op']}({skeleton(node['arg'])})"
    return f"B{node['op']}({skeleton(node['left'])},{skeleton(node['right'])})"


def cell_key(node: dict, result: dict) -> tuple:
    d = depth(node)
    depth_bucket = 'shallow' if d <= 2 else ('mid' if d <= 4 else 'deep')
    return (root_family(node), depth_bucket, turnover_bucket(result.get('turnover_proxy')))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True)
    parser.add_argument('--budget', type=int, default=24)
    parser.add_argument('--split-frac', type=float, default=0.7)
    parser.add_argument('--seed', type=int, default=7)
    parser.add_argument('--batch', type=int, default=4)
    parser.add_argument('--exclude-library-parents', action='store_true')
    parser.add_argument('--no-kg', action='store_true')
    parser.add_argument('--surrogate', action='store_true')
    parser.add_argument('--combination', action='store_true')
    parser.add_argument('--novelty', action='store_true')
    parser.add_argument('--tag', default='')
    parser.add_argument('--sage-prompt', action='store_true')
    args = parser.parse_args()
    panel = es.build_panel()
    features = es.features(panel)
    dates = es.pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(20, int(round(len(dates) * args.split_frac)))
    search_dates, holdout_dates = dates[:cut], dates[cut:]
    library = json.loads(LIB.read_text(encoding='utf-8'))
    parents = []
    for row in sorted(library, key=lambda item: -item.get('fitness', -9))[:3]:
        try:
            node = parse(row['formula'], set(features)); validate(node, set(features))
            parents.append({'formula': row['formula'], 'node': node})
        except Exception:  # noqa: BLE001
            continue
    archive: dict[tuple, dict] = {}
    evaluated = []
    skeleton_best: dict[str, float] = {}
    proposal_stats = {'proposed': 0, 'accepted': 0, 'rejected': 0}
    proposal_fitness = []
    library_formulas = {es.canonical(parent['node']) for parent in parents}
    def evaluate(node, source):
        result = es.evaluate_expr(node, panel, search_dates)
        if not result:
            return None
        formula = es.canonical(node)
        if source != 'library' and formula in library_formulas:
            return None
        sk = skeleton(node)
        if args.novelty and source != 'library':
            best = skeleton_best.get(sk)
            if best is not None and result['fitness'] <= best + 0.01:
                return None
        proposal_stats['accepted'] += 1
        if source == 'llm_mutation':
            proposal_fitness.append(result)
        row = {'formula': formula, 'node': node, 'search_fitness': result['fitness'],
               'mean_ic': result['mean_ic'], 'icir': result['icir'],
               'turnover_proxy': result['turnover_proxy'], 'source': source}
        evaluated.append(row)
        if source != 'library':
            skeleton_best[sk] = max(skeleton_best.get(sk, -9.0), result['fitness'])
        key = cell_key(node, result)
        if key not in archive or result['fitness'] > archive[key]['search_fitness']:
            archive[key] = row
        return row
    for parent in parents:
        evaluate(parent['node'], 'library')
    mapping = sanitize.alias_map(features) if llm.PROVIDER != 'local' else None
    kg = '' if args.no_kg else knowledge.compose('find factor expressions with stable IC')
    rng = random.Random(args.seed)
    while len(evaluated) < args.budget:
        elites = sorted(archive.values(), key=lambda row: -row['search_fitness'])
        if args.novelty:
            cell_counts = {}
            for row in evaluated:
                key = cell_key(row['node'], {'turnover_proxy': row.get('turnover_proxy')})
                cell_counts[key] = cell_counts.get(key, 0) + 1
            least = sorted(elites, key=lambda row: cell_counts.get(cell_key(row['node'], {'turnover_proxy': row.get('turnover_proxy')}), 0))
            parent = rng.choice(least[:max(1, min(5, len(least)))])
        else:
            parent = rng.choice(elites[:max(1, min(5, len(elites)))])
        need = min(args.batch, args.budget - len(evaluated))
        parent_text = sanitize.to_alias(parent['formula'], mapping) if mapping else parent['formula']
        kg_text = f'Known negative results (avoid):\n{kg[:1200]}\n' if kg else ''
        if args.sage_prompt:
            prompt = (f'You are a quantitative factor researcher. Parent expression:\n{parent_text}\n\n'
                      f'Keep the economic mechanism; change only the horizon/operator, or combine it with one related signal.\n'
                      f'Use only these operators: rank, zscore, winsorize, vol_scaled, tanh, sign, residual_size, delta4, delta13, delta_w, ema5, lag5, neg, ratio, diff, rank_minus, product.\n'
                      f'Propose exactly {need} valid expressions.\n'
                      f'Example output format (JSON array only, no markdown):\n'
                      f'[{{\"expression\": \"zscore(f01)\", \"rationale\": \"standardize the same raw feature\"}},\n'
                      f' {{\"expression\": \"product(f01, f02)\", \"rationale\": \"interact two related signals\"}}]\n'
                      f'{kg_text}'
                      f'Output ONLY the JSON array, with keys expression and rationale.')
        else:
            prompt = (f'You are a factor researcher. The parent expression is: {parent_text}\n'
                      f'Keep its economic mechanism; change only horizon/operator or combine with a related signal. '
                      f'Propose exactly {need} new expressions. Use the same grammar.\n'
                      f'{kg_text}'
                      f'Output ONLY a JSON array of objects with keys expression, rationale.')
        try:
            messages = [{'role': 'user', 'content': prompt}]
            response_format = None
            temperature = 0.4
            max_tokens = 8000
            if args.sage_prompt:
                messages = [{'role': 'system', 'content': 'You output only valid JSON; no prose, no markdown.'}] + messages
                temperature = 0.2
                max_tokens = 4096
                response_format = {'type': 'json_schema', 'json_schema': {
                    'name': 'proposals', 'strict': True,
                    'schema': {'type': 'array', 'items': {
                        'type': 'object',
                        'properties': {'expression': {'type': 'string'}, 'rationale': {'type': 'string'}},
                        'required': ['expression', 'rationale'], 'additionalProperties': False}}}}
            raw = llm.chat(messages, model=args.model, temperature=temperature,
                           max_tokens=max_tokens, response_format=response_format)
        except Exception:  # noqa: BLE001
            raw = ''
        items = []
        for value in llm.extract_all_json(raw):
            if isinstance(value, list):
                items.extend(value)
            elif isinstance(value, dict):
                inner = value.get('hypotheses') or value.get('proposals')
                items.extend(inner if isinstance(inner, list) else [value])
        added = 0
        if args.surrogate:
            model = fit_ridge(evaluated)
            scored = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                expression = str(item.get('expression', ''))
                if mapping:
                    expression = sanitize.from_alias(expression, mapping)
                try:
                    node = parse(expression, set(features)); validate(node, set(features))
                except Exception:  # noqa: BLE001
                    continue
                scored.append((predict_ridge(model, node), node))
            scored.sort(key=lambda pair: -pair[0])
            items = [{'expression': es.canonical(node)} for _, node in scored]
        for item in items:
            if not isinstance(item, dict) or added >= need:
                continue
            proposal_stats['proposed'] += 1
            expression = str(item.get('expression', ''))
            if mapping:
                expression = sanitize.from_alias(expression, mapping)
            try:
                node = parse(expression, set(features)); validate(node, set(features))
            except Exception:  # noqa: BLE001
                continue
            if evaluate(node, 'llm_mutation'):
                added += 1
        while added == 0 and len(evaluated) < args.budget:
            node = es.mutate(parent['node'], rng, features)
            if evaluate(node, 'fallback_mutation'):
                added += 1
    candidates = [row for row in evaluated if not (args.exclude_library_parents and row['source'] == 'library')]
    top = sorted(candidates, key=lambda row: -row['search_fitness'])[:5]
    holdout = []
    for row in top:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            holdout.append(result['fitness'])
    qd_top5 = float(np.mean(holdout)) if holdout else None
    # baselines at same budget
    random_rows = es.run_search('random', args.seed, args.budget, panel, features, search_dates)
    def top5(rows):
        vals = []
        for row in sorted(rows, key=lambda item: -item['fitness'])[:5]:
            node = es.expr_lookup(row['formula'])
            result = es.evaluate_expr(node, panel, holdout_dates) if node else None
            if result:
                vals.append(result['fitness'])
        return float(np.mean(vals)) if vals else None
    # same-prior random mutation baseline
    rng3 = random.Random(args.seed * 31 + 7)
    rand_mut_rows = []
    seen_mut = set()
    while len(rand_mut_rows) < args.budget:
        parent = rng3.choice(parents)
        node = es.mutate(parent['node'], rng3, features)
        formula = es.canonical(node)
        if formula in seen_mut or formula in library_formulas:
            continue
        seen_mut.add(formula)
        result = es.evaluate_expr(node, panel, search_dates)
        if result:
            rand_mut_rows.append({'formula': formula, 'node': node, 'fitness': result['fitness']})
    rm_holdout = []
    for row in sorted(rand_mut_rows, key=lambda item: -item['fitness'])[:5]:
        result = es.evaluate_expr(row['node'], panel, holdout_dates)
        if result:
            rm_holdout.append(result['fitness'])
    random_mutation_top5 = float(np.mean(rm_holdout)) if rm_holdout else None
    combo = None
    combo_random = None
    if args.combination and len(top) >= 2:
        def combo_fitness(rows):
            signals = []
            for row in rows:
                sig = es.eval_tree(row['node'], panel)
                if sig is None:
                    continue
                signals.append(es.EP._rank(sig, panel['date']))
            if len(signals) < 2:
                return None
            combined = sum(signals)
            series, _ = es.EP.ic_series(panel, combined, holdout_dates)
            if len(series) < 20:
                return None
            mean_ic = abs(float(series.mean()))
            icir = abs(float(series.mean() / series.std(ddof=1))) if series.std(ddof=1) > 0 else 0.0
            turnover = es.EP._turnover_proxy(panel, [{'values': combined}], holdout_dates)
            return round(mean_ic + 0.3 * icir - 0.1 * (turnover if np.isfinite(turnover) else 0.0), 6)
        combo = combo_fitness(top)
        random_combo = []
        for row in sorted(random_rows, key=lambda item: -item['fitness'])[:5]:
            node = es.expr_lookup(row['formula'])
            if node:
                random_combo.append({'node': node})
        combo_random = combo_fitness(random_combo)
    payload = {'schema': 'AUTO-RESEARCH-QD-1', 'created_at': datetime.now(timezone.utc).isoformat(),
               'provider': llm.PROVIDER, 'model': args.model, 'seed': args.seed, 'split_frac': args.split_frac,
               'budget': args.budget, 'n_evaluated': len(evaluated), 'n_cells': len(archive),
               'qd_top5_holdout_fitness': qd_top5, 'random_top5_holdout_fitness': top5(random_rows),
               'random_mutation_top5_holdout_fitness': random_mutation_top5,
               'exclude_library_parents': args.exclude_library_parents, 'no_kg': args.no_kg, 'surrogate': args.surrogate, 'n_candidates': len(candidates),
               'sage_prompt': args.sage_prompt,
               'llm_proposal_stats': {**proposal_stats, 'rejected': max(0, proposal_stats['proposed'] - proposal_stats['accepted'])},
               'llm_proposal_quality': {
                   'n': len(proposal_fitness),
                   'mean_fitness': round(float(sum(r['fitness'] for r in proposal_fitness) / len(proposal_fitness)), 6) if proposal_fitness else None,
                   'max_fitness': round(max((r['fitness'] for r in proposal_fitness), default=0.0), 6),
                   'pass_rate': round(sum(1 for r in proposal_fitness if r.get('passed')) / len(proposal_fitness), 4) if proposal_fitness else None,
               },
               'combination_holdout_fitness': combo, 'random_combination_holdout_fitness': combo_random,
               'delta_vs_random': None, 'cells': {str(k): v['formula'] for k, v in list(archive.items())[:20]},
               'score_window_read': False}
    payload['delta_vs_random'] = (qd_top5 - payload['random_top5_holdout_fitness']) if (qd_top5 is not None and payload['random_top5_holdout_fitness'] is not None) else None
    payload['delta_vs_random_mutation'] = (qd_top5 - random_mutation_top5) if (qd_top5 is not None and random_mutation_top5 is not None) else None
    payload['top_rows'] = [{'formula': r['formula'], 'source': r['source'], 'fitness': r['search_fitness']} for r in top]
    OUT.mkdir(parents=True, exist_ok=True)
    cond = 'nokg' if args.no_kg else 'kg'
    if args.surrogate:
        cond += '_surr'
    if args.combination:
        cond += '_comb'
    if args.novelty:
        cond += '_nov'
    tag = f'_{args.tag}' if args.tag else ''
    path = OUT / f'qd_{llm.PROVIDER}_{args.model.replace("/", "_")}_split{args.split_frac}_seed{args.seed}_b{args.budget}_{cond}{tag}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({key: payload[key] for key in ('model', 'seed', 'split_frac', 'budget', 'n_evaluated',
                                                    'n_cells', 'qd_top5_holdout_fitness',
                                                    'random_top5_holdout_fitness', 'delta_vs_random')}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
