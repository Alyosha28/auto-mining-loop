#!/usr/bin/env python3
"""Deterministic matched probes for the v2 evolution harness (M2).

A probe is one bounded evolution round executed under a given harness config:
generate candidates from the seed pool using the harness operator library,
evaluate them on the development window, and return comparable behavior metrics.

M2 additions:
* fixed candidate budget: reserve crossover slots, round-robin mutations and
  evaluate exactly ``protocol.budget_candidates`` unique candidates; insufficient
  capacity fails instead of padding duplicates and confounding pass counts;
* harder/continuous probes: top-quartile |IC|, ICIR p25, turnover proxy
  (top-fraction membership churn), year stability, novelty and duplicate rate;
* more operators in the editable library (tanh / ema5 / lag5 / sign).

Guardrails: only development dates are ever read; the score window is never
loaded. Fail-closed coverage stop and non-zero runtime failures are reported to
the caller.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

PREFERRED_SEEDS = [
    "momentum_20", "volatility_20", "turnover_mean_20", "reversal_5",
    "amihud_20", "volume_ratio_5_20", "momentum_60", "volatility_60",
]
META = {"date", "symbol", "research_mask", "forward_return", "log_market_cap", "universe_reason",
        "label_reason", "label_entry_date", "label_exit_date", "status_source_date", "observed_listing_age"}
MIN_IC_PAIRS = 30
PASS_MIN_IC = 0.005
PASS_MIN_ICIR = 0.10
DUP_CORR = 0.9
NOVELTY_CORR = 0.7
TURNOVER_TOPK = 0.2


class GuardrailError(RuntimeError):
    pass


class CoverageStop(GuardrailError):
    pass


# --------------------------------------------------------------------------- data

def _panel_files(panel_dir: Path) -> list[Path]:
    for pattern in ("parts/part-*.parquet", "parts/*.parquet", "part-*.parquet",
                    "features/part-*.parquet", "features/*.parquet", "features.parquet"):
        files = sorted(panel_dir.glob(pattern))
        if files:
            return files
    return []


def _authorized_window(start: str, end: str, allowed_window: tuple[str, str]) -> tuple[str, str]:
    start = pd.Timestamp(start).date().isoformat()
    end = pd.Timestamp(end).date().isoformat()
    allowed_start, allowed_end = (pd.Timestamp(value).date().isoformat() for value in allowed_window)
    if start > end or start < allowed_start or end > allowed_end:
        raise ValueError(f'panel window [{start}, {end}] exceeds authorized range [{allowed_start}, {allowed_end}]')
    return start, end


def _cache_path(path: Path, panel_dir: Path, columns: list[str], start: str, end: str,
                allowed_window: tuple[str, str]) -> Path:
    identity = json.dumps({'safe_io_version': 2, 'panel': str(Path(panel_dir).resolve()),
                           'columns': columns, 'start': start, 'end': end,
                           'allowed_window': list(allowed_window)}, sort_keys=True)
    suffix = hashlib.sha256(identity.encode()).hexdigest()[:20]
    return path.with_name(f'{path.stem}_{suffix}{path.suffix}')


def _read_panel_part(path: Path, columns: list[str], start: str, end: str) -> pd.DataFrame | None:
    import datetime as dt
    import pyarrow as pa
    import pyarrow.parquet as pq
    schema = pq.read_schema(path)
    available = set(schema.names)
    wanted = [column for column in columns if column in available]
    if not {'date', 'symbol'}.issubset(wanted):
        return None
    date_type = schema.field('date').type
    end_exclusive = (pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat()
    if pa.types.is_timestamp(date_type):
        if date_type.tz is not None:
            raise TypeError(f'timezone-aware panel dates require an explicit protocol: {path}')
        lower = pd.Timestamp(start).to_pydatetime()
        upper = pd.Timestamp(end_exclusive).to_pydatetime()
    elif pa.types.is_date32(date_type) or pa.types.is_date64(date_type):
        lower, upper = dt.date.fromisoformat(start), dt.date.fromisoformat(end_exclusive)
    elif pa.types.is_string(date_type) or pa.types.is_large_string(date_type):
        lower, upper = start, end_exclusive
    else:
        raise TypeError(f'unsupported panel date type {date_type}: {path}')
    filters = [('date', '>=', pa.scalar(lower, type=date_type)),
               ('date', '<', pa.scalar(upper, type=date_type))]
    frame = pq.read_table(path, columns=wanted, filters=filters).to_pandas()
    frame['date'] = pd.to_datetime(frame['date'])
    return frame.loc[(frame['date'] >= pd.Timestamp(start))
                     & (frame['date'] < pd.Timestamp(end_exclusive))]


def load_panel(panel_dir: Path, columns: list[str], start: str, end: str,
               cache_path: Path | None = None,
               allowed_window: tuple[str, str] = ('2017-01-01', '2020-11-30')) -> pd.DataFrame:
    start, end = _authorized_window(start, end, allowed_window)
    columns = list(dict.fromkeys(['date', 'symbol', *columns]))
    if cache_path:
        cache_path = _cache_path(Path(cache_path), Path(panel_dir), columns, start, end, allowed_window)
        if cache_path.is_file():
            return pd.read_parquet(cache_path)
    files = _panel_files(Path(panel_dir))
    if not files:
        raise FileNotFoundError(f'no panel files under {panel_dir}')
    frames = []
    for path in files:
        frame = _read_panel_part(path, columns, start, end)
        if frame is not None and not frame.empty:
            frames.append(frame)
    if not frames:
        raise FileNotFoundError(f'no rows in [{start}, {end}] under {panel_dir}')
    panel = pd.concat(frames, ignore_index=True)
    panel['symbol'] = panel['symbol'].astype(str)
    panel = panel.drop_duplicates(['date', 'symbol']).sort_values(['date', 'symbol']).reset_index(drop=True)
    for name in columns:
        if name not in panel.columns:
            panel[name] = np.nan
    panel = panel[columns]
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        panel.to_parquet(cache_path, index=False)
    return panel


def synthetic_panel(seed: int = 0, dates: int = 120, symbols: int = 80) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates_index = pd.date_range('2017-01-03', periods=dates, freq='B')
    symbols_list = [f'S{index:03d}' for index in range(symbols)]
    rows = []
    for date in dates_index:
        frame = pd.DataFrame({'symbol': symbols_list})
        frame['date'] = date
        rows.append(frame)
    panel = pd.concat(rows, ignore_index=True)
    panel['log_market_cap'] = rng.normal(23, 1.0, len(panel))
    for name in PREFERRED_SEEDS[:6]:
        panel[name] = rng.normal(0, 1, len(panel))
    signal = 0.6 * panel['momentum_20'] + 0.4 * panel['volatility_20'] - 0.3 * panel['turnover_mean_20']
    panel['forward_return'] = 0.0015 * signal + rng.normal(0, 0.01, len(panel))
    panel['research_mask'] = rng.random(len(panel)) < 0.9
    panel['forward_return'] = panel['forward_return'].where(panel['research_mask'])
    return panel


# --------------------------------------------------------------------- operators

def _rank(series: pd.Series, dates: pd.Series) -> pd.Series:
    return series.groupby(dates).rank(pct=True)


def _zscore(series: pd.Series, dates: pd.Series) -> pd.Series:
    grouped = series.groupby(dates)
    mean = grouped.transform('mean')
    std = grouped.transform('std').replace(0, np.nan)
    return (series - mean) / std


def _winsorize(series: pd.Series, dates: pd.Series) -> pd.Series:
    grouped = series.groupby(dates)
    low = grouped.transform(lambda values: values.quantile(0.01))
    high = grouped.transform(lambda values: values.quantile(0.99))
    return series.clip(lower=low, upper=high)


def _mad_scale(series: pd.Series, dates: pd.Series) -> pd.Series:
    grouped = series.groupby(dates)
    center = grouped.transform('median')
    dev = (series - center).abs().groupby(dates).transform('median').replace(0, np.nan)
    return (series - center) / (1.4826 * dev)


def _tanh(series: pd.Series, dates: pd.Series) -> pd.Series:
    return np.tanh(_zscore(series, dates))


def _sign(series: pd.Series, dates: pd.Series) -> pd.Series:
    return np.sign(series)


def _delta(panel: pd.DataFrame, value: pd.Series, weeks: int) -> pd.Series:
    frame = pd.DataFrame({'date': panel['date'].values, 'symbol': panel['symbol'].values,
                          'value': value.values}, index=value.index)
    frame = frame.sort_values(['symbol', 'date'])
    shifted = frame.groupby('symbol')['value'].shift(weeks)
    return (frame['value'] - shifted).reindex(value.index)


def _ema(panel: pd.DataFrame, value: pd.Series, span: int) -> pd.Series:
    frame = pd.DataFrame({'date': panel['date'].values, 'symbol': panel['symbol'].values,
                          'value': value.values}, index=value.index).sort_values(['symbol', 'date'])
    smoothed = frame.groupby('symbol')['value'].transform(lambda values: values.ewm(span=span).mean())
    return smoothed.reindex(value.index)


def _lag(panel: pd.DataFrame, value: pd.Series, periods: int) -> pd.Series:
    frame = pd.DataFrame({'date': panel['date'].values, 'symbol': panel['symbol'].values,
                          'value': value.values}, index=value.index).sort_values(['symbol', 'date'])
    return frame.groupby('symbol')['value'].shift(periods).reindex(value.index)


def apply_operator(panel: pd.DataFrame, value: pd.Series, operator: str) -> pd.Series | None:
    dates = panel['date']
    if operator == 'neg':
        return -value
    if operator == 'rank':
        return _rank(value, dates)
    if operator == 'zscore':
        return _zscore(value, dates)
    if operator == 'winsorize':
        return _winsorize(value, dates)
    if operator == 'vol_scaled':
        return _mad_scale(value, dates)
    if operator == 'tanh':
        return _tanh(value, dates)
    if operator == 'sign':
        return _sign(value, dates)
    if operator == 'residual_size':
        size = panel['log_market_cap'].astype(float)
        bucket = size.groupby(dates).transform(
            lambda values: pd.qcut(values.rank(method='first'), 5, labels=False, duplicates='drop')
            if values.notna().sum() >= 5 else pd.Series(0, index=values.index))
        frame = pd.DataFrame({'date': dates.values, 'bucket': bucket.values, 'value': value.values})
        means = frame.groupby(['date', 'bucket'])['value'].transform('mean')
        return pd.Series(frame['value'].values - means.values, index=value.index)
    if operator == 'delta4':
        return _delta(panel, value, 4)
    if operator == 'delta13':
        return _delta(panel, value, 13)
    if operator == 'delta_w':
        return _delta(panel, value, 5)
    if operator == 'ema5':
        return _ema(panel, value, 5)
    if operator == 'lag5':
        return _lag(panel, value, 5)
    return None


AVAILABLE_OPERATORS = ['neg', 'rank', 'zscore', 'winsorize', 'vol_scaled', 'tanh', 'sign',
                       'residual_size', 'delta4', 'delta13', 'delta_w', 'ema5', 'lag5']


# ------------------------------------------------------------------ probe metrics

def _support_sha256(panel: pd.DataFrame, mask: pd.Series) -> str:
    """Hash exact date/symbol support identities, independently of row order."""
    keys = panel.loc[mask, ['date', 'symbol']].copy()
    keys['date'] = pd.to_datetime(keys['date']).dt.strftime('%Y-%m-%dT%H:%M:%S')
    keys['symbol'] = keys['symbol'].astype(str)
    keys = keys.sort_values(['date', 'symbol'], kind='mergesort')
    digest = hashlib.sha256(b'EVO-COMMON-DATE-SYMBOL-SUPPORT-1\0')
    for date, symbol in keys.itertuples(index=False, name=None):
        digest.update(json.dumps([date, symbol], ensure_ascii=False,
                                 separators=(',', ':')).encode('utf-8'))
        digest.update(b'\n')
    return digest.hexdigest()


def common_support(panel: pd.DataFrame, values: list[pd.Series],
                   dates: pd.DatetimeIndex) -> tuple[pd.Series, dict]:
    """Return the exact eligible date/stock cells shared by every value series.

    Harness panels do not necessarily carry the richer label-timing fields used
    by ``research.evaluation_contract``.  Eligibility here is therefore limited
    to fields this harness actually owns: explicit research mask and a present
    forward return.  No label_reason or other unavailable metadata is required.
    """
    selected = panel['date'].isin(dates)
    eligible = (selected & panel['research_mask'].fillna(False).astype(bool)
                & panel['forward_return'].notna())
    common = eligible.copy()
    for value in values:
        if not isinstance(value, pd.Series) or not value.index.equals(panel.index):
            raise GuardrailError('common support requires panel-aligned value series')
        numeric = pd.to_numeric(value, errors='coerce')
        common &= numeric.notna() & np.isfinite(numeric)
    diagnostics = {
        'cells': int(common.sum()),
        'dates': int(panel.loc[common, 'date'].nunique()),
        'eligible_cells': int(eligible.sum()),
        'support_sha256': _support_sha256(panel, common),
        'n_value_series': len(values),
        'key': 'date+symbol',
    }
    return common, diagnostics


def ic_series(panel: pd.DataFrame, values: pd.Series, dates: pd.DatetimeIndex,
              min_pairs: int = MIN_IC_PAIRS,
              support_mask: pd.Series | None = None) -> tuple[pd.Series, int]:
    """Vectorised per-date rank correlation on an optional fixed common support."""
    selected = panel['date'].isin(dates)
    if support_mask is not None:
        if not isinstance(support_mask, pd.Series) or not support_mask.index.equals(panel.index):
            raise GuardrailError('IC support mask must be aligned to the panel index')
        selected &= support_mask.fillna(False).astype(bool)
    frame = panel.loc[selected, ['date', 'symbol', 'forward_return', 'research_mask']].copy()
    frame['value'] = values.loc[frame.index].astype(float)
    frame = frame.loc[frame['research_mask'].fillna(False).astype(bool) & frame['forward_return'].notna()
                     & frame['value'].notna()]
    if frame.empty:
        return pd.Series(dtype=float), 0
    frame['value_rank'] = frame.groupby('date')['value'].rank()
    frame['fwd_rank'] = frame.groupby('date')['forward_return'].rank()
    grouped = frame.groupby('date')
    counts = grouped.size()
    sum_x = grouped['value_rank'].sum()
    sum_y = grouped['fwd_rank'].sum()
    sum_x2 = grouped['value_rank'].apply(lambda values: float((values ** 2).sum()))
    sum_y2 = grouped['fwd_rank'].apply(lambda values: float((values ** 2).sum()))
    sum_xy = grouped.apply(lambda group: float((group['value_rank'] * group['fwd_rank']).sum()))
    variance_x = sum_x2 - sum_x ** 2 / counts
    variance_y = sum_y2 - sum_y ** 2 / counts
    covariance = sum_xy - sum_x * sum_y / counts
    denominator = np.sqrt(np.maximum(variance_x, 0) * np.maximum(variance_y, 0))
    corr = (covariance / denominator.replace(0, np.nan)).dropna()
    corr = corr.loc[counts.reindex(corr.index) >= min_pairs]
    return corr.sort_index(), len(corr)


def _seed_series(panel: pd.DataFrame, seed: dict, dates: pd.DatetimeIndex) -> pd.Series:
    feature = seed['feature']
    if feature not in panel.columns:
        raise KeyError(f'seed feature missing from panel: {feature}')
    value = pd.to_numeric(panel[feature], errors='coerce')
    transform = seed.get('transform', 'raw')
    if transform == 'neg':
        value = -value
    elif transform == 'rank':
        value = _rank(value, panel['date'])
    return value


def _crossover(panel: pd.DataFrame, values_a: pd.Series, values_b: pd.Series,
               rule: str = 'legacy_rank_minus', direction_a: float = 1.0,
               direction_b: float = 1.0) -> pd.Series:
    rank_a = _rank(values_a, panel['date'])
    rank_b = _rank(values_b, panel['date'])
    if rule == 'legacy_rank_minus':
        return rank_a - rank_b
    if rule == 'signed_rank_sum':
        return direction_a * rank_a + direction_b * rank_b
    raise GuardrailError(f'unsupported crossover rule: {rule}')


def generate_candidates(harness: dict, panel: pd.DataFrame, fit_dates: pd.DatetimeIndex,
                        budget: int) -> list[dict]:
    """Allocate crossover slots first, then round-robin mutations and raw-seed padding."""
    budget = max(0, int(budget))
    seeds = harness.get('seed_pool') or []
    operators = [operator for operator in harness.get('operators', []) if operator != 'neg']
    skills = harness.get('skills', {})
    pairs = min(budget, max(0, int(skills.get('crossover_pairs', 0) or 0)))
    mutation_budget = budget - pairs
    mutation_limit = max(1, int(skills.get('mutation_per_seed', 1) or 1))
    candidates: list[dict] = []
    seed_stats = {}
    seed_values = {}
    for seed in seeds:
        seed_values[seed['name']] = _seed_series(panel, seed, fit_dates)
    seed_support, _ = common_support(panel, list(seed_values.values()), fit_dates)
    for seed in seeds:
        values = seed_values[seed['name']]
        series, n_dates = ic_series(panel, values, fit_dates, support_mask=seed_support)
        mean_ic = float(series.mean()) if len(series) else float('nan')
        seed_stats[seed['name']] = {'values': values, 'ic_series': series,
                                    'mean_ic': mean_ic, 'n_dates': n_dates}
    mutation_counts = {seed['name']: 0 for seed in seeds}
    for _depth in range(mutation_limit):
        before = len(candidates)
        for seed in seeds:
            if len(candidates) >= mutation_budget:
                break
            stats = seed_stats[seed['name']]
            direction = -1.0 if np.isfinite(stats['mean_ic']) and stats['mean_ic'] < 0 else 1.0
            for operator in operators:
                if any(item['name'] == f"{seed['name']}__{operator}" for item in candidates):
                    continue
                value = apply_operator(panel, stats['values'], operator)
                if value is None or value.notna().sum() < 100:
                    continue
                candidates.append({'name': f"{seed['name']}__{operator}", 'kind': 'mutation',
                                   'parents': [seed['name']], 'operator': operator,
                                   'values': value * direction})
                mutation_counts[seed['name']] += 1
                break
        if len(candidates) >= mutation_budget or len(candidates) == before:
            break
    if pairs > 0 and len(seeds) >= 2:
        rule = skills.get('crossover_rule', 'legacy_rank_minus')
        pair_selection = skills.get('pair_selection', 'strength_then_corr')
        if pair_selection not in ('strength_then_corr', 'diversified'):
            raise GuardrailError(f'unsupported pair selection: {pair_selection}')
        strong = sorted((name for name in seed_stats if np.isfinite(seed_stats[name]['mean_ic'])),
                        key=lambda name: -abs(seed_stats[name]['mean_ic']))
        pair_specs = []
        for index, name_a in enumerate(strong[:4]):
            for name_b in strong[index + 1:4]:
                joined = pd.concat([seed_stats[name_a]['ic_series'].rename('a'),
                                    seed_stats[name_b]['ic_series'].rename('b')], axis=1).dropna()
                if len(joined) < 50:
                    continue
                corr = float(np.abs(np.corrcoef(joined['a'], joined['b'])[0, 1]))
                if np.isfinite(corr):
                    strength = abs(seed_stats[name_a]['mean_ic']) + abs(seed_stats[name_b]['mean_ic'])
                    pair_specs.append({'strength': strength, 'correlation': corr,
                                       'a': name_a, 'b': name_b})
        def pair_key(pair: dict) -> tuple:
            if pair_selection == 'diversified':
                score = pair['strength'] * max(0.0, 1.0 - min(1.0, pair['correlation']))
                return (-score, pair['correlation'], pair['a'], pair['b'])
            return (-pair['strength'], pair['correlation'], pair['a'], pair['b'])
        for pair in sorted(pair_specs, key=pair_key)[:pairs]:
            name_a, name_b = pair['a'], pair['b']
            if rule == 'signed_rank_sum':
                direction_a = -1.0 if seed_stats[name_a]['mean_ic'] < 0 else 1.0
                direction_b = -1.0 if seed_stats[name_b]['mean_ic'] < 0 else 1.0
                value = _crossover(panel, seed_stats[name_a]['values'], seed_stats[name_b]['values'],
                                   rule=rule, direction_a=direction_a, direction_b=direction_b)
                name = f'{name_a}__signed_sum__{name_b}'
            else:
                value = _crossover(panel, seed_stats[name_a]['values'], seed_stats[name_b]['values'])
                if seed_stats[name_a]['mean_ic'] < 0:
                    value = -value
                name = f'{name_a}__minus__{name_b}'
            candidates.append({'name': name, 'kind': 'crossover', 'parents': [name_a, name_b],
                               'operator': rule, 'values': value,
                               'fit_pair_correlation': pair['correlation'],
                               'fit_pair_strength': pair['strength']})
    if len(candidates) < budget:
        for seed in seeds:
            if len(candidates) >= budget:
                break
            stats = seed_stats[seed['name']]
            direction = -1.0 if np.isfinite(stats['mean_ic']) and stats['mean_ic'] < 0 else 1.0
            candidates.append({'name': f"{seed['name']}__raw", 'kind': 'seed',
                               'parents': [seed['name']], 'operator': 'raw',
                               'values': stats['values'] * direction})
    if len(candidates) != budget:
        raise GuardrailError(f'candidate budget {budget} exceeds {len(candidates)} unique candidates '
                             'under the current seed/operator/crossover limits')
    return candidates


def _duplicate_rate(candidates: list[dict], min_overlap: int = 100,
                    corr_threshold: float = DUP_CORR) -> float:
    series = [(candidate['ic_series'], candidate['name']) for candidate in candidates
              if len(candidate.get('ic_series', [])) >= min_overlap]
    if len(series) < 2:
        return 0.0
    hits = total = 0
    for left in range(len(series)):
        for right in range(left + 1, len(series)):
            joined = pd.concat([series[left][0].rename('a'), series[right][0].rename('b')], axis=1).dropna()
            if len(joined) < min_overlap:
                continue
            corr = np.corrcoef(joined['a'], joined['b'])[0, 1]
            total += 1
            if np.isfinite(corr) and abs(corr) > corr_threshold:
                hits += 1
    return hits / total if total else 0.0


def _novelty(panel: pd.DataFrame, candidates: list[dict], fit_dates: pd.DatetimeIndex,
             support_mask: pd.Series | None = None, seed_pool: list[dict] | None = None) -> float:
    sample_dates = list(fit_dates[::max(1, len(fit_dates) // 6)])[:6]
    if not sample_dates or not candidates:
        return float('nan')
    seed_columns: dict = {}
    selected = panel['date'].isin(sample_dates)
    if support_mask is not None:
        if not isinstance(support_mask, pd.Series) or not support_mask.index.equals(panel.index):
            raise GuardrailError('novelty support mask must be aligned to the panel index')
        selected &= support_mask.fillna(False).astype(bool)
    frame = panel.loc[selected, ['date', 'symbol']].copy()
    parent_seeds = panel.attrs.get('seed_pool', []) if seed_pool is None else seed_pool
    pairs = independent = 0
    for candidate in candidates[:8]:
        frame['value'] = candidate['values'].loc[frame.index].values
        frame['value_rank'] = frame.groupby('date')['value'].rank(pct=True)
        for parent in candidate['parents']:
            seed = next((item for item in parent_seeds if item['name'] == parent), None)
            if seed is None:
                continue
            if parent not in seed_columns:
                seed_columns[parent] = _seed_series(panel, seed, fit_dates)
            parent_values = seed_columns[parent].loc[frame.index]
            frame['parent_rank'] = pd.Series(parent_values.values, index=frame.index) \
                .groupby(frame['date'].values).rank(pct=True)
            joined = frame[['date', 'value_rank', 'parent_rank']].dropna()
            corrs = [np.corrcoef(group['value_rank'], group['parent_rank'])[0, 1]
                     for _date, group in joined.groupby('date') if len(group) >= 10]
            corr = float(np.nanmean(corrs)) if corrs else np.nan
            pairs += 1
            if np.isfinite(corr) and abs(corr) < NOVELTY_CORR:
                independent += 1
    return independent / pairs if pairs else float('nan')


def _turnover_proxy(panel: pd.DataFrame, candidates: list[dict], eval_dates: pd.DatetimeIndex,
                    fraction: float = TURNOVER_TOPK,
                    support_mask: pd.Series | None = None) -> float:
    """Top-fraction membership churn between consecutive evaluated dates."""
    dates = list(eval_dates)
    if len(dates) < 3 or not candidates:
        return float('nan')
    sample = dates[::max(1, len(dates) // 10)]
    selected = panel['date'].isin(sample)
    if support_mask is not None:
        if not support_mask.index.equals(panel.index):
            raise GuardrailError('turnover support mask must be aligned to the panel index')
        selected &= support_mask.fillna(False).astype(bool)
    index = panel.index[selected]
    turnovers = []
    for candidate in candidates[:12]:
        frame = panel.loc[index, ['date', 'symbol']].copy()
        frame['value'] = candidate['values'].loc[index].values
        frame = frame.dropna(subset=['value'])
        if frame.empty:
            continue
        frame['rank'] = frame.groupby('date')['value'].rank(pct=True)
        top = frame.loc[frame['rank'] > 1 - fraction]
        groups = {date: set(group['symbol']) for date, group in top.groupby('date')}
        ordered = sorted(groups)
        for left, right in zip(ordered, ordered[1:]):
            intersection = len(groups[left] & groups[right])
            union = len(groups[left] | groups[right])
            if union:
                turnovers.append(1 - intersection / union)
    return float(np.nanmean(turnovers)) if turnovers else float('nan')


def _evaluate_probe_round(harness: dict, panel: pd.DataFrame, fit_dates: pd.DatetimeIndex,
                          eval_dates: pd.DatetimeIndex, candidates: list[dict],
                          support_mask: pd.Series, support: dict,
                          fit_support_mask: pd.Series, fit_support: dict, started: float,
                          include_daily: bool = False) -> dict:
    """Evaluate one harness after its comparison group has fixed common cells."""
    protocol = harness['protocol']
    min_pairs = int(protocol.get('min_ic_pairs', MIN_IC_PAIRS))
    records = []
    for candidate in candidates:
        series, n_dates = ic_series(panel, candidate['values'], eval_dates, min_pairs,
                                    support_mask=support_mask)
        mean_ic = float(series.mean()) if len(series) else float('nan')
        std_ic = float(series.std(ddof=1)) if len(series) > 1 else float('nan')
        icir = mean_ic / std_ic if np.isfinite(std_ic) and std_ic > 0 else float('nan')
        passed = bool(np.isfinite(mean_ic) and abs(mean_ic) >= PASS_MIN_IC
                      and np.isfinite(icir) and abs(icir) >= PASS_MIN_ICIR)
        candidate['ic_series'] = series
        candidate['mean_ic'] = mean_ic
        candidate['icir'] = float(abs(icir)) if np.isfinite(icir) else float('nan')
        candidate['passed'] = passed
        record = {'name': candidate['name'], 'kind': candidate['kind'],
                  'operator': candidate['operator'], 'parents': candidate['parents'],
                  'mean_ic': mean_ic, 'icir': candidate['icir'], 'passed': passed,
                  'n_dates': int(n_dates)}
        for key in ('fit_pair_correlation', 'fit_pair_strength'):
            if key in candidate:
                record[key] = candidate[key]
        if include_daily:
            record['ic_by_date'] = {str(date.date()): float(value)
                                    for date, value in candidate['ic_series'].items()
                                    if np.isfinite(value)}
        records.append(record)
    eligible_cells = int(support['eligible_cells'])
    coverage = support['cells'] / eligible_cells if eligible_cells else 0.0
    year_ics: dict = {}
    if harness.get('probe', {}).get('year_split'):
        for candidate in candidates:
            for year, group in candidate['ic_series'].groupby(candidate['ic_series'].index.year):
                year_ics.setdefault(candidate['name'], {})[int(year)] = float(group.mean())
    year_min = float(min(np.nanmin(list(entry.values())) for entry in year_ics.values())) if year_ics else float('nan')
    pass_count = sum(1 for record in records if record['passed'])
    abs_ic = np.abs(np.asarray([record['mean_ic'] for record in records], dtype=float))
    abs_ic = abs_ic[np.isfinite(abs_ic)]
    icirs = np.abs(np.asarray([record['icir'] for record in records], dtype=float))
    icirs = icirs[np.isfinite(icirs)]
    top_ic = float(np.mean(np.sort(abs_ic)[-max(1, len(abs_ic) // 4):])) if len(abs_ic) else float('nan')
    metrics = {
        'n_candidates': len(candidates),
        'pass_count': pass_count,
        'pass_rate': pass_count / len(candidates) if candidates else 0.0,
        'mean_abs_ic': float(np.mean(abs_ic)) if len(abs_ic) else float('nan'),
        'top_ic_mean': top_ic,
        'icir_median': float(np.median(icirs)) if len(icirs) else float('nan'),
        'icir_p25': float(np.percentile(icirs, 25)) if len(icirs) >= 4 else float('nan'),
        'coverage': float(coverage),
        'common_cells': support['cells'],
        'common_dates': support['dates'],
        'common_support_sha256': support['support_sha256'],
        'fit_common_cells': fit_support['cells'],
        'fit_common_dates': fit_support['dates'],
        'fit_common_support_sha256': fit_support['support_sha256'],
        'duplicate_rate': _duplicate_rate(
            candidates,
            min_overlap=int(harness.get('probe', {}).get('duplication_min_overlap', 30)),
            corr_threshold=float(harness.get('probe', {}).get('duplication_corr', DUP_CORR))),
        'novelty': _novelty(panel, candidates, fit_dates, support_mask=fit_support_mask,
                            seed_pool=harness.get('seed_pool', [])),
        'turnover_proxy': _turnover_proxy(panel, candidates, eval_dates,
                                          support_mask=support_mask),
        'year_ic_min': year_min,
        'runtime_seconds': round(time.time() - started, 2),
        'failures': 0,
        'max_date_read': str(panel['date'].max().date()),
    }
    return {'harness_hash': hashlib.sha256(json.dumps(harness, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            'metrics': metrics, 'candidates': records,
            'common_support': support, 'fit_common_support': fit_support}


def probe_rounds(harnesses: list[dict], panel: pd.DataFrame, fit_dates: pd.DatetimeIndex,
                 eval_dates: pd.DatetimeIndex, seed: int = 0,
                 include_daily: bool = False) -> list[dict]:
    """Evaluate all harness rosters on identical eligible date/stock cells.

    Generation remains deterministic and budgeted per harness. Only after every
    roster exists is the intersection fixed; IC and evaluation turnover ranks are
    then formed afresh inside those cells rather than on per-candidate dropna sets.
    """
    if not harnesses:
        raise GuardrailError('a common-support probe needs at least one harness')
    started = time.time()
    panel = panel.copy()
    grouped_candidates = []
    all_values = []
    fit_values = []
    for harness in harnesses:
        panel.attrs['seed_pool'] = harness.get('seed_pool', [])
        budget = int(harness['protocol']['budget_candidates'])
        candidates = generate_candidates(harness, panel, fit_dates, budget)
        grouped_candidates.append(candidates)
        all_values.extend(candidate['values'] for candidate in candidates)
        novelty_candidates = candidates[:8]
        fit_values.extend(candidate['values'] for candidate in novelty_candidates)
        seeds = {seed['name']: seed for seed in harness.get('seed_pool', [])}
        parents = sorted({parent for candidate in novelty_candidates for parent in candidate['parents']})
        fit_values.extend(_seed_series(panel, seeds[parent], fit_dates)
                          for parent in parents if parent in seeds)
    support_mask, support = common_support(panel, all_values, eval_dates)
    support = {**support, 'n_harnesses': len(harnesses)}
    fit_support_mask, fit_support = common_support(panel, fit_values, fit_dates)
    fit_support = {**fit_support, 'n_harnesses': len(harnesses)}
    return [_evaluate_probe_round(harness, panel, fit_dates, eval_dates, candidates,
                                  support_mask, support, fit_support_mask, fit_support, started,
                                  include_daily=include_daily)
            for harness, candidates in zip(harnesses, grouped_candidates)]


def probe_round(harness: dict, panel: pd.DataFrame, fit_dates: pd.DatetimeIndex,
                eval_dates: pd.DatetimeIndex, seed: int = 0) -> dict:
    return probe_rounds([harness], panel, fit_dates, eval_dates, seed=seed)[0]


def split_dates(panel: pd.DataFrame, split: float = 0.7) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex]:
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    cut = max(1, int(round(len(dates) * split)))
    return dates[:cut], dates[cut:]


def split_dev_dates(panel: pd.DataFrame, seed: int,
                    block_size: int = 4) -> tuple[pd.DatetimeIndex, pd.DatetimeIndex, pd.DatetimeIndex]:
    """Seeded year-stratified blocks: 50% fit, 25% proposal probe, 25% held-out dev."""
    dates = pd.DatetimeIndex(sorted(panel['date'].unique()))
    if len(dates) < 3:
        raise ValueError('seeded dev split needs at least three distinct dates')
    rng = np.random.default_rng(int(seed))
    groups = []
    for year in sorted(set(dates.year)):
        year_dates = dates[dates.year == year]
        blocks = [year_dates[start:start + block_size]
                  for start in range(0, len(year_dates), block_size)]
        groups.append([blocks[index] for index in rng.permutation(len(blocks))])
    ordered = []
    for depth in range(max(len(group) for group in groups)):
        for group in groups:
            if depth < len(group):
                ordered.append(group[depth])
    n_blocks = len(ordered)
    if n_blocks < 3:
        raise ValueError('seeded dev split needs at least three date blocks')
    n_fit = min(n_blocks - 2, max(1, int(round(n_blocks * 0.50))))
    n_probe = min(n_blocks - n_fit - 1, max(1, int(round(n_blocks * 0.25))))
    boundaries = (n_fit, n_fit + n_probe)
    parts = []
    start = 0
    for stop in (*boundaries, n_blocks):
        selected = [date for block in ordered[start:stop] for date in block]
        parts.append(pd.DatetimeIndex(sorted(selected)))
        start = stop
    return parts[0], parts[1], parts[2]


def check_guardrails(harness: dict, probe: dict) -> None:
    protocol = harness['protocol']
    guardrails = harness['guardrails']
    max_date = pd.Timestamp(probe['metrics']['max_date_read'])
    if max_date > pd.Timestamp(protocol['dev_end']):
        raise GuardrailError(f"probe read {max_date.date()} beyond dev_end {protocol['dev_end']}")
    if probe['metrics']['runtime_seconds'] > guardrails['max_runtime_seconds']:
        raise GuardrailError('probe exceeded max_runtime_seconds')
    if (probe['metrics']['coverage'] < guardrails['min_coverage']
            and guardrails.get('coverage_stop', True)):
        raise CoverageStop(f"coverage {probe['metrics']['coverage']:.3f} below "
                           f"{guardrails['min_coverage']:.3f}; STOP")
