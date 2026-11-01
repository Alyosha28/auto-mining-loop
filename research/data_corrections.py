"""Apply verified, dated evidence only; historical selection never consumes future blocks."""
import datetime as dt


def applicable_corrections(records, symbol, effective_date, signal_at):
    signal=dt.datetime.fromisoformat(signal_at)
    if signal.tzinfo is None:
        raise ValueError('signal must include timezone')
    date=dt.date.fromisoformat(effective_date)
    selected={}
    for row in records:
        if row['symbol']!=symbol or row['status']!='VERIFIED':
            continue
        available=dt.datetime.fromisoformat(row['available_at'])
        if available.tzinfo is None or available>signal:
            continue
        start=dt.date.fromisoformat(row['effective_date'])
        end=dt.date.fromisoformat(row['valid_to']) if row.get('valid_to') else None
        if not start<=date or (end is not None and date>=end):
            continue
        previous=selected.get(row['field'])
        if previous is not None and previous['value']!=row['value']:
            raise ValueError('conflicting verified evidence requires explicit adjudication')
        selected[row['field']]=dict(row)
    return selected


def overlay_verified_status(snapshot, records, signal_at):
    out=dict(snapshot)
    changes=applicable_corrections(records,out['symbol'],out['date'],signal_at)
    out['correction_evidence_ids']=[]
    for field,row in changes.items():
        if field not in ('listed','is_st','suspended','listing_date'):
            continue
        out[field]=row['value']
        out['correction_evidence_ids'].append(row['id'])
    return out


def affected_usage_blocks(blocks, symbol, start_date, end_date, usage):
    """Usage guard for labels/ledger/neutralization, never a universe-selection filter."""
    if usage=='universe_selection':
        raise ValueError('future action/price blocks must not filter current universe')
    return [dict(row) for row in blocks if row.get('symbol') in (symbol,'*')
            and usage in row['usages'] and row['start_date']<=end_date
            and row['end_date']>=start_date]
