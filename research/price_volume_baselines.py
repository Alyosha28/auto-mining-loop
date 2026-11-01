"""Twelve calendar-aligned raw-quote diagnostics, with no external data access."""
import datetime as dt
import re

import numpy as np
import pandas as pd


IDS = ("momentum_5", "momentum_20", "momentum_60", "reversal_5",
       "volatility_20", "volatility_60", "amihud_20", "turnover_mean_20",
       "turnover_cv_20", "volume_ratio_5_20", "close_location_mean_20",
       "overnight_gap_mean_20",
       "momentum_10", "momentum_120", "reversal_10", "mom_skip_recent_20",
       "dist_52w_high_250", "volatility_5", "downside_vol_20",
       "vol_ratio_20_60", "parkinson_vol_20", "max_daily_ret_20",
       "amihud_5", "turnover_mean_5", "turnover_std_20", "amount_ratio_5_60",
       "volume_shock_20", "volume_weighted_ret_20", "vol_return_corr_20",
       "mf_volume_ratio_20", "close_to_vwap_20", "range_mean_20")


def _date(value):
    value = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must be ISO YYYY-MM-DD")
    date = dt.date.fromisoformat(value)
    if date >= dt.date(2023, 1, 1):
        raise ValueError("OOS dates are forbidden")
    return date


def calculate_baselines(frame, calendar, signal_date):
    """Evaluate one symbol at one signal date; missing sessions stay missing.

    Numeric errors invalidate only candidates depending on that column/window.
    Input identity/date errors raise ValueError. All values are price diagnostics,
    including those whose formula only consumes volume or turnover.
    """
    dates = [_date(value) for value in calendar]
    if dates != sorted(set(dates)):
        raise ValueError("calendar must be strictly increasing and unique")
    signal = _date(signal_date)
    if signal not in dates:
        raise ValueError("signal_date is absent from calendar")
    if "date" not in frame:
        raise ValueError("missing date column")
    source = frame.copy()
    source["date"] = [_date(value) for value in source.date]
    if source.date.duplicated().any():
        raise ValueError("duplicate symbol/date")
    if "symbol" in source and (source.symbol.isna().any() or source.symbol.nunique() != 1):
        raise ValueError("input must contain exactly one symbol")
    source = source.loc[source.date <= signal].set_index("date")
    if any(value not in dates for value in source.index):
        raise ValueError("historical quote date is not in calendar")
    position = dates.index(signal)
    results = {name: {"value": None, "reason": None, "price_kind": "raw_quote_diagnostic"} for name in IDS}

    def window(count, columns, positive=()):
        if position + 1 < count:
            raise ValueError("insufficient_calendar_history")
        needed = dates[position-count+1:position+1]
        if any(value not in source.index for value in needed):
            raise ValueError("missing_calendar_session")
        if any(column not in source for column in columns):
            raise ValueError("missing_required_column")
        values = source.loc[needed, list(columns)].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("nonfinite_required_value")
        if (values < 0).any():
            raise ValueError("negative_required_value")
        for column in positive:
            if (values[:, columns.index(column)] <= 0).any():
                raise ValueError("nonpositive_required_price")
        return values

    def returns(width):
        close = window(width + 1, ("close",), ("close",))[:, 0]
        return close[1:] / close[:-1] - 1

    for candidate in IDS:
        try:
            if candidate.startswith("momentum_") or candidate == "reversal_5":
                width = int(candidate.split("_")[-1])
                close = window(width + 1, ("close",), ("close",))[:, 0]
                value = close[-1] / close[0] - 1
                if candidate == "reversal_5":
                    value = -value
            # momentum_10 / momentum_120 reuse the width-parsing branch above verbatim.
            elif candidate == "reversal_10":
                close = window(11, ("close",), ("close",))[:, 0]
                value = -(close[-1] / close[0] - 1)
            elif candidate == "mom_skip_recent_20":
                close = window(21, ("close",), ("close",))[:, 0]
                value = close[-6] / close[0] - 1
            elif candidate == "dist_52w_high_250":
                close = window(250, ("close",), ("close",))[:, 0]
                high = window(250, ("high",), ("high",))[:, 0]
                value = close[-1] / np.max(high) - 1
            elif candidate.startswith("volatility_"):
                value = np.std(returns(int(candidate.split("_")[-1])), ddof=1)
            # volatility_5 reuses the width-parsing branch above verbatim.
            elif candidate == "downside_vol_20":
                value = np.std(np.minimum(returns(20), 0), ddof=1)
            elif candidate == "vol_ratio_20_60":
                daily = returns(60)
                denominator = np.std(daily, ddof=1)
                if denominator == 0:
                    raise ValueError("zero_volatility_denominator")
                value = np.std(daily[-20:], ddof=1) / denominator
            elif candidate == "parkinson_vol_20":
                high, low = window(20, ("high", "low"), ("high", "low")).T
                value = np.sqrt(np.mean(np.log(high / low)**2) / (4*np.log(2)))
            elif candidate == "max_daily_ret_20":
                value = np.max(returns(20))
            elif candidate == "amihud_20":
                daily = returns(20)
                amount = window(20, ("amount_CNY",))[:, 0]
                if (amount == 0).any():
                    raise ValueError("zero_amount_denominator")
                value = np.mean(np.abs(daily) / amount)
            elif candidate == "amihud_5":
                daily = returns(5)
                amount = window(5, ("amount_CNY",))[:, 0]
                if (amount == 0).any():
                    raise ValueError("zero_amount_denominator")
                value = np.mean(np.abs(daily) / amount)
            elif candidate == "turnover_mean_5":
                value = np.mean(window(5, ("turnover_fraction",))[:, 0])
            elif candidate == "turnover_std_20":
                value = np.std(window(20, ("turnover_fraction",))[:, 0], ddof=1)
            elif candidate.startswith("turnover_"):
                turnover = window(20, ("turnover_fraction",))[:, 0]
                value = np.mean(turnover)
                if candidate == "turnover_cv_20":
                    if value == 0:
                        raise ValueError("zero_turnover_mean")
                    value = np.std(turnover, ddof=1) / value
            elif candidate == "amount_ratio_5_60":
                amount = window(60, ("amount_CNY",))[:, 0]
                denominator = np.mean(amount)
                if denominator == 0:
                    raise ValueError("zero_amount_mean")
                value = np.mean(amount[-5:]) / denominator
            elif candidate == "volume_shock_20":
                volume = window(80, ("volume_shares",))[:, 0]
                baseline = np.mean(volume[:60])
                if baseline == 0:
                    raise ValueError("zero_volume_baseline")
                value = float(np.count_nonzero(volume[60:] > 2 * baseline))
            elif candidate == "volume_weighted_ret_20":
                daily = returns(20)
                volume = window(20, ("volume_shares",))[:, 0]
                denominator = np.sum(volume)
                if denominator == 0:
                    raise ValueError("zero_volume_sum")
                value = np.sum(volume * daily) / denominator
            elif candidate == "vol_return_corr_20":
                daily = returns(20)
                volume = window(20, ("volume_shares",))[:, 0]
                if np.std(daily, ddof=1) == 0 or np.std(volume, ddof=1) == 0:
                    raise ValueError("constant_series_correlation")
                value = np.corrcoef(volume, daily)[0, 1]
            elif candidate == "mf_volume_ratio_20":
                daily = returns(20)
                volume = window(20, ("volume_shares",))[:, 0]
                denominator = np.sum(volume[daily < 0])
                if denominator == 0:
                    raise ValueError("zero_downside_volume")
                value = np.sum(volume[daily > 0]) / denominator
            elif candidate == "close_to_vwap_20":
                close, amount, volume = window(20, ("close", "amount_CNY", "volume_shares"), ("close",)).T
                with np.errstate(divide="ignore", invalid="ignore"):
                    vwap = amount / volume
                if not np.isfinite(vwap).all():
                    raise ValueError("nonfinite_vwap_denominator")
                if (vwap <= 0).any():
                    raise ValueError("nonpositive_vwap_denominator")
                value = np.mean(close / vwap) - 1
            elif candidate == "range_mean_20":
                close, high, low = window(20, ("close", "high", "low"), ("close", "high", "low")).T
                value = np.mean((high - low) / close)
            elif candidate == "volume_ratio_5_20":
                volume = window(20, ("volume_shares",))[:, 0]
                denominator = np.mean(volume)
                if denominator == 0:
                    raise ValueError("zero_volume_mean")
                value = np.mean(volume[-5:]) / denominator
            elif candidate == "close_location_mean_20":
                close, high, low = window(20, ("close", "high", "low"), ("close", "high", "low")).T
                if ((high < close) | (low > close) | (high < low)).any():
                    raise ValueError("invalid_OHLC")
                if (high == low).any():
                    raise ValueError("zero_intraday_range")
                value = np.mean((close-low) / (high-low))
            else:
                opening, reference = window(20, ("open", "reference_preclose"), ("open", "reference_preclose")).T
                value = np.mean(opening/reference - 1)
            if not np.isfinite(value):
                raise ValueError("nonfinite_result")
            results[candidate]["value"] = float(value)
        except ValueError as exc:
            results[candidate]["reason"] = str(exc)
    return results
