"""Strict PIT universe from complete historical status snapshots.

Missing status fails closed. This module does not infer historical status from
today's names, next week's prices, or zero trading volume.
"""
import datetime as dt
from research.evaluation import CUTOFF, calendar_dates


def universe_at(snapshots, symbols, signal_at, calendar, min_listing_days=60, corrections=()):
    signal = dt.datetime.fromisoformat(signal_at)
    shanghai = dt.timezone(dt.timedelta(hours=8))
    if signal.tzinfo is None:
        raise ValueError('signal_at must be offset-aware')
    date = signal.astimezone(shanghai).date()
    if date >= CUTOFF:
        raise ValueError('OOS signal forbidden')
    dates = calendar_dates(calendar)
    if date not in dates:
        raise ValueError('signal date absent from exchange calendar')
    latest = {}
    for row in snapshots:
        effective = dt.date.fromisoformat(row['date'])
        available = dt.datetime.fromisoformat(row['available_at'])
        if available.tzinfo is None:
            raise ValueError('available_at must be offset-aware')
        if effective != date or available > signal:
            continue
        symbol = row['symbol']
        old = latest.get(symbol)
        if old is not None and old[0] == available and old[1] != row:
            raise ValueError('conflicting status snapshots')
        if old is None or available > old[0]:
            latest[symbol] = (available, row)
    result=[]
    for symbol in sorted(set(symbols)):
        item={'signal_at':signal_at,'symbol':symbol,'research_mask':False,'reason':None}
        if symbol not in latest:
            item['reason']='unknown_status'
        else:
            row=latest[symbol][1]
            if corrections:
                from research.data_corrections import overlay_verified_status
                row=overlay_verified_status(row,corrections,signal_at)
                item['correction_evidence_ids']=row['correction_evidence_ids']
            flags=[row.get(k) for k in ['listed','is_st','suspended']]
            if row.get('asset_type') is None or any(type(f) is not bool for f in flags) or not row.get('listing_date'):
                item['reason']='incomplete_status'
            elif row['asset_type']!='a_share': item['reason']='not_a_share'
            elif not row['listed']: item['reason']='not_listed'
            elif row['is_st']: item['reason']='st'
            elif row['suspended']: item['reason']='suspended_at_signal'
            else:
                listed=dt.date.fromisoformat(row['listing_date'])
                # A calendar segment that starts after listing can certify at
                # least the minimum only if it contains that many known sessions.
                age=sum(listed <= d <= date for d in dates)
                if age < min_listing_days:
                    item['reason']='listing_age_below_minimum_or_insufficient_calendar'
                else:
                    item['research_mask']=True
                    item['reason']='eligible'
        result.append(item)
    return result
