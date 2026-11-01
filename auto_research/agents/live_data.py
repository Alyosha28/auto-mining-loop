#!/usr/bin/env python3
"""Synthetic panels and fail-closed development-only real-data snapshots."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_manifest import sha256_file, sha256_json, write_immutable_json

DEV_START = pd.Timestamp("2017-01-01")
DEV_END = pd.Timestamp("2020-11-30")
META_COLUMNS = {
    "date", "symbol", "research_mask", "forward_return", "log_market_cap", "universe_reason",
    "label_reason", "label_entry_date", "label_exit_date", "status_source_date", "observed_listing_age",
}


@dataclass
class SyntheticBundle:
    search_panel: pd.DataFrame
    search_dates: pd.DatetimeIndex
    audit_panels: dict[int, pd.DataFrame]
    audit_folds: dict[int, dict[str, pd.DatetimeIndex]]
    manifest: dict
    audit_loader: Callable[[int], pd.DataFrame] | None = None

    @property
    def data_hashes(self) -> dict:
        return self.manifest["hashes"]

    def get_audit_panel(self, seed: int) -> pd.DataFrame:
        if seed not in self.audit_panels:
            if self.audit_loader is None:
                raise KeyError(f"no audit panel or loader for seed {seed}")
            self.audit_panels[seed] = self.audit_loader(seed)
        return self.audit_panels[seed]


@lru_cache(maxsize=1)
def _benchmark_core():
    path = ROOT / "auto_research/benchmark/bench/core.py"
    spec = importlib.util.spec_from_file_location("live_qd_benchmark_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def panel_sha256(panel: pd.DataFrame) -> str:
    ordered = panel.sort_values(["date", "symbol"], kind="mergesort").reset_index(drop=True)
    digest = hashlib.sha256()
    digest.update(json.dumps(list(ordered.columns)).encode("utf-8"))
    digest.update(json.dumps([str(dtype) for dtype in ordered.dtypes]).encode("utf-8"))
    for column in ordered.columns:
        values = ordered[column]
        if pd.api.types.is_datetime64_any_dtype(values):
            values = values.astype("int64")
        digest.update(column.encode("utf-8"))
        digest.update(pd.util.hash_pandas_object(values, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def _date_set(values) -> set[pd.Timestamp]:
    return {pd.Timestamp(value) for value in values}


def coverage_report(evaluable_dates, planned_dates, pit_safe_dates=(), assumption_dates=None,
                    assumption_label="synthetic_terminal_label") -> dict:
    evaluable = _date_set(evaluable_dates)
    planned = _date_set(planned_dates)
    pit = _date_set(pit_safe_dates) & planned
    assumption = planned - pit if assumption_dates is None else _date_set(assumption_dates) & planned

    def layer(dates: set[pd.Timestamp], label: str, certified: bool) -> dict:
        count = len(evaluable & dates)
        return {
            "label": label,
            "pit_certified": certified,
            "evaluable_weeks": count,
            "planned_weeks": len(dates),
            "coverage": count / len(dates) if dates else None,
        }

    return {
        "schema": "AUTO-RESEARCH-LIVE-COVERAGE-1",
        "overall": {
            "evaluable_weeks": len(evaluable & planned),
            "planned_weeks": len(planned),
            "coverage": len(evaluable & planned) / len(planned) if planned else None,
        },
        "pit_safe": layer(pit, "PIT-safe", True),
        "assumption_layer": layer(assumption, assumption_label, False),
        "note": "PIT-safe and assumption-layer coverage are disjoint; synthetic panels make no market PIT claim.",
    }


def _range(dates) -> list[str | None]:
    dates = pd.DatetimeIndex(dates)
    return [str(dates.min().date()), str(dates.max().date())] if len(dates) else [None, None]


DEFAULT_WARMUP_WEEKS = 24 * 13


def _synthetic_panel(core, task: str, seed: int, n_symbols: int, n_weeks: int,
                     n_features: int, warmup_weeks: int) -> pd.DataFrame:
    panel = core.make_task_panel(task, int(seed), n_symbols=n_symbols,
                                 n_weeks=n_weeks + warmup_weeks, n_features=n_features)
    if warmup_weeks:
        panel["date"] = panel["date"] - pd.to_timedelta(7 * warmup_weeks, unit="D")
    return panel


def make_synthetic_bundle(task: str, search_seed: int, audit_seeds: Sequence[int], n_symbols: int = 40,
                          n_weeks: int = 32, n_features: int = 8, search_fraction: float = 0.75,
                          n_audit_folds: int = 2, warmup_weeks: int = DEFAULT_WARMUP_WEEKS) -> SyntheticBundle:
    if len(set(audit_seeds)) != len(audit_seeds) or not audit_seeds:
        raise ValueError("audit seeds must be non-empty and unique")
    if search_seed in audit_seeds:
        raise ValueError("audit panels must use fresh seeds distinct from the search panel")
    if n_symbols < 31 or n_weeks < 8 or n_features < 2 or n_audit_folds < 1 or warmup_weeks < 0:
        raise ValueError("synthetic panel is too small for the IC/coverage contract")
    core = _benchmark_core()
    search_panel = _synthetic_panel(core, task, search_seed, n_symbols, n_weeks, n_features, warmup_weeks)
    dates = pd.DatetimeIndex(sorted(search_panel["date"].unique()))
    evaluation_dates = dates[warmup_weeks:]
    cut = int(round(n_weeks * search_fraction))
    if cut < 2 or cut > n_weeks or len(evaluation_dates) != n_weeks:
        raise ValueError("invalid synthetic search split")
    search_dates = evaluation_dates[:cut]
    audit_panels: dict[int, pd.DataFrame] = {}
    audit_folds: dict[int, dict[str, pd.DatetimeIndex]] = {}
    for seed in audit_seeds:
        panel = _synthetic_panel(core, task, seed, n_symbols, n_weeks, n_features, warmup_weeks)
        audit_panels[int(seed)] = panel
        audit_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))[warmup_weeks:]
        if n_audit_folds > len(audit_dates):
            raise ValueError("more audit folds than dates")
        audit_folds[int(seed)] = {
            f"fold_{index:02d}": pd.DatetimeIndex(part)
            for index, part in enumerate(np.array_split(audit_dates, n_audit_folds))
        }
    config = {
        "kind": "synthetic",
        "task": task,
        "search_seed": int(search_seed),
        "audit_seeds": [int(seed) for seed in audit_seeds],
        "n_symbols": int(n_symbols),
        "n_weeks": int(n_weeks),
        "n_features": int(n_features),
        "search_fraction": float(search_fraction),
        "n_audit_folds": int(n_audit_folds),
        "warmup_weeks": int(warmup_weeks),
        "warmup_limit": "history only; covers floor(warmup_weeks/13) successive maximum temporal shifts, not arbitrary ASTs",
    }
    hashes = {
        "synthetic_config_sha256": sha256_json(config),
        "search_panel_sha256": panel_sha256(search_panel),
        "audit_panel_sha256": {str(seed): panel_sha256(panel) for seed, panel in audit_panels.items()},
    }
    hashes["combined_sha256"] = sha256_json(hashes)
    manifest = {
        "schema": "AUTO-RESEARCH-LIVE-DATA-1",
        **config,
        "panel_dates": _range(dates),
        "history_dates": _range(dates[:warmup_weeks]),
        "search_dates": _range(search_dates),
        "audit_dates": {str(seed): _range(pd.DatetimeIndex(sorted(panel["date"].unique()))[warmup_weeks:])
                        for seed, panel in audit_panels.items()},
        "audit_fold_dates": {str(seed): {fold: _range(fold_dates) for fold, fold_dates in folds.items()}
                             for seed, folds in audit_folds.items()},
        "hashes": hashes,
        "coverage": coverage_report(search_dates, search_dates),
        "historical_claims_read": False,
        "kg_read": False,
        "real_data_read": False,
        "score_window_read": False,
        "oos_window_read": False,
    }
    return SyntheticBundle(search_panel, search_dates, audit_panels, audit_folds, manifest)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_real_dev_snapshot(panel_dir: Path, columns: Sequence[str], start: str = "2017-01-01",
                           end: str = "2020-11-30", snapshot_dir: Path | None = None,
                           authorized_roots: Sequence[Path] | None = None,
                           pit_safe_dates=(), assumption_label="terminal_label",
                           label_end: str | None = None,
                           expected_label_dates: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Read authorized dev parts with Arrow filters; never call this for synthetic TEST smoke."""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    label_end_ts = pd.Timestamp(label_end or end)
    if (start_ts < DEV_START or end_ts > DEV_END or start_ts > end_ts
            or label_end_ts < start_ts or label_end_ts > DEV_END):
        raise ValueError("real-data predictors and labels are restricted to 2017-01-01..2020-11-30")
    base = Path(panel_dir).resolve()
    roots = [Path(path).resolve() for path in (authorized_roots or (ROOT / "Data", ROOT / "data_processed"))]
    if not any(_inside(base, root) for root in roots):
        raise PermissionError("panel_dir is outside authorized local Data/data_processed roots")
    if snapshot_dir is not None and any(_inside(Path(snapshot_dir).resolve(), root) for root in roots):
        raise ValueError("derived snapshots must not overwrite or live inside raw-data roots")
    manifest_path = base / "manifest.json"
    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    feature_columns = [column for column in columns if column not in META_COLUMNS]
    wanted = list(dict.fromkeys([*sorted(META_COLUMNS), *feature_columns]))
    date_filter = [("date", ">=", start), ("date", "<=", end)]
    outcome_filter = [*date_filter, ("label_exit_date", "<=", label_end_ts.date().isoformat())]
    frames = []
    part_records = []
    for part in source_manifest["parts"]:
        path = (base / part["path"]).resolve()
        if not _inside(path, base):
            raise ValueError("panel part escapes panel_dir")
        actual_hash = sha256_file(path)
        if actual_hash != part["sha256"]:
            raise ValueError(f"quote-panel part drift: {part['path']}")
        predictors = pd.read_parquet(
            path,
            columns=[column for column in wanted if column not in {"forward_return", "label_reason"}],
            filters=date_filter,
        )
        outcomes = pd.read_parquet(
            path,
            columns=["date", "symbol", "forward_return", "label_reason"],
            filters=outcome_filter,
        )
        frames.append(predictors.merge(outcomes, on=["date", "symbol"], how="left", validate="one_to_one"))
        part_records.append({"path": part["path"], "sha256": actual_hash})
    if not frames:
        raise ValueError("no source parts declared")
    panel = pd.concat(frames, ignore_index=True)
    if panel.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate market-panel key")
    if not pd.api.types.is_bool_dtype(panel.research_mask) or panel.research_mask.isna().any():
        raise ValueError("explicit universe eligibility required")
    panel["date"] = pd.to_datetime(panel["date"])
    panel["label_entry_date"] = pd.to_datetime(panel["label_entry_date"])
    panel["label_exit_date"] = pd.to_datetime(panel["label_exit_date"])
    bad_order = ((panel["label_entry_date"].notna() & panel["label_entry_date"].le(panel["date"]))
                 | (panel["label_exit_date"].notna() & panel["label_exit_date"].le(panel["label_entry_date"])))
    if bad_order.any():
        raise ValueError("label entry/exit ordering violation")
    used_label = panel["label_exit_date"].le(label_end_ts)
    panel["forward_return"] = panel["forward_return"].where(used_label)
    panel["label_reason"] = panel["label_reason"].where(used_label)
    panel = panel.loc[panel.research_mask].sort_values(["date", "symbol"]).reset_index(drop=True)
    used_label = panel["label_exit_date"].le(label_end_ts)
    used_exit = panel.loc[used_label & panel["forward_return"].notna(), "label_exit_date"]
    if len(used_exit) and used_exit.max() > label_end_ts:
        raise ValueError("label_exit_date crossed the frozen label boundary")
    planned_dates = pd.DatetimeIndex(sorted(panel["date"].unique()))
    if expected_label_dates is not None:
        actual_dates = {str(date.date()) for date in planned_dates}
        if actual_dates != set(expected_label_dates):
            raise ValueError("planned signal dates are missing from the research universe")
        for date in planned_dates:
            expected = expected_label_dates[str(date.date())]
            group = panel.loc[panel["date"].eq(date)]
            for column in ("label_entry_date", "label_exit_date"):
                expected_value = expected.get(column)
                expected_ts = pd.Timestamp(expected_value) if expected_value is not None else None
                actual = group[column].dropna().unique()
                if expected_ts is None and len(actual):
                    raise ValueError(f"unexpected {column} on {date.date()}")
                if expected_ts is not None and any(pd.Timestamp(value) != expected_ts for value in actual):
                    raise ValueError(f"label calendar mismatch for {column} on {date.date()}")
    metadata = {
        "schema": "AUTO-RESEARCH-LIVE-DATA-1",
        "kind": "real_dev_snapshot",
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_parts": part_records,
        "columns": list(panel.columns),
        "requested_columns": wanted,
        "arrow_filters": {
            "predictors": date_filter,
            "outcomes": outcome_filter,
        },
        "requested_window": [start, end],
        "label_exit_cutoff": label_end_ts.date().isoformat(),
        "expected_label_calendar_sha256": sha256_json(expected_label_dates) if expected_label_dates is not None else None,
        "actual_date_range": _range(planned_dates),
        "max_label_exit_date": str(panel["label_exit_date"].max().date()) if panel["label_exit_date"].notna().any() else None,
        "max_used_label_exit_date": str(used_exit.max().date()) if len(used_exit) else None,
        "labels_masked_after_cutoff": int((~used_label).sum()),
        "rows": len(panel),
        "panel_sha256": panel_sha256(panel),
        "coverage": coverage_report([], planned_dates, pit_safe_dates=pit_safe_dates,
                                    assumption_label=assumption_label),
        "strict_pit_certified": bool(len(set(pd.DatetimeIndex(pit_safe_dates))) > 0),
        "source_clock_assumption": True,
        "historical_claims_read": False,
        "kg_read": False,
        "real_data_read": True,
        "score_window_read": False,
        "oos_window_read": False,
        "embargo_read": False,
    }
    if snapshot_dir is not None:
        snapshot_dir = Path(snapshot_dir)
        snapshot_dir.mkdir(parents=True, exist_ok=False)
        panel.to_parquet(snapshot_dir / "panel.parquet", index=False)
        metadata["panel_file_sha256"] = sha256_file(snapshot_dir / "panel.parquet")
        write_immutable_json(snapshot_dir / "manifest.json", metadata)
    return panel, metadata
