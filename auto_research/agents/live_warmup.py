#!/usr/bin/env python3
"""Build predictor-only pre-development history from bounded local CSV streams."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_manifest import sha256_file, sha256_json, utc_now, write_immutable_json
from research.local_quote_library import PRICE_WINDOWS, weekly_signals
from research.mentor_clean import FIELDS, SYMBOL, normalize
from research.price_volume_batch import compute_signal_panel

DEV_START = dt.date(2017, 1, 1)
DEFAULT_FEATURES = (
    "momentum_5", "momentum_20", "volatility_20", "amihud_20", "turnover_mean_20",
    "volume_ratio_5_20", "close_location_mean_20", "overnight_gap_mean_20", "range_mean_20",
)
LABEL_COLUMNS = ("forward_return", "label_reason", "label_entry_date", "label_exit_date")


def _parse_date(value: str) -> dt.date:
    value = str(value).strip()
    if len(value) == 8 and value.isdigit():
        return dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    return dt.date.fromisoformat(value[:10])


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _bounded_csv(path: Path, columns: list[str], start: dt.date, end: dt.date,
                 data_root: Path, date_column: str = "交易时间") -> tuple[pd.DataFrame, dict]:
    if end >= DEV_START or start > end:
        raise ValueError("warm-up raw reads must end before 2017-01-01")
    data_root = data_root.resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(data_root):
        raise PermissionError("raw source escapes the authorized Data root")
    selected = []
    first_date = None
    previous = None
    boundary = None
    scanned = 0
    with path.open("r", encoding="gb18030", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        if any(column in header for column in LABEL_COLUMNS):
            raise ValueError(f"outcome/label columns are forbidden in warmup inputs: {path}")
        indices = {column: header.index(column) for column in [date_column, *columns]}
        for line_number, row in enumerate(reader, start=2):
            if len(row) != len(header):
                raise ValueError(f"malformed CSV row: {path}:{line_number}")
            try:
                current = _parse_date(row[indices[date_column]])
            except ValueError as exc:
                raise ValueError(f"invalid source date: {path}:{line_number}") from exc
            if first_date is None:
                first_date = current
            if previous is not None and current < previous:
                raise ValueError(f"nonchronological source requires fail-closed review: {path}:{line_number}")
            if current > end:
                boundary = current
                break
            previous = current
            scanned += 1
            selected.append([row[indices[column]] for column in columns] + [current.isoformat()])
    frame = pd.DataFrame(selected, columns=[*columns, "_authorized_date"], dtype="string")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    filtered = table.filter(pc.and_(
        pc.greater_equal(table["_authorized_date"], pa.scalar(start.isoformat())),
        pc.less_equal(table["_authorized_date"], pa.scalar(end.isoformat())),
    )).to_pandas()
    record = {
        "path": _display_path(path),
        "first_quote_date": first_date.isoformat() if first_date else None,
        "first_excluded_date": boundary.isoformat() if boundary else None,
        "scanned_pre_boundary_rows": scanned,
        "arrow_rows": len(filtered),
        "arrow_date_filter": [f"_authorized_date >= {start.isoformat()}",
                              f"_authorized_date <= {end.isoformat()}"],
        "stream_stopped_before_first_post_boundary_row": boundary is not None,
        "full_source_table_materialized": False,
        "post_boundary_quote_values_retained": False,
        "outcome_columns_requested": False,
    }
    return filtered, record


def _read_calendar(paths: list[Path], start: dt.date, end: dt.date,
                   data_root: Path) -> tuple[list[str], dict[str, dict], set[str]]:
    calendar = set()
    records = {}
    for path in paths:
        frame, record = _bounded_csv(path, ["证券代码", "交易时间"], start, end, data_root)
        record["sha256"] = sha256_file(path)
        records[path.name] = record
        calendar.update(frame["_authorized_date"].dropna().tolist())
    return sorted(calendar), records, set(calendar)


def _read_status(path: Path, symbols: list[str], prior_dates: set[str], start: dt.date,
                 end: dt.date, data_root: Path) -> tuple[pd.DataFrame, dict]:
    with path.open("r", encoding="gb18030", newline="") as handle:
        header = next(csv.reader(handle))
    available = [symbol for symbol in symbols if symbol in header]
    frame, record = _bounded_csv(path, ["日期", *available], start, end, data_root, date_column="日期")
    record["missing_symbol_columns"] = sorted(set(symbols) - set(available))
    record["missing_status_policy"] = "unknown_not_false"
    if frame["_authorized_date"].duplicated().any():
        raise ValueError("duplicate ST source date")
    frame = frame.loc[frame["_authorized_date"].isin(prior_dates)].copy()
    if frame["_authorized_date"].duplicated().any():
        raise ValueError("duplicate retained ST source date")
    record["sha256"] = sha256_file(path)
    record["retained_prior_signal_rows"] = len(frame)
    return frame.set_index("_authorized_date", drop=False), record


def _read_actions(path: Path, start: dt.date, end: dt.date,
                  data_root: Path) -> tuple[list[str] | None, dict | None]:
    if not path.is_file():
        return None, None
    frame, record = _bounded_csv(path, ["日期"], start, end, data_root, date_column="日期")
    record["sha256"] = sha256_file(path)
    return sorted(frame["_authorized_date"].dropna().tolist()), record


def _predictor_frame(path: Path, symbol: str, calendar: list[str], signals: list[str],
                     status: pd.DataFrame, actions: list[str] | None, first_quote: str,
                     start: dt.date, end: dt.date, data_root: Path,
                     feature_names: tuple[str, ...]) -> pd.DataFrame:
    raw, _ = _bounded_csv(path, ["证券代码", "交易时间", *[value[0] for value in FIELDS.values()]],
                          start, end, data_root)
    raw = raw.drop(columns="_authorized_date")
    if raw.empty:
        return pd.DataFrame()
    clean, stats = normalize(raw, symbol, start, end, set())
    if stats["duplicate_rows"] or stats["identity_conflict_rows"] or stats["malformed_date_rows"]:
        raise ValueError(f"source identity or duplicate failure: {path}: {stats}")
    clean = clean.loc[clean.date.isin(calendar)]
    if clean.empty:
        return pd.DataFrame()
    q = clean.sort_values("date").set_index("date")
    prices = q[["open", "high", "low", "close"]]
    good = (prices.gt(0).all(axis=1) & q.high.ge(prices[["open", "close", "low"]].max(axis=1))
            & q.low.le(prices[["open", "close", "high"]].min(axis=1)))
    q.loc[~good, ["open", "high", "low", "close", "reference_preclose"]] = np.nan
    result = compute_signal_panel(q.reset_index(), calendar, signals, wide=True)
    positions = {date: index for index, date in enumerate(calendar)}
    for factor in set(feature_names).intersection(PRICE_WINDOWS):
        width = PRICE_WINDOWS[factor]
        invalid = []
        for date in signals:
            first = calendar[max(0, positions[date] - width)]
            invalid.append(actions is None or bisect_right(actions, date) > bisect_right(actions, first))
        result.loc[invalid, factor] = np.nan
    first_position = bisect_left(calendar, first_quote)
    reasons = []
    source_dates = []
    ages = []
    for date in signals:
        position = positions[date]
        prior = calendar[position - 1] if position else None
        source_dates.append(prior)
        age = position - first_position + 1
        ages.append(age)
        state = None
        if prior in status.index and symbol in status.columns:
            value = status.loc[prior, symbol]
            state = None if pd.isna(value) else False if value == "False" else True if value == "True" else None
        reason = "eligible"
        if age < 60 or date not in q.index:
            reason = "absent_quote_or_short_observed_history"
        elif symbol.endswith(".BJ") and date < "2021-11-15":
            reason = "before_BSE_equity_market"
        elif state is None:
            reason = "unknown_ST"
        elif state:
            reason = "ST"
        elif not bool(good.get(date, False)) or not q.loc[date, "volume_shares"] > 0:
            reason = "invalid_or_nontrading_quote"
        reasons.append(reason)
    result["research_mask"] = [reason == "eligible" for reason in reasons]
    result["universe_reason"] = reasons
    result["status_source_date"] = source_dates
    result["observed_listing_age"] = ages
    result["log_market_cap"] = np.log(
        q.total_market_cap_CNY.where(q.total_market_cap_CNY.gt(0))).reindex(signals).to_numpy()
    result["forward_return"] = np.nan
    result["label_reason"] = "NOT_READ_PREDICTOR_ONLY_WARMUP"
    result["label_entry_date"] = None
    result["label_exit_date"] = pd.NaT
    result["date"] = pd.to_datetime(result["date"])
    return result


def _availability(frames: list[pd.DataFrame], features: tuple[str, ...], signals: list[str]) -> dict:
    if not frames:
        return {"features": {}, "all_required_predictor_weeks_pass": False}
    panel = pd.concat(frames, ignore_index=True)
    rows = {}
    for feature in features:
        counts = panel.loc[panel[feature].notna()].groupby("date").size()
        counts = counts.reindex(pd.DatetimeIndex(signals), fill_value=0)
        weeks = int((counts >= 30).sum())
        rows[feature] = {
            "weeks_with_at_least_30_nonmissing_predictors": weeks,
            "planned_weeks": len(signals),
            "coverage": weeks / len(signals),
            "nonmissing_cells": int(panel[feature].notna().sum()),
        }
    return {
        "scope": "predictor availability only; no outcome labels; warmup universe mask is not an evaluation filter",
        "features": rows,
        "all_required_predictor_weeks_pass": all(row["coverage"] >= 0.96 for row in rows.values()),
    }


def build_warmup(output_dir: Path, start: str = "2010-01-01", end: str = "2016-12-31",
                 warmup_weeks: int = 312, feature_names: tuple[str, ...] = DEFAULT_FEATURES,
                 symbols: list[str] | None = None, test_mode: bool = False,
                 data_root: Path | None = None) -> dict:
    start_date, end_date = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if end_date >= DEV_START or warmup_weeks < 1 or warmup_weeks > 312:
        raise ValueError("invalid authorized warmup window or length")
    if set(feature_names) - set(DEFAULT_FEATURES):
        raise ValueError("warmup feature contract must be a subset of the frozen nine predictors")
    data_root = Path(data_root or ROOT / "Data").resolve()
    if not test_mode and data_root != (ROOT / "Data").resolve():
        raise PermissionError("formal warmup accepts only the local Data root")
    output_dir = Path(output_dir).resolve()
    if output_dir.is_relative_to(data_root) or output_dir.is_relative_to(ROOT / "data_processed"):
        raise ValueError("derived warmup must be outside raw-data roots")
    if test_mode != output_dir.name.startswith("TEST_"):
        raise ValueError("TEST and formal warmup output names must be explicit")
    if not test_mode and symbols is not None:
        raise ValueError("formal warmup cannot select a convenient symbol subset")
    raw_root = data_root / "a股指标行情"
    paths = [raw_root / f"{symbol}.csv" for symbol in symbols] if symbols is not None else sorted(raw_root.glob("*.csv"))
    if not paths or any(not path.is_file() or not SYMBOL.fullmatch(path.stem) for path in paths):
        raise ValueError("missing or invalid raw quote sources")
    output_dir.mkdir(parents=True, exist_ok=False)
    protocol = {
        "schema": "AUTO-RESEARCH-LIVE-WARMUP-PREREG-1",
        "created_at": utc_now(),
        "authorized_raw_window": [start, end],
        "warmup_weeks": warmup_weeks,
        "feature_names": list(feature_names),
        "symbols": "TEST fixture subset" if test_mode else "all valid local quote CSVs",
        "authorization": "User explicitly authorized pre-2017 local predictor history on 2026-10-24; no pre-2017 outcomes",
        "label_columns_read": [],
        "labels_computed": False,
        "raw_read": "bounded chronological CSV stream; Arrow exact-window filters; stop before first post-boundary row",
        "calendar": "union of observed dates in authorized raw quote rows; not a certified exchange calendar",
        "coverage_min_predictor_weeks": 0.96,
        "score_window_read": False,
        "oos_window_read": False,
        "embargo_read": False,
    }
    write_immutable_json(output_dir / "preregistration.json", protocol)
    try:
        calendar, sources, calendar_dates = _read_calendar(paths, start_date, end_date, data_root)
        all_signals = weekly_signals(calendar, start, end)
        if len(all_signals) < warmup_weeks:
            raise ValueError(f"insufficient authorized weekly history: {len(all_signals)} < {warmup_weeks}")
        signals = all_signals[-warmup_weeks:]
        if calendar.index(signals[0]) < 60:
            raise ValueError("insufficient daily predictor history before first warmup signal")
        prior_dates = {calendar[calendar.index(date) - 1] for date in signals}
        status, status_record = _read_status(data_root / "ST信息.csv", [path.stem for path in paths],
                                            prior_dates, start_date, end_date, data_root)
        sources[status_record["path"]] = status_record
        frames = []
        parts = []
        empty = []
        action_records = {}
        for path in paths:
            source = sources[path.name]
            if not source["arrow_rows"]:
                empty.append(path.name)
                continue
            if sha256_file(path) != source["sha256"]:
                raise ValueError(f"raw source changed between passes: {path}")
            actions, action_record = _read_actions(data_root / "权息数据" / path.name,
                                                   start_date, end_date, data_root)
            if action_record is not None:
                action_records[action_record["path"]] = action_record
            frame = _predictor_frame(path, path.stem, calendar, signals, status, actions,
                                     source["first_quote_date"], start_date, end_date,
                                     data_root, feature_names)
            if frame.empty:
                empty.append(path.name)
                continue
            frames.append(frame)
            if len(frames) >= 100:
                target = output_dir / f"part-{len(parts):04d}.parquet"
                pd.concat(frames, ignore_index=True).to_parquet(target, index=False)
                parts.append({"path": target.name, "sha256": sha256_file(target)})
                frames = []
        if frames:
            target = output_dir / f"part-{len(parts):04d}.parquet"
            pd.concat(frames, ignore_index=True).to_parquet(target, index=False)
            parts.append({"path": target.name, "sha256": sha256_file(target)})
        all_frames = [pd.read_parquet(output_dir / part["path"]) for part in parts]
        availability = _availability(all_frames, feature_names, signals)
        sources.update(action_records)
        for path in paths:
            if sha256_file(path) != sources[path.name]["sha256"]:
                raise ValueError(f"raw source changed during build: {path}")
        status_name = "COMPLETED" if availability["all_required_predictor_weeks_pass"] else "COVERAGE_STOP"
        manifest = {
            "schema": "AUTO-RESEARCH-LIVE-WARMUP-1",
            "kind": "real_dev_warmup",
            "status": status_name,
            "created_at": utc_now(),
            "test_only": test_mode,
            "authorized_raw_window": [start, end],
            "signal_dates": signals,
            "warmup_weeks": warmup_weeks,
            "feature_names": list(feature_names),
            "columns": list(all_frames[0].columns) if all_frames else [],
            "parts": parts,
            "sources": sources,
            "source_calendar_sha256": sha256_json(calendar_dates),
            "empty_history_sources": empty,
            "source_files": len(paths),
            "rows": sum(len(frame) for frame in all_frames),
            "availability": availability,
            "raw_label_columns_read": [],
            "labels_computed": False,
            "placeholder_labels_are_not_observations": True,
            "forward_return_all_missing": True,
            "strict_pit_certified": False,
            "source_clock_assumption": True,
            "pit_safe_dates": [],
            "assumption_label": "real_terminal_label",
            "historical_claims_read": False,
            "kg_read": False,
            "score_window_read": False,
            "oos_window_read": False,
            "embargo_read": False,
            "code_hashes": {name: sha256_file(ROOT / name) for name in (
                "auto_research/agents/live_warmup.py", "auto_research/agents/live_manifest.py",
                "research/local_quote_library.py", "research/price_volume_batch.py", "research/mentor_clean.py",
            )},
            "preregistration_sha256": sha256_file(output_dir / "preregistration.json"),
        }
        write_immutable_json(output_dir / "manifest.json", manifest)
        if status_name != "COMPLETED":
            raise RuntimeError("warmup predictor coverage below 96%; frozen COVERAGE_STOP manifest retained")
        return manifest
    except Exception as exc:
        if not (output_dir / "failure.json").exists():
            write_immutable_json(output_dir / "failure.json", {
                "created_at": utc_now(), "status": "FAILED_OR_COVERAGE_STOP", "error": str(exc),
                "raw_label_columns_read": [], "labels_computed": False,
                "score_window_read": False, "oos_window_read": False, "embargo_read": False,
            })
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", default="2010-01-01")
    parser.add_argument("--end", default="2016-12-31")
    parser.add_argument("--weeks", type=int, default=312)
    args = parser.parse_args()
    manifest = build_warmup(args.output, start=args.start, end=args.end, warmup_weeks=args.weeks)
    print(json.dumps({key: manifest[key] for key in ("status", "source_files", "rows", "warmup_weeks",
                                                    "score_window_read", "oos_window_read")}, indent=1))


if __name__ == "__main__":
    main()
