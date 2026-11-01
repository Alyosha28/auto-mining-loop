"""Vectorized raw-quote candidate panel; scalar definitions are the reference."""
import numpy as np
import pandas as pd
import datetime as dt

from research.price_volume_baselines import IDS, _date


def compute_signal_panel(frame, calendar, signals, *, wide=False, exposure_state=None):
    parse_date = _date if exposure_state is None else lambda value: dt.date.fromisoformat(str(value))
    dates = [parse_date(value) for value in calendar]
    if exposure_state is not None:
        from research.run_contract import _access
        if not dates:raise ValueError('empty calendar')
        _access({'start':min(dates).isoformat(),'end':max(dates).isoformat(),'evaluation_purpose':'development'},exposure_state)
    if dates != sorted(set(dates)):
        raise ValueError("calendar must be strictly increasing and unique")
    requested = [parse_date(value) for value in signals]
    if len(requested) != len(set(requested)) or any(value not in dates for value in requested):
        raise ValueError("signals must be unique calendar dates")
    if "date" not in frame or "symbol" not in frame:
        raise ValueError("date and symbol are required")
    data = frame.copy()
    data["date"] = [parse_date(value) for value in data.date]
    if data.date.duplicated().any():
        raise ValueError("duplicate symbol/date")
    if data.symbol.isna().any() or data.symbol.nunique() != 1:
        raise ValueError("exactly one symbol is required")
    if any(value not in dates for value in data.date):
        raise ValueError("quote date is not in calendar")
    symbol = data.symbol.iloc[0]
    data = data.set_index("date").reindex(dates)

    def column(name, positive=False):
        result = pd.to_numeric(data[name], errors="coerce") if name in data else pd.Series(np.nan, index=data.index)
        result = result.astype(float)
        valid = np.isfinite(result) & (result > 0 if positive else result >= 0)
        return result.where(valid)

    def complete(series, width):
        return series.rolling(width, min_periods=width).count().eq(width)

    close = column("close", True)
    values = {}
    for width in (5, 10, 20, 60, 120):
        values[f"momentum_{width}"] = (close / close.shift(width)-1).where(complete(close, width+1))
    values["reversal_5"] = -values["momentum_5"]
    values["reversal_10"] = -values["momentum_10"]
    daily = close / close.shift(1)-1
    for width in (5, 20, 60):
        values[f"volatility_{width}"] = daily.rolling(width, min_periods=width).std(ddof=1)
    amount = column("amount_CNY").replace(0, np.nan)
    values["amihud_20"] = (daily.abs()/amount).rolling(20, min_periods=20).mean()
    turnover = column("turnover_fraction")
    turnover_mean = turnover.rolling(20, min_periods=20).mean()
    values["turnover_mean_20"] = turnover_mean
    values["turnover_cv_20"] = turnover.rolling(20, min_periods=20).std(ddof=1) / turnover_mean.replace(0, np.nan)
    volume = column("volume_shares")
    values["volume_ratio_5_20"] = (volume.rolling(5, min_periods=5).mean() /
                                      volume.rolling(20, min_periods=20).mean().replace(0, np.nan))
    high, low = column("high", True), column("low", True)
    location = ((close-low)/(high-low)).where((high > low) & (high >= close) & (low <= close))
    values["close_location_mean_20"] = location.rolling(20, min_periods=20).mean()
    opening, reference = column("open", True), column("reference_preclose", True)
    values["overnight_gap_mean_20"] = (opening/reference-1).rolling(20, min_periods=20).mean()
    # Appended 20 candidates; same column()/complete() rolling pattern as the original 12.
    values["mom_skip_recent_20"] = (close.shift(5)/close.shift(20)-1).where(complete(close, 21))
    values["dist_52w_high_250"] = (close/high.rolling(250, min_periods=250).max()-1).where(
        complete(high, 250) & complete(close, 250))
    values["downside_vol_20"] = daily.clip(upper=0).rolling(20, min_periods=20).std(ddof=1)
    std20 = daily.rolling(20, min_periods=20).std(ddof=1)
    std60 = daily.rolling(60, min_periods=60).std(ddof=1)
    values["vol_ratio_20_60"] = std20 / std60.replace(0, np.nan)
    values["parkinson_vol_20"] = np.sqrt((np.log(high/low)**2).rolling(20, min_periods=20).mean()/(4*np.log(2)))
    values["max_daily_ret_20"] = daily.rolling(20, min_periods=20).max()
    values["amihud_5"] = (daily.abs()/amount).rolling(5, min_periods=5).mean()
    values["turnover_mean_5"] = turnover.rolling(5, min_periods=5).mean()
    values["turnover_std_20"] = turnover.rolling(20, min_periods=20).std(ddof=1)
    amount_raw = column("amount_CNY")
    values["amount_ratio_5_60"] = (amount_raw.rolling(5, min_periods=5).mean() /
                                   amount_raw.rolling(60, min_periods=60).mean().replace(0, np.nan))
    # The baseline is fixed per count window (mean of the preceding 60 sessions),
    # so compare each of the 20 lagged volumes against that window's own baseline
    # instead of rolling a single comparison series.
    baseline = volume.rolling(60, min_periods=60).mean().shift(20)
    shocks = pd.Series(0.0, index=volume.index)
    for lag in range(20):
        shocks += (volume.shift(lag) > 2*baseline).astype(float)
    values["volume_shock_20"] = shocks.where(complete(volume, 80) & baseline.notna() & baseline.ne(0))
    values["volume_weighted_ret_20"] = ((volume*daily).rolling(20, min_periods=20).sum() /
                                        volume.rolling(20, min_periods=20).sum().replace(0, np.nan))
    complete20 = complete(volume, 20) & complete(daily, 20)
    std20_volume = volume.rolling(20, min_periods=20).std(ddof=1)
    correlation = volume.rolling(20, min_periods=20).corr(daily)
    values["vol_return_corr_20"] = correlation.where(complete20 & std20_volume.gt(0) & std20.gt(0))
    up_volume = volume.where(daily > 0, 0.0)
    down_volume = volume.where(daily < 0, 0.0)
    values["mf_volume_ratio_20"] = (up_volume.rolling(20, min_periods=20).sum() /
                                    down_volume.rolling(20, min_periods=20).sum().replace(0, np.nan)).where(complete20)
    vwap = amount_raw/volume
    vwap = vwap.where(np.isfinite(vwap) & (vwap > 0))
    values["close_to_vwap_20"] = (close/vwap).rolling(20, min_periods=20).mean()-1
    values["range_mean_20"] = ((high-low)/close).rolling(20, min_periods=20).mean()
    if wide:
        result = pd.DataFrame(values).reindex(sorted(requested)).replace([np.inf, -np.inf], np.nan)
        result.index = [value.isoformat() for value in result.index]
        result.index.name = 'date'
        result['symbol'] = symbol
        return result.reset_index()
    rows = []
    for signal in sorted(requested):
        for candidate in IDS:
            value = values[candidate].loc[signal]
            valid = pd.notna(value) and np.isfinite(value)
            rows.append({"date": signal.isoformat(), "symbol": symbol, "candidate_id": candidate,
                         "value": float(value) if valid else None,
                         "reason": None if valid else "missing_or_invalid_window",
                         "price_kind": "raw_quote_diagnostic"})
    return pd.DataFrame(rows, columns=["date", "symbol", "candidate_id", "value", "reason", "price_kind"])
