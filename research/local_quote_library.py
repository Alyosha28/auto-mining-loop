"""Callable local-quote factors. Raw prices use explicit action-window masks.

The returned research universe is an observed-quote/ST-lag proxy, not certified
historical listing or order execution. No network or external API is used.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
import datetime as dt
import numpy as np
import pandas as pd
from research.price_volume_batch import compute_signal_panel

PRICE_WINDOWS={'momentum_5':5,'momentum_10':10,'momentum_20':20,'momentum_60':60,'momentum_120':120,
    'reversal_5':5,'reversal_10':10,'mom_skip_recent_20':20,'dist_52w_high_250':250,
    'volatility_5':5,'volatility_20':20,'volatility_60':60,'downside_vol_20':20,'vol_ratio_20_60':60,
    'parkinson_vol_20':20,'max_daily_ret_20':20,'amihud_5':5,'amihud_20':20,
    'volume_weighted_ret_20':20,'vol_return_corr_20':20,'mf_volume_ratio_20':20,'overnight_gap_mean_20':20,
    'downside_asymmetry_20':20,'participation_reversal_5_20':5}
NEW_IDS=['downside_asymmetry_20','participation_reversal_5_20','intraday_flat_range_share_20']


def _adjusted_price_frame(frame):
    """Back-adjust OHLC with the vendor's ex-rights previous close."""
    work=frame.sort_values('date').copy()
    close=pd.to_numeric(work['close'],errors='coerce').astype(float)
    reference=pd.to_numeric(work['reference_preclose'],errors='coerce').astype(float)
    prev=close.shift(1)
    step=(reference/prev).where(close.gt(0)&prev.gt(0)&reference.gt(0)).fillna(1.0)
    future=step.iloc[::-1].cumprod().iloc[::-1].shift(-1).fillna(1.0)
    for name in ('open','high','low','close','reference_preclose','amount_CNY'):
        work[name]=pd.to_numeric(work[name],errors='coerce').astype(float)*future
    return work


def trading_calendar(archive_root, end):
    dates=[]
    for path in archive_root.rglob('*.7z'):
        if len(path.stem)!=8 or not path.stem.isdigit():continue
        date=dt.datetime.strptime(path.stem,'%Y%m%d').date().isoformat()
        if '2016-01-01'<=date<=end:dates.append(date)
    dates=sorted(set(dates))
    if not dates:raise ValueError('no dated local market archives')
    return dates


def weekly_signals(calendar, start, end):
    grouped={}
    for value in calendar:
        if not start<=value<=end:continue
        d=dt.date.fromisoformat(value);iso=d.isocalendar();grouped[(iso.year,iso.week)]=value
    # Exclude incomplete final week; if the vendor ends midweek, no Friday is invented.
    stop=dt.date.fromisoformat(end)
    return [v for v in grouped.values() if dt.date.fromisoformat(v)+dt.timedelta(days=4-dt.date.fromisoformat(v).weekday())<=stop]


def compute_quote_factors(frame,calendar,signals,action_dates,st_values,*,exposure_state=None):
    if frame.duplicated(['date','symbol']).any():raise ValueError('duplicate quote key')
    symbol=frame.symbol.iloc[0];q=frame.sort_values('date').set_index('date')
    # Invalid prices are not valid inputs just because they are numeric.
    price_good=(q[['open','high','low','close']].gt(0).all(axis=1)&q.high.ge(q[['open','close','low']].max(axis=1))&q.low.le(q[['open','close','high']].min(axis=1)))
    clean=q.copy();clean.loc[~price_good,['open','high','low','close','reference_preclose']]=np.nan
    clean=clean.reset_index()
    adjusted=_adjusted_price_frame(clean)
    out=compute_signal_panel(adjusted,calendar,signals,wide=True,exposure_state=exposure_state).set_index('date')
    aligned=adjusted.set_index('date').reindex(calendar)
    close=aligned.close.where(aligned.close.gt(0));ret=close/close.shift(1)-1
    sq=ret.pow(2);den=sq.rolling(20,min_periods=20).sum()
    asym=(sq.where(ret.lt(0),0).where(ret.notna()).rolling(20,min_periods=20).sum()-sq.where(ret.gt(0),0).where(ret.notna()).rolling(20,min_periods=20).sum())/den.where(den.gt(0))
    out['downside_asymmetry_20']=asym.reindex(signals)
    out['participation_reversal_5_20']=-out.momentum_5*(out.turnover_mean_5/out.turnover_mean_20.where(out.turnover_mean_20.gt(0))-1)
    flat=(aligned.high.eq(aligned.low)&aligned.volume_shares.gt(0)).astype(float)
    day_known=aligned[['high','low','volume_shares']].notna().all(axis=1)&aligned.high.gt(0)&aligned.low.gt(0)&aligned.volume_shares.gt(0)
    out['intraday_flat_range_share_20']=flat.where(day_known).rolling(20,min_periods=20).mean().reindex(signals)
    positions={d:i for i,d in enumerate(calendar)};actions=sorted(set(action_dates)) if action_dates is not None else None
    first_quote=frame.date.min();first_position=bisect_left(calendar,first_quote)
    reasons=[];labels=[];label_reasons=[];source_dates=[];listing_age=[];entries=[];exits=[]
    for i,date in enumerate(signals):
        pos=positions[date];prior=calendar[pos-1] if pos else None;source_dates.append(prior)
        age=pos-first_position+1;listing_age.append(age);state=st_values.get(prior)
        reason='eligible'
        if symbol.endswith('.BJ'):
            # BSE is outside the all-market research universe by policy (D4).
            reason='before_BSE_equity_market' if date<'2021-11-15' else 'bse_not_in_universe'
        elif age<0:
            # The local panel carries future listings as rows before their first
            # quote; those are not "young", they are not listed yet.
            reason='before_listing'
        elif state is None:reason='unknown_ST'
        elif state:reason='ST'
        elif age<60:reason='short_observed_history'
        elif date not in q.index:reason='missing_quote'
        elif not bool(price_good.get(date,False)) or not q.loc[date,'volume_shares']>0:reason='invalid_or_nontrading_quote'
        reasons.append(reason)
        value=np.nan;label_reason='terminal_or_missing_horizon';entry=None;exit_date=None
        if i+1<len(signals):
            entry=calendar[pos+1] if pos+1<len(calendar) else None
            nxt=positions[signals[i+1]]+1;exit_date=calendar[nxt] if nxt<len(calendar) else None
            if entry and exit_date:
                if actions is None:label_reason='unknown_action_history'
                elif bisect_right(actions,exit_date)>bisect_left(actions,entry):label_reason='action_horizon_raw_return_unfit'
                elif entry not in q.index or exit_date not in q.index:label_reason='missing_endpoint'
                elif not bool(price_good.get(entry,False)) or not bool(price_good.get(exit_date,False)):label_reason='invalid_endpoint'
                else:
                    value=float(q.loc[exit_date,'open']/q.loc[entry,'open']-1);label_reason='valid_price_diagnostic'
        labels.append(value);label_reasons.append(label_reason);entries.append(entry);exits.append(exit_date)
    out['research_mask']=[r=='eligible' for r in reasons];out['universe_reason']=reasons
    out['forward_return']=labels;out['label_reason']=label_reasons
    out['label_entry_date']=entries;out['label_exit_date']=exits
    out['status_source_date']=source_dates;out['observed_listing_age']=listing_age
    out['log_market_cap']=np.log(q.total_market_cap_CNY.where(q.total_market_cap_CNY.gt(0))).reindex(signals)
    return out.reset_index().replace([np.inf,-np.inf],np.nan)
