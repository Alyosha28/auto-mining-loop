"""Calendar-aligned labels and cross-sectional statistics.

These primitives do not certify vendor point-in-time completeness. Raw price
ratios are diagnostic price returns, not executable portfolio total returns.
"""
from collections.abc import Mapping
import datetime as dt
import math
from statistics import mean, stdev

import numpy as np
import pandas as pd

CUTOFF = dt.date(2023, 1, 1)


def calendar_dates(values):
    dates = [dt.date.fromisoformat(str(v)) for v in values]
    if dates != sorted(set(dates)):
        raise ValueError('calendar must be strictly sorted and unique')
    if any(d >= CUTOFF for d in dates):
        raise ValueError('calendar contains OOS dates')
    return dates


def weekly_schedule(calendar):
    dates = calendar_dates(calendar)
    by_week = {}
    for index, date in enumerate(dates):
        by_week[date.isocalendar()[:2]] = index
    last_indexes = list(by_week.values())
    result = []
    for i, index in enumerate(last_indexes):
        entry = dates[index + 1] if index + 1 < len(dates) else None
        next_index = last_indexes[i+1] if i+1 < len(last_indexes) else None
        exit_date = dates[next_index+1] if next_index is not None and next_index+1 < len(dates) else None
        result.append({'signal_date': dates[index], 'entry_date': entry, 'exit_date': exit_date})
    return result


def weekly_price_labels(prices, schedule, requests, period_end='2022-12-31'):
    """requests lists (signal_date,symbol); missing prices never shift the horizon."""
    end = dt.date.fromisoformat(period_end)
    if end >= CUTOFF:
        raise ValueError('period_end crosses OOS')
    lookup = {}
    for row in prices:
        date = dt.date.fromisoformat(str(row['date']))
        if date >= CUTOFF:
            raise ValueError('price input contains OOS')
        key = (date, row['symbol'])
        if key in lookup:
            raise ValueError('duplicate price key')
        lookup[key] = row['open']
    schedule_lookup = {str(s['signal_date']): s for s in schedule}
    result = []
    for signal, symbol in requests:
        s = schedule_lookup[str(signal)]
        entry, exit_date = s['entry_date'], s['exit_date']
        row = {'signal_date': str(signal), 'symbol': symbol,
               'entry_date': str(entry) if entry else None,
               'exit_date': str(exit_date) if exit_date else None,
               'forward_return': None, 'return_kind': 'raw_open_price_return_diagnostic', 'reason': None}
        if entry is None or exit_date is None:
            row['reason'] = 'incomplete_calendar_horizon'
        elif exit_date > end or entry >= CUTOFF or exit_date >= CUTOFF:
            row['reason'] = 'horizon_crosses_period_boundary'
        else:
            a, b = lookup.get((entry, symbol)), lookup.get((exit_date, symbol))
            if a is None or b is None or not all(math.isfinite(float(x)) and float(x)>0 for x in [a,b]):
                row['reason'] = 'missing_or_invalid_endpoint'
            else:
                row['forward_return'] = b/a-1
        result.append(row)
    return result


def available_versions(records, signal_at):
    """Explicit version-as-of; caller supplies offset-aware availability timestamps."""
    signal = dt.datetime.fromisoformat(signal_at)
    if signal.tzinfo is None or signal.astimezone(dt.timezone(dt.timedelta(hours=8))).date() >= CUTOFF:
        raise ValueError('signal must be timezone-aware and pre-OOS')
    selected = {}
    for row in records:
        available = dt.datetime.fromisoformat(row['available_at'])
        if available.tzinfo is None:
            raise ValueError('availability must have timezone')
        if available > signal:
            continue
        key = (row['symbol'], row['period'], row['field'])
        previous = selected.get(key)
        if previous and available == previous[0] and row != previous[1]:
            raise ValueError('ambiguous simultaneous versions')
        if previous is None or available > previous[0]:
            selected[key] = (available, dict(row))
    return [value[1] for _,value in sorted(selected.items())]


def preprocess_cross_section(frame, winsorize=(0.01,0.99)):
    """Input contains a single signal cross-section, symbol/factor/research_mask."""
    if frame['symbol'].duplicated().any():
        raise ValueError('duplicate symbol in cross-section')
    if frame['research_mask'].isna().any() or not pd.api.types.is_bool_dtype(frame['research_mask']):
        raise ValueError('research_mask must be explicit boolean')
    low, high = winsorize
    if not 0 <= low < high <= 1:
        raise ValueError('invalid winsorization quantiles')
    out = frame.loc[frame['research_mask']].copy()
    values = pd.to_numeric(out['factor'],errors='coerce').replace([np.inf,-np.inf],np.nan)
    clipped = values.clip(values.quantile(low),values.quantile(high))
    sd = clipped.std(ddof=0)
    out['processed'] = (clipped-clipped.mean())/sd if pd.notna(sd) and sd>0 else np.nan
    out['rank'] = out['processed'].rank(method='average',ascending=False)
    return out.sort_values('symbol').reset_index(drop=True)


def rank_ic(factors, returns, min_pairs=30):
    if not factors.index.is_unique or not returns.index.is_unique:
        raise ValueError('duplicate symbols in IC')
    pairs = pd.concat([factors.rename('f'),returns.rename('r')],axis=1)
    pairs = pairs.apply(pd.to_numeric,errors='coerce').replace([np.inf,-np.inf],np.nan).dropna()
    n = len(pairs)
    if n < min_pairs:
        return {'rank_ic': None,'pairs':n,'reason':'insufficient_pairs'}
    ranks = pairs.rank(method='average')
    if ranks['f'].nunique()<2 or ranks['r'].nunique()<2:
        return {'rank_ic': None,'pairs':n,'reason':'constant_input'}
    return {'rank_ic':float(ranks['f'].corr(ranks['r'])),'pairs':n,'reason':None}


def rank_icir(values):
    values = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    avg = mean(values) if values else None
    sd = stdev(values) if len(values)>1 else None
    return {'mean_rank_ic':avg,'std_rank_ic':sd,'rank_icir':avg/sd if sd is not None and sd>0 else None,
            'valid_weeks':len(values),'ddof':1,'annualized':False}


# --- screening extensions: quantile spread, IC decay, stratified IC --------
# Pure cross-sectional helpers. They accept the same inputs as rank_ic (a
# key-indexed Series, i.e. symbol-indexed) and additionally Mapping or
# (key, value) pair sequences. No file IO, no net-value chaining: every
# statistic is recomputed per horizon and per stratum.


def _as_day(value):
    """Return value as a date when it unambiguously denotes one, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, np.datetime64):
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.date()
    if isinstance(value, str):
        try:
            return dt.datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    if isinstance(value, (int, np.integer)):
        number = int(value)
        if 19000101 <= number <= 29991231:
            text = str(number)
            try:
                return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
            except ValueError:
                return None
    return None


def _assert_pre_oos(values, name):
    for value in values:
        if isinstance(value, (tuple, list)):
            _assert_pre_oos(value, name)
            continue
        day = _as_day(value)
        if day is not None and day >= CUTOFF:
            raise ValueError(name + ' contains OOS dates')


def _assert_series_pre_oos(series, name):
    _assert_pre_oos(series.index.tolist(), name)
    dtype = series.dtype
    if (pd.api.types.is_datetime64_any_dtype(dtype) or pd.api.types.is_object_dtype(dtype)
            or pd.api.types.is_string_dtype(dtype)):
        _assert_pre_oos(series.tolist(), name)


def _as_cross_section(values, name):
    """Normalize Series / Mapping / (key, value) pairs to a key-indexed Series."""
    if isinstance(values, pd.Series):
        series = values
    elif isinstance(values, Mapping):
        series = pd.Series(dict(values))
    else:
        keys, items = [], []
        for entry in values:
            if not isinstance(entry, (tuple, list)) or len(entry) != 2:
                raise ValueError(name + ' expects (key, value) pairs')
            keys.append(entry[0]); items.append(entry[1])
        if len(set(keys)) != len(keys):
            raise ValueError('duplicate keys in ' + name)
        series = pd.Series(items, index=keys)
    if series.index.has_duplicates:
        raise ValueError('duplicate keys in ' + name)
    return series


def _canonical_rows(frame):
    """Total, type-safe row order so the same cross-section sums identically."""
    order = sorted(range(len(frame)), key=lambda i: (type(frame.index[i]).__name__, repr(frame.index[i])))
    return frame.iloc[order]


def _aligned_frame(factors, returns, strata=None):
    """Key-aligned numeric pairs; missing/non-numeric/inf pairs are dropped."""
    f = _as_cross_section(factors, 'factors')
    r = _as_cross_section(returns, 'returns')
    _assert_series_pre_oos(f, 'factors'); _assert_series_pre_oos(r, 'returns')
    columns = [f.rename('factor'), r.rename('return')]
    if strata is not None:
        s = _as_cross_section(strata, 'strata')
        _assert_series_pre_oos(s, 'strata')
        columns.append(s.rename('stratum'))
    frame = pd.concat(columns, axis=1).copy()
    frame['factor'] = pd.to_numeric(frame['factor'], errors='coerce').replace([np.inf, -np.inf], np.nan)
    frame['return'] = pd.to_numeric(frame['return'], errors='coerce').replace([np.inf, -np.inf], np.nan)
    frame = frame.loc[frame['factor'].notna() & frame['return'].notna()]
    return _canonical_rows(frame)


def _validated_min_pairs(min_pairs):
    if isinstance(min_pairs, bool) or not isinstance(min_pairs, (int, np.integer)) or int(min_pairs) < 1:
        raise ValueError('min_pairs must be a positive integer')
    return int(min_pairs)


def _validated_quantiles(quantiles):
    if isinstance(quantiles, bool) or not isinstance(quantiles, (int, np.integer)) or int(quantiles) < 2:
        raise ValueError('quantiles must be an integer >= 2')
    return int(quantiles)


def _quantile_codes(frame, quantiles):
    """Average-rank mid-point binning: tied factor values never split."""
    ranks = frame['factor'].rank(method='average')
    n = len(ranks)
    codes = np.floor((ranks.to_numpy(dtype=float) - 0.5) * float(quantiles) / float(n)).astype(int)
    return np.clip(codes, 0, quantiles - 1)


def _finite_or_none(value):
    return float(value) if value is not None and math.isfinite(float(value)) else None


def quantile_returns(factors, returns, quantiles=5, min_pairs=30):
    """Equal-weighted mean return per ascending-factor quantile bucket.

    Group 1 is always the lowest raw factor value: direction is a caller
    concern. Ties share one average rank, so a tied block cannot be split by
    the bucket rule or by input ordering. A bucket with no member has a None
    mean, never 0; insufficient pairs or a constant factor return all-None
    statistics with a reason.
    """
    quantiles = _validated_quantiles(quantiles)
    min_pairs = _validated_min_pairs(min_pairs)
    frame = _aligned_frame(factors, returns)
    n = len(frame)
    empty = {'quantiles': [None] * quantiles, 'counts': [None] * quantiles, 'spread': None,
             'monotonicity': None, 'pairs': n, 'n_groups': quantiles, 'min_pairs': min_pairs,
             'empty_groups': None, 'spread_reason': None, 'monotonicity_reason': None}
    if n < min_pairs:
        return {**empty, 'reason': 'insufficient_pairs'}
    if frame['factor'].nunique() < 2:
        return {**empty, 'reason': 'constant_input'}
    codes = _quantile_codes(frame, quantiles)
    values = frame['return'].to_numpy(dtype=float)
    means, counts = [], []
    for group in range(quantiles):
        mask = codes == group
        count = int(mask.sum())
        counts.append(count)
        means.append(_finite_or_none(values[mask].mean()) if count else None)
    spread, spread_reason = None, None
    if means[0] is None or means[-1] is None:
        spread_reason = 'empty_extreme_group'
    else:
        spread = _finite_or_none(means[-1] - means[0])
        spread_reason = None if spread is not None else 'non_finite'
    ordinals = [group + 1 for group in range(quantiles) if means[group] is not None]
    bucket_returns = [means[group] for group in range(quantiles) if means[group] is not None]
    monotonicity, monotonicity_reason = None, None
    if len(bucket_returns) < 2:
        monotonicity_reason = 'insufficient_groups'
    elif len(set(bucket_returns)) < 2:
        monotonicity_reason = 'constant_group_returns'
    else:
        rank_corr = pd.Series(ordinals, dtype=float).rank().corr(pd.Series(bucket_returns, dtype=float).rank())
        monotonicity = _finite_or_none(rank_corr)
        monotonicity_reason = None if monotonicity is not None else 'undefined'
    return {'quantiles': means, 'counts': counts, 'spread': spread, 'monotonicity': monotonicity,
            'pairs': n, 'n_groups': quantiles, 'min_pairs': min_pairs,
            'empty_groups': int(sum(1 for count in counts if count == 0)),
            'spread_reason': spread_reason, 'monotonicity_reason': monotonicity_reason, 'reason': None}


def _normalized_horizon(horizon):
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float, np.integer, np.floating, str)):
        raise ValueError('horizons must be positive finite numbers')
    if _as_day(horizon) is not None:
        # A YYYYMMDD key is a date, not a day count; refuse it instead of
        # silently reading it as a multi-thousand-year horizon.
        raise ValueError('horizons are forward day counts, not dates')
    try:
        value = float(horizon)
    except (TypeError, ValueError):
        raise ValueError('horizons must be positive finite numbers')
    if not math.isfinite(value) or value <= 0:
        raise ValueError('horizons must be positive finite numbers')
    return int(value) if value.is_integer() else value


def decay_profile(factors, returns_by_horizon, min_pairs=30):
    """Rank IC per forward horizon for one shared signal cross-section.

    Each horizon is a separate cross-sectional statistic over exactly the
    provided return series: overlapping-horizon returns are never summed,
    chained or compounded into a net-value path. Horizons are reported in
    ascending order; horizons with too few pairs stay None with a reason.
    """
    min_pairs = _validated_min_pairs(min_pairs)
    if not isinstance(returns_by_horizon, Mapping):
        raise ValueError('returns_by_horizon must be a mapping of horizon to returns')
    normalized = {}
    for horizon in returns_by_horizon:
        key = _normalized_horizon(horizon)
        if key in normalized:
            raise ValueError('duplicate horizons after normalization')
        normalized[key] = returns_by_horizon[horizon]
    f = _as_cross_section(factors, 'factors')
    _assert_series_pre_oos(f, 'factors')
    horizons = sorted(normalized)
    if not horizons:
        return {'horizons': [], 'by_horizon': {}, 'ic_sequence': [], 'decay_vs_shortest': [],
                'successive_decay': [], 'shortest_valid_horizon': None, 'overlap_compounding': False,
                'reason': 'no_horizons'}
    by_horizon = {}
    for horizon in horizons:
        name = 'returns[%s]' % horizon
        r = _as_cross_section(normalized[horizon], name)
        _assert_series_pre_oos(r, name)
        by_horizon[horizon] = rank_ic(f, r, min_pairs=min_pairs)
    valid = [h for h in horizons if by_horizon[h]['rank_ic'] is not None]
    shortest = valid[0] if valid else None
    decay_vs_shortest = []
    for horizon in horizons:
        ic, base = by_horizon[horizon]['rank_ic'], None if shortest is None else by_horizon[shortest]['rank_ic']
        decay_vs_shortest.append({'horizon': horizon,
                                  'delta_rank_ic': _finite_or_none(ic - base) if ic is not None and base is not None else None})
    successive_decay = []
    for first, second in zip(horizons, horizons[1:]):
        a, b = by_horizon[first]['rank_ic'], by_horizon[second]['rank_ic']
        successive_decay.append({'from_horizon': first, 'to_horizon': second,
                                 'delta_rank_ic': _finite_or_none(b - a) if a is not None and b is not None else None})
    return {'horizons': horizons, 'by_horizon': by_horizon,
            'ic_sequence': [by_horizon[h]['rank_ic'] for h in horizons],
            'decay_vs_shortest': decay_vs_shortest, 'successive_decay': successive_decay,
            'shortest_valid_horizon': shortest, 'overlap_compounding': False,
            'reason': None if valid else 'no_valid_horizon'}


def _as_label(value):
    return value.item() if isinstance(value, np.generic) else value


def _ordered_labels(labels):
    unique = []
    seen = set()
    for label in labels:
        if label not in seen:
            seen.add(label); unique.append(label)
    try:
        return sorted(unique)
    except TypeError:
        return sorted(unique, key=lambda value: (type(value).__name__, repr(value)))


def stratified_ic(factors, returns, strata, min_pairs=30):
    """Rank IC inside each stratum plus between-stratum IC dispersion.

    Strata are reported independently: a stratum below min_pairs, or one with
    a constant input, gets None with a reason and is excluded from the
    dispersion statistics rather than pooled with its neighbours. Rows with a
    missing stratum label are counted but never assigned to a layer.
    """
    min_pairs = _validated_min_pairs(min_pairs)
    frame = _aligned_frame(factors, returns, strata)
    frame = frame.assign(stratum=frame['stratum'].map(_as_label))
    unlabeled = int(frame['stratum'].isna().sum())
    labeled = frame.loc[frame['stratum'].notna()]
    try:
        labels = _ordered_labels(labeled['stratum'].tolist())
    except TypeError:
        raise ValueError('stratum labels must be hashable')
    by_stratum = {}
    for label in labels:
        subset = labeled.loc[labeled['stratum'] == label]
        by_stratum[label] = rank_ic(subset['factor'], subset['return'], min_pairs=min_pairs)
    valid = [entry['rank_ic'] for entry in by_stratum.values() if entry['rank_ic'] is not None]
    ic_mean = _finite_or_none(mean(valid)) if valid else None
    ic_std = _finite_or_none(stdev(valid)) if len(valid) > 1 else None
    ic_range = _finite_or_none(max(valid) - min(valid)) if len(valid) > 1 else None
    dispersion_reason = None
    if not valid:
        dispersion_reason = 'no_valid_stratum'
    elif len(valid) < 2:
        dispersion_reason = 'insufficient_valid_strata'
    if not len(frame):
        reason = 'no_valid_pairs'
    elif not valid:
        reason = 'no_valid_stratum'
    else:
        reason = None
    return {'strata': labels, 'by_stratum': by_stratum, 'ic_mean': ic_mean, 'ic_std': ic_std,
            'ic_range': ic_range, 'ic_std_ddof': 1, 'dispersion_reason': dispersion_reason,
            'valid_strata': len(valid), 'total_strata': len(labels),
            'pairs': len(frame), 'labeled_pairs': len(labeled), 'unlabeled_pairs': unlabeled,
            'min_pairs': min_pairs, 'reason': reason}
