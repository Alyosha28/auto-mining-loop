#!/usr/bin/env python3
"""RESEARCH-ONLY one-shot OOS validation (2024-01-01..2025-09-19).

Reads the new time window exactly once, evaluates ONLY the frozen dev candidates
and frozen controls, never touches the existing factor library, and writes a claim
for review. No re-tuning or feedback after the read.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import expr_search as es  # noqa: E402

OOS = ROOT / 'auto_research/oos_2024'
FROZEN = OOS / 'FROZEN_CANDIDATES.json'
RESULT = OOS / 'oos_result.json'
START, END = '2024-01-01', '2025-09-19'


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_window_panel() -> tuple[pd.DataFrame, list[str]]:
    base_columns = es.panel_columns(es.BASE_PANEL)
    local = es.EP.load_panel(es.BASE_PANEL, base_columns, START, END)
    pv_columns = [name for name in es.panel_columns(es.PV_PANEL) if name.startswith('pv_')]
    pv_columns = sorted(pv_columns)[::4][:56]
    pv = es.EP.load_panel(es.PV_PANEL, pv_columns, START, END)
    merged = local.merge(pv, on=['date', 'symbol'], how='left')
    merged = merged.drop_duplicates(['date', 'symbol']).sort_values(['date', 'symbol']).reset_index(drop=True)
    return merged, pv_columns


def evaluate(panel: pd.DataFrame, values: pd.Series, dates) -> dict | None:
    values = pd.to_numeric(values, errors='coerce').replace([np.inf, -np.inf], np.nan)
    if values.notna().sum() < 2000:
        return None
    series, n_dates = es.EP.ic_series(panel, values, dates)
    if n_dates < 20 or len(series) < 20:
        return None
    mean_ic = float(series.mean())
    std = float(series.std(ddof=1))
    icir = mean_ic / std if std > 0 else float('nan')
    direction = 1.0 if mean_ic >= 0 else -1.0
    values = values * direction
    mean_ic = abs(mean_ic)
    icir_abs = abs(icir) if np.isfinite(icir) else float('nan')
    turnover = es.EP._turnover_proxy(panel, [{'values': values}], dates)
    fitness = mean_ic + 0.3 * (icir_abs if np.isfinite(icir_abs) else 0.0) - 0.1 * (turnover if np.isfinite(turnover) else 0.0)
    return {'mean_ic': round(mean_ic, 6), 'icir': round(icir_abs, 6) if np.isfinite(icir_abs) else None,
            'turnover_proxy': round(float(turnover), 6) if np.isfinite(turnover) else None,
            'fitness': round(fitness, 6), 'n_dates': int(n_dates),
            'coverage': round(n_dates / len(dates), 4)}


def main() -> None:
    frozen = json.loads(FROZEN.read_text(encoding='utf-8'))
    payload_hash = hashlib.sha256(json.dumps({k: v for k, v in frozen.items() if k != 'sha256'},
                                             sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if payload_hash != frozen.get('sha256'):
        raise SystemExit('frozen candidates hash mismatch; refusing to read new window')
    if (OOS / 'READ_LEDGER.json').exists():
        raise SystemExit('refusing to re-read: the new window has already been read once (one-shot protocol)')
    panel, pv_columns = load_window_panel()
    features = [name for name in panel.columns if name not in es.META_COLUMNS]
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    ranked = panel.groupby('date')[features].rank(pct=True).astype('float32').fillna(0.5)

    def combo_record(combo: list[str]) -> dict | None:
        available = [name for name in combo if name in features]
        if len(available) != len(combo):
            return None
        record = evaluate(panel, ranked[available].mean(axis=1), dates)
        return {'features': combo, **record} if record else None

    def single_record(feature: str) -> dict | None:
        if feature not in panel.columns:
            return None
        record = evaluate(panel, pd.to_numeric(panel[feature], errors='coerce'), dates)
        return {'feature': feature, **record} if record else None

    candidates = [row for row in (combo_record(item['features']) for item in frozen['candidates']) if row]
    random_combos = [row for row in (combo_record(item['features']) for item in frozen['random_combo_controls']) if row]
    singles = [row for row in (single_record(item['feature']) for item in frozen['single_feature_controls']) if row]

    def top_stats(rows: list[dict], key: str = 'fitness') -> dict:
        ordered = sorted(rows, key=lambda row: -row[key])
        top = ordered[:5]
        return {'n': len(rows), 'top5_mean_fitness': round(float(np.mean([row['fitness'] for row in top])), 6) if top else None,
                'top1_fitness': top[0]['fitness'] if top else None,
                'top5': [{'features': row.get('features', row.get('feature')), 'fitness': row['fitness'],
                          'mean_ic': row['mean_ic'], 'icir': row['icir'], 'turnover': row['turnover_proxy']} for row in top]}

    candidate_stats = top_stats(candidates)
    random_stats = top_stats(random_combos)
    single_stats = top_stats(singles)
    delta = round(candidate_stats['top5_mean_fitness'] - single_stats['top5_mean_fitness'], 6) if candidate_stats['top5_mean_fitness'] is not None and single_stats['top5_mean_fitness'] is not None else None
    verdict = 'supported' if (delta is not None and delta > 0.005) else 'not_supported'
    result = {'schema': 'AUTO-RESEARCH-OOS-RESULT-1', 'created_at': now(),
              'window': [START, END], 'read_count': 1, 'new_window_read': True,
              'research_only': True, 'library_mixed': False,
              'frozen_sha256': frozen['sha256'],
              'candidate_stats': candidate_stats, 'random_combo_stats': random_stats,
              'single_feature_stats': single_stats,
              'delta_candidates_minus_singles': delta, 'verdict': verdict,
              'rule': 'supported if frozen candidate top5 mean new-window fitness > frozen single-feature control top5 + 0.005',
              'score_window_read': False}
    OOS.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    (OOS / 'READ_LEDGER.json').write_text(json.dumps({'read_at': now(), 'window': [START, END],
                                                       'read_count': 1, 'purpose': 'research-only OOS validation',
                                                       'frozen_sha256': frozen['sha256']},
                                                      ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(json.dumps({'candidates': candidate_stats['top5_mean_fitness'],
                      'random_combos': random_stats['top5_mean_fitness'],
                      'singles': single_stats['top5_mean_fitness'],
                      'delta': delta, 'verdict': verdict}, ensure_ascii=False, indent=1))
    # claim
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import claim_ledger
    import review_claims
    spec = {'schema': 'AUTO-RESEARCH-CLAIM-SPEC-1', 'project': 'oos_2024',
            'claims': [{'claim_id': 'CLM-EXPR-OOS-2024-001', 'type': 'experimental_result',
                        'statement': (f"研究用一次性 OOS（{START}..{END}，仅读一次，未混入现有因子库）：冻结的 dev 组合因子 top5 mean fitness = "
                                      f"{candidate_stats['top5_mean_fitness']}，冻结单因子 control = {single_stats['top5_mean_fitness']}，"
                                      f"random combo control = {random_stats['top5_mean_fitness']}；Δ candidates−singles = {delta}；verdict={verdict}。"),
                        'evidence': ['auto_research/oos_2024/oos_result.json',
                                     'auto_research/oos_2024/PREREG_OOS_2024.json',
                                     'auto_research/oos_2024/FROZEN_CANDIDATES.json'],
                        'run_refs': ['auto_research/oos_2024'],
                        'preregistration': 'auto_research/oos_2024/PREREG_OOS_2024.json',
                        'score_window_read': False}]}
    path = OOS / 'claim_spec.json'
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    added = claim_ledger.add_claims(path)
    reviewed = review_claims.review_all(only={claim['claim_id'] for claim in added}) if added else []
    print(json.dumps({'claims': [claim['claim_id'] for claim in added],
                      'reviews': [row['decision'] for row in reviewed]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
