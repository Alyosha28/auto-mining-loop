"""Bounded technical normalization of mentor quotes; no investment readiness claim.

Run: python -m research.mentor_clean --start 2016-01-01 --end 2021-12-31
     --output data_processed/mentor_clean --symbols 000001.SZ 600000.SH
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
SYMBOL = re.compile(r"\d{6}\.(SH|SZ|BJ)\Z")
FIELDS = {
    "open": ("开盘价", 1), "high": ("最高价", 1),
    "low": ("最低价", 1), "close": ("收盘价", 1),
    "reference_preclose": ("前收盘价", 1),
    "volume_shares": ("成交量(手)", 100),
    "amount_CNY": ("成交额(千元)", 1000),
    "turnover_fraction": ("换手率（%）", .01),
    "total_shares": ("总股本 （万股）", 10000),
    "float_shares": ("流通股本 （万股）", 10000),
    "total_market_cap_CNY": ("总市值 （万元）", 10000),
}
SCHEMA = pa.schema([("date", pa.string()), ("symbol", pa.string())] +
                   [(name, pa.float64()) for name in FIELDS] +
                   [("quality_flags", pa.string())])


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def bounds(start, end):
    for value in (start, end):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Dates must be ISO YYYY-MM-DD")
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if not dt.date(2016, 1, 1) <= a <= b <= dt.date(2022, 12, 31):
        raise ValueError("Quotes window must lie within 2016-01-01..2022-12-31")
    if b > dt.date(2021, 12, 31):
        # 2022 became readable only through the holdout preregistration.
        receipt = Path(__file__).resolve().parents[1] /             "reports/research/holdout_2022_preregistration_receipt.md"
        if not receipt.is_file():
            raise ValueError("2022 quotes requested but the holdout preregistration receipt is missing")
    return a, b


def normalize(frame, symbol, start, end, seen):
    """Drop malformed identities/duplicates; preserve numeric faults with flags."""
    required = ["证券代码", "交易时间"] + [v[0] for v in FIELDS.values()]
    if any(c not in frame for c in required):
        raise ValueError("Missing required columns")
    dates = frame["交易时间"].astype(str)
    syntax = dates.str.fullmatch(r"\d{8}")
    parsed = pd.to_datetime(dates.where(syntax), format="%Y%m%d", errors="coerce")
    valid_dates = parsed.notna()
    identities = frame["证券代码"].eq(symbol)
    quarantined = [{"row_index": int(i), "source_date": str(dates.loc[i]),
                    "reason": "malformed_date" if not valid_dates.loc[i] else "identity_conflict"}
                   for i in frame.index[~valid_dates | ~identities]]
    stats = {"malformed_date_rows": int((~valid_dates).sum()),
             "identity_conflict_rows": int((valid_dates & ~identities).sum()),
             "duplicate_rows": 0, "numeric_fault_rows": 0, "quarantined_rows": quarantined}
    in_window = valid_dates & parsed.between(pd.Timestamp(start), pd.Timestamp(end))
    selected = frame.loc[in_window & identities].copy()
    selected_dates = parsed.loc[selected.index].dt.strftime("%Y-%m-%d")
    keep = []
    for source_index, value in selected_dates.items():
        if value in seen:
            keep.append(False)
            stats["duplicate_rows"] += 1
            quarantined.append({"row_index": int(source_index), "source_date": value,
                                "reason": "duplicate_key", "raw_row": selected.loc[source_index].to_dict()})
        else:
            seen.add(value)
            keep.append(True)
    selected = selected.loc[np.asarray(keep, dtype=bool)]
    result = pd.DataFrame({"date": selected_dates.loc[selected.index], "symbol": symbol})
    for name, (column, multiplier) in FIELDS.items():
        result[name] = pd.to_numeric(selected[column], errors="coerce").astype(float) * multiplier
    flags = [[] for _ in range(len(result))]
    for name in FIELDS:
        values = result[name].to_numpy()
        bad = ~np.isfinite(values)
        for index in np.flatnonzero(bad):
            flags[index].append("nonfinite_" + name)
        result.loc[bad, name] = np.nan
        negative = values < 0
        for index in np.flatnonzero(negative):
            flags[index].append("negative_" + name)
    prices = result[["open", "high", "low", "close"]]
    invalid = (prices.le(0).any(axis=1) |
               (result.high < prices[["open", "close", "low"]].max(axis=1)) |
               (result.low > prices[["open", "close", "high"]].min(axis=1)))
    for index in np.flatnonzero(invalid.to_numpy()):
        flags[index].append("invalid_OHLC")
    result["quality_flags"] = ["|".join(row) for row in flags]
    stats["numeric_fault_rows"] = sum(bool(row) for row in flags)
    return result.reset_index(drop=True), stats


def build(start, end, output, symbols=None, source=None, chunksize=50000):
    start_date, end_date = bounds(start, end)
    source = Path(source or ROOT / "Data/a股指标行情").resolve()
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT) or output == ROOT or output.is_relative_to(ROOT / "Data"):
        raise ValueError("Output must be a new project directory outside Raw Data")
    names = sorted(set(symbols)) if symbols else None
    if names and any(not SYMBOL.fullmatch(s) for s in names):
        raise ValueError("Invalid symbol")
    paths = [source / (s + ".csv") for s in names] if names else sorted(source.glob("*.csv"))
    if not paths:
        raise ValueError("No CSV inputs")
    if any(p.is_symlink() or not SYMBOL.fullmatch(p.stem) for p in paths):
        raise ValueError("Unsafe or invalid input identity")
    config = {"start": start, "end": end, "symbols": names, "source": str(source),
              "version": 1, "semantics_sha256": digest(ROOT / "config/verified_data_semantics.json"),
              "blocks_sha256": digest(ROOT / "config/local_data_blocks.json"),
              "adapter_sha256": digest(Path(__file__))}
    inputs, errors = [], []
    for path in paths:
        try:
            inputs.append({"path": str(path), "sha256": digest(path)})
        except OSError as exc:
            errors.append({"path": str(path), "error": type(exc).__name__})
    if output.exists():
        manifest = json.loads((output / "manifest.json").read_text())
        if manifest["config"] != config or manifest["inputs"] != inputs or manifest["errors"]:
            raise ValueError("Existing output has different configuration/input or errors")
        for part in manifest["outputs"]:
            if digest(output / part["path"]) != part["sha256"]:
                raise ValueError("Existing output hash mismatch")
        quarantine = manifest.get("quarantine", {})
        if quarantine.get("path") and digest(output / quarantine["path"]) != quarantine["sha256"]:
            raise ValueError("Existing quarantine hash mismatch")
        return manifest
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="mentor-clean-", dir=output.parent))
    outputs = []
    quarantine_rows = 0
    duplicate_keys = set()
    counts = {k: 0 for k in ("malformed_date_rows", "identity_conflict_rows", "duplicate_rows", "numeric_fault_rows")}
    try:
        for item in inputs:
            path = Path(item["path"])
            writers, seen, part_meta = {}, set(), {}
            try:
                for chunk in pd.read_csv(path, encoding="gb18030", dtype=str, chunksize=chunksize):
                    clean, stats = normalize(chunk, path.stem, start_date, end_date, seen)
                    for key in counts:
                        counts[key] += stats[key]
                    if stats["quarantined_rows"]:
                        with (stage / "quarantine.jsonl").open("a", encoding="utf-8") as stream:
                            for item_q in stats["quarantined_rows"]:
                                if item_q['reason']=='duplicate_key':
                                    duplicate_keys.add((path.stem,item_q['source_date']))
                                stream.write(json.dumps({"source": str(path), "symbol": path.stem, **item_q}, ensure_ascii=False) + "\n")
                                quarantine_rows += 1
                    for year, group in clean.groupby(clean.date.str[:4]):
                        relative = f"year={year}/{path.stem}.parquet"
                        if year not in writers:
                            target = stage / relative
                            target.parent.mkdir(exist_ok=True)
                            writers[year] = pq.ParquetWriter(target, SCHEMA, compression="zstd")
                            part_meta[year] = {"path": relative, "symbol": path.stem, "rows": 0, "min_date": None, "max_date": None}
                        writers[year].write_table(pa.Table.from_pandas(group, schema=SCHEMA, preserve_index=False))
                        meta = part_meta[year]
                        meta["rows"] += len(group)
                        meta["min_date"] = min(meta["min_date"] or group.date.min(), group.date.min())
                        meta["max_date"] = max(meta["max_date"] or group.date.max(), group.date.max())
            except Exception as exc:
                errors.append({"path": str(path), "error": type(exc).__name__, "message": str(exc)[:300]})
            finally:
                for writer in writers.values():
                    writer.close()
            if digest(path) != item['sha256']:
                errors.append({'path':str(path),'error':'SourceChangedDuringRead'})
            for meta in part_meta.values():
                meta["sha256"] = digest(stage / meta["path"])
                outputs.append(meta)
        manifest = {"config": config, "inputs": inputs, "outputs": outputs, "errors": errors,
                    "quality_counts": counts, "rows": sum(p["rows"] for p in outputs),
                    "readiness": False, "production_ready": False,
                    "scope": "technical_quotes_only_no_universe_or_total_return",
                    "duplicate_policy": "keep_first_in_source_order_quarantine_subsequent_with_lineage",
                    "excluded_from_research_keys": [{'symbol':s,'date':d,'reason':'duplicate_source_key'} for s,d in sorted(duplicate_keys)],
                    "quarantine": {"rows": quarantine_rows, "path": "quarantine.jsonl" if quarantine_rows else None,
                                   "sha256": digest(stage / "quarantine.jsonl") if quarantine_rows else None},
                    "local_blocks": "recorded_by_hash_not_applied_to_raw_quote_fields"}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        stage.rename(output)
        return manifest
    except BaseException:
        shutil.rmtree(stage)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--symbols", nargs="+")
    args = parser.parse_args()
    manifest = build(args.start, args.end, args.output, args.symbols)
    print(json.dumps({"rows": manifest["rows"], "errors": len(manifest["errors"]),
                      "quality_counts": manifest["quality_counts"], "readiness": False}))
    return 1 if manifest["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
