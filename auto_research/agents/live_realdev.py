#!/usr/bin/env python3
"""Frozen dev-only temporal folds and lazy-audit bundles for live research loops."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
from agents.live_data import (
    DEV_END,
    DEV_START,
    META_COLUMNS,
    SyntheticBundle,
    load_real_dev_snapshot,
    panel_sha256,
)
from agents.live_manifest import read_json, sha256_file, sha256_json, write_immutable_json
from research.local_quote_library import trading_calendar

DEFAULT_PANEL = ROOT / "data_processed/local_quote_library_2017_2025_v1"
DEFAULT_ARCHIVE = ROOT / "Data/股票历史分钟行情数据"


def _derived_path(value: str | Path, test_only: bool) -> Path:
    path = Path(value).resolve()
    if any(path.is_relative_to(root) for root in (ROOT / "Data", ROOT / "data_processed")):
        raise PermissionError("derived snapshots cannot replace raw inputs")
    if not test_only and not path.is_relative_to(ROOT / "auto_research"):
        raise PermissionError("formal derived inputs must be inside auto_research")
    return path


def make_fold_calendar(panel_dir: Path = DEFAULT_PANEL, archive_root: Path = DEFAULT_ARCHIVE,
                       initial_search_weeks: int = 23, audit_weeks: int = 22,
                       n_blocks: int = 8, agent_replicates: int = 4,
                       trading_dates: Sequence[str] | None = None,
                       test_only: bool = False) -> dict:
    if (initial_search_weeks, audit_weeks, n_blocks, agent_replicates) != (23, 22, 8, 4):
        raise ValueError("the frozen design uses 23 initial search weeks, eight 22-week audits and four agent pairs")
    if trading_dates is not None and not test_only:
        raise ValueError("injected trading calendars are TEST-only")
    panel_dir = Path(panel_dir).resolve()
    manifest_path = panel_dir / "manifest.json"
    source = read_json(manifest_path)
    if not test_only and panel_dir == DEFAULT_PANEL:
        registered = read_json(ROOT / "config/active_mining_inputs.json")
        if sha256_file(manifest_path) != registered["quote_manifest_sha256"]:
            raise ValueError("quote panel manifest differs from the registered active mining input")
    dates = sorted(str(value)[:10] for value in source["signal_dates"]
                   if DEV_START.date().isoformat() <= str(value)[:10] <= DEV_END.date().isoformat())
    if len(dates) != 200 or len(set(dates)) != len(dates):
        raise ValueError("expected exactly 200 unique dev weekly signal dates")
    calendar = sorted(set(trading_dates or trading_calendar(archive_root, DEV_END.date().isoformat())))
    calendar = [value for value in calendar if DEV_START.date().isoformat() <= value <= DEV_END.date().isoformat()]
    if any(date not in calendar for date in dates):
        raise ValueError("signal date absent from the frozen trading calendar")

    def entry(signal_date: str) -> str:
        position = calendar.index(signal_date)
        if position + 1 >= len(calendar):
            raise ValueError("no dev trading session after signal")
        return calendar[position + 1]

    def exit_for_index(index: int) -> str:
        if index + 1 >= len(dates):
            raise ValueError("refusing to construct a label crossing the dev boundary")
        return entry(dates[index + 1])

    used_count = initial_search_weeks + n_blocks * audit_weeks
    expected = {
        dates[index]: {"label_entry_date": entry(dates[index]), "label_exit_date": exit_for_index(index)}
        for index in range(used_count)
    }
    folds = []
    for block in range(n_blocks):
        audit_start = initial_search_weeks + block * audit_weeks
        audit_end = audit_start + audit_weeks
        search_dates = dates[:audit_start]
        audit_dates = dates[audit_start:audit_end]
        if len(search_dates) < 20 or len(audit_dates) != audit_weeks:
            raise ValueError("insufficient planned search or audit weeks")
        search_labels = {date: expected[date] for date in search_dates}
        audit_labels = {date: expected[date] for date in audit_dates}
        search_label_end = search_labels[search_dates[-1]]["label_exit_date"]
        audit_label_end = audit_labels[audit_dates[-1]]["label_exit_date"]
        if search_label_end != audit_labels[audit_dates[0]]["label_entry_date"]:
            raise ValueError("search and audit label boundaries are not aligned")
        if audit_label_end > DEV_END.date().isoformat():
            raise ValueError("audit label crosses the dev boundary")
        split = np.array_split(pd.DatetimeIndex(audit_dates), 2)
        folds.append({
            "fold_id": f"fold_{block:02d}",
            "search_dates": search_dates,
            "audit_dates": audit_dates,
            "search_snapshot_start": "2017-01-01",
            "search_signal_end": search_dates[-1],
            "search_label_end": search_label_end,
            "audit_signal_start": audit_dates[0],
            "audit_signal_end": audit_dates[-1],
            "audit_label_end": audit_label_end,
            "search_expected_labels": search_labels,
            "audit_expected_labels": audit_labels,
            "audit_folds": {
                f"fold_{index:02d}": [str(value.date()) for value in part]
                for index, part in enumerate(split)
            },
            "search_history_overlap": True,
            "independent_market_samples": 1,
        })
    return {
        "schema": "AUTO-RESEARCH-LIVE-REALDEV-CALENDAR-1",
        "test_only": test_only,
        "initial_search_weeks": initial_search_weeks,
        "audit_weeks": audit_weeks,
        "n_blocks": n_blocks,
        "agent_replicates": agent_replicates,
        "n_matched_pairs": n_blocks * agent_replicates,
        "folds": folds,
        "unused_dev_signal_dates": dates[used_count:],
        "unused_reason": "Excluded before outcomes to preserve eight complete dev-matured 22-week audit blocks",
        "label_interval": "next-session open to next-week next-session open; half-open holding-return intervals",
        "adjacent_boundary": "search/audit and adjacent audits may share an opening-price endpoint, never a holding-return interval",
        "purge_rule": "signal dates are disjoint; search label_exit<=first audit label_entry; audit label_exit<=next audit label_entry or dev end",
        "search_validation": "IC/ICIR gate on the same expanding search history; not a separate temporal holdout",
        "history_weeks": 312,
        "history_label_policy": "pre-2017 predictor-only; pre-audit dev history has labels disabled in the audit panel",
        "temporal_clusters": n_blocks,
        "agent_seed_pairs_are_not_independent_markets": True,
        "independent_oos": False,
        "real_data_read": False,
        "outcome_rows_read": False,
        "score_window_read": False,
        "oos_window_read": False,
        "embargo_read": False,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": sha256_file(manifest_path),
        "source_calendar_sha256": sha256_json(calendar),
    }


def _load_warmup(reference: dict, features: tuple[str, ...], test_only: bool) -> tuple[pd.DataFrame, dict]:
    directory = _derived_path(reference["path"], test_only)
    manifest_path = directory / "manifest.json"
    if sha256_file(manifest_path) != reference["manifest_sha256"]:
        raise ValueError("warmup manifest hash mismatch")
    manifest = read_json(manifest_path)
    if (manifest.get("kind") != "real_dev_warmup" or manifest.get("status") != "COMPLETED"
            or manifest.get("warmup_weeks", 312) != 312
            or manifest.get("labels_computed") is not False or manifest.get("raw_label_columns_read") != []
            or manifest.get("forward_return_all_missing") is not True):
        raise ValueError("warmup is not a completed predictor-only snapshot")
    columns = list(dict.fromkeys([*sorted(META_COLUMNS), *features]))
    frames = []
    for part in manifest["parts"]:
        path = (directory / part["path"]).resolve()
        if not path.is_relative_to(directory) or sha256_file(path) != part["sha256"]:
            raise ValueError("warmup part path or hash mismatch")
        frames.append(pd.read_parquet(path, columns=columns,
                                      filters=[("date", "<", pd.Timestamp("2017-01-01"))]))
    if not frames:
        raise ValueError("empty warmup snapshot")
    panel = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    if panel.empty or panel["date"].max() >= DEV_START or panel.duplicated(["date", "symbol"]).any():
        raise ValueError("warmup date or key boundary violation")
    if panel["forward_return"].notna().any() or not panel["label_reason"].eq("NOT_READ_PREDICTOR_ONLY_WARMUP").all():
        raise ValueError("warmup contains an outcome-like value")
    actual_hash = panel_sha256(panel)
    if reference.get("panel_sha256") is not None and actual_hash != reference["panel_sha256"]:
        raise ValueError("warmup semantic panel hash mismatch")
    return panel, {"path": str(directory), "manifest_sha256": reference["manifest_sha256"],
                   "panel_sha256": actual_hash}


def _load_snapshot(reference: dict, expected_dates: list[str], label_end: str,
                   expected_labels: dict, test_only: bool) -> tuple[pd.DataFrame, dict]:
    directory = _derived_path(reference["path"], test_only)
    manifest_path = directory / "manifest.json"
    if sha256_file(manifest_path) != reference["manifest_sha256"]:
        raise ValueError("dev snapshot manifest hash mismatch")
    manifest = read_json(manifest_path)
    if (manifest.get("kind") != "real_dev_snapshot" or manifest.get("score_window_read") is not False
            or manifest.get("oos_window_read") is not False or manifest.get("embargo_read") is not False
            or manifest.get("label_exit_cutoff") != label_end
            or manifest.get("expected_label_calendar_sha256") != sha256_json(expected_labels)):
        raise ValueError("dev snapshot access or label contract mismatch")
    panel_path = directory / "panel.parquet"
    if sha256_file(panel_path) != manifest["panel_file_sha256"]:
        raise ValueError("dev snapshot file hash mismatch")
    panel = pd.read_parquet(panel_path, columns=manifest["columns"], filters=[
        ("date", ">=", pd.Timestamp(DEV_START)), ("date", "<=", pd.Timestamp(DEV_END)),
    ])
    actual_dates = [str(value.date()) for value in pd.DatetimeIndex(sorted(panel["date"].unique()))]
    if actual_dates != expected_dates or panel.duplicated(["date", "symbol"]).any():
        raise ValueError("dev snapshot dates or keys differ from the frozen fold")
    if panel_sha256(panel) != manifest["panel_sha256"]:
        raise ValueError("dev snapshot semantic panel hash mismatch")
    exit_dates = pd.to_datetime(panel["label_exit_date"])
    if (panel["forward_return"].notna() & exit_dates.gt(pd.Timestamp(label_end))).any():
        raise ValueError("snapshot contains a label beyond its frozen boundary")
    return panel, {"path": str(directory), "manifest_sha256": reference["manifest_sha256"],
                   "panel_sha256": manifest["panel_sha256"]}


def _history_without_labels(panel: pd.DataFrame) -> pd.DataFrame:
    history = panel.copy()
    history["forward_return"] = np.nan
    history["label_reason"] = "HISTORY_LABELS_NOT_USED"
    history["label_entry_date"] = pd.NaT
    history["label_exit_date"] = pd.NaT
    return history


def load_real_dev_bundle(config: dict) -> SyntheticBundle:
    if config.get("kind") != "real_dev":
        raise ValueError("not a real-dev bundle configuration")
    test_only = bool(config.get("test_only"))
    calendar_reference = config["fold_calendar"]
    calendar_path = _derived_path(calendar_reference["path"], test_only)
    if sha256_file(calendar_path) != calendar_reference["sha256"]:
        raise ValueError("fold calendar hash mismatch")
    calendar = read_json(calendar_path)
    fold = next((row for row in calendar["folds"] if row["fold_id"] == config["fold_id"]), None)
    if fold is None:
        raise ValueError("fold or feature contract mismatch")
    features = tuple(config["feature_names"])
    if not features or len(set(features)) != len(features):
        raise ValueError("real-dev feature names must be non-empty and unique")
    warmup, warmup_reference = _load_warmup(config["warmup"], features, test_only)
    search, search_reference = _load_snapshot(config["search_snapshot"], fold["search_dates"],
                                              fold["search_label_end"], fold["search_expected_labels"], test_only)
    search_panel = pd.concat([warmup, search], ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)
    actual_features = {column for column in search_panel.columns
                       if column not in META_COLUMNS and pd.api.types.is_numeric_dtype(search_panel[column])}
    if actual_features != set(features):
        raise ValueError("combined panel features differ from the frozen contract")
    if search_panel.duplicated(["date", "symbol"]).any():
        raise ValueError("search warmup/current key overlap")
    if panel_sha256(search_panel) != config["expected_panel_hashes"]["search"]:
        raise ValueError("combined search panel hash mismatch")
    audit_reference = config["audit_snapshot"]

    def audit_loader(seed: int) -> pd.DataFrame:
        if seed != 0:
            raise ValueError("real-dev has one market panel, indexed as seed zero")
        audit, _ = _load_snapshot(audit_reference, fold["audit_dates"], fold["audit_label_end"],
                                  fold["audit_expected_labels"], test_only)
        panel = pd.concat([warmup, _history_without_labels(search), audit], ignore_index=True)
        panel = panel.sort_values(["date", "symbol"]).reset_index(drop=True)
        if panel.duplicated(["date", "symbol"]).any():
            raise ValueError("audit history/current key overlap")
        if panel_sha256(panel) != config["expected_panel_hashes"]["audit"]:
            raise ValueError("combined audit panel hash mismatch")
        return panel

    hashes = {
        "warmup_panel_sha256": config["expected_panel_hashes"]["warmup"],
        "search_panel_sha256": config["expected_panel_hashes"]["search"],
        "audit_panel_sha256": {"0": config["expected_panel_hashes"]["audit"]},
        "data_config_sha256": sha256_json(config),
    }
    hashes["combined_sha256"] = sha256_json(hashes)
    manifest = {
        "schema": "AUTO-RESEARCH-LIVE-DATA-1",
        "kind": "real_dev",
        "fold_id": config["fold_id"],
        "feature_names": list(features),
        "warmup_weeks": 312,
        "search_dates": [fold["search_dates"][0], fold["search_dates"][-1]],
        "search_date_count": len(fold["search_dates"]),
        "audit_dates": {"0": [fold["audit_dates"][0], fold["audit_dates"][-1]]},
        "audit_fold_dates": {"0": {name: [dates[0], dates[-1]] for name, dates in fold["audit_folds"].items()}},
        "source_snapshots": {"warmup": warmup_reference, "search": search_reference, "audit": audit_reference},
        "hashes": hashes,
        "lazy_audit_loading": True,
        "audit_feedback_to_search": False,
        "search_validation": calendar["search_validation"],
        "overlapping_expanding_search_history": True,
        "temporal_cluster": config["fold_id"],
        "independent_market_samples": 1,
        "independent_evaluation_eligible": False,
        "independent_oos": False,
        "real_data_read": True,
        "score_window_read": False,
        "oos_window_read": False,
        "embargo_read": False,
        "historical_claims_read": False,
        "kg_read": False,
        "strict_pit_certified": False,
        "source_clock_assumption": True,
        "pit_safe_dates": [],
        "assumption_label": "real_terminal_label",
    }
    audit_folds = {0: {name: pd.DatetimeIndex(dates) for name, dates in fold["audit_folds"].items()}}
    return SyntheticBundle(search_panel, pd.DatetimeIndex(fold["search_dates"]), {}, audit_folds,
                           manifest, audit_loader=audit_loader)


def materialize_real_fold(calendar_path: Path, warmup_dir: Path, output_dir: Path,
                          feature_names: tuple[str, ...], panel_dir: Path = DEFAULT_PANEL,
                          authorized_roots: Sequence[Path] | None = None,
                          preregistration: Path | None = None, fold_id: str = "fold_00") -> dict:
    calendar_path = Path(calendar_path).resolve()
    calendar = read_json(calendar_path)
    calendar_hash = sha256_file(calendar_path)
    test_only = bool(calendar.get("test_only"))
    prereg_record = None
    if not test_only:
        if preregistration is None or not Path(preregistration).is_file():
            raise PermissionError("formal real-data outcomes require a frozen preregistration before snapshot materialization")
        preregistration = Path(preregistration).resolve()
        prereg = read_json(preregistration)
        if prereg.get("real_dev_fold_calendar_sha256") != calendar_hash:
            raise ValueError("preregistration does not bind this fold calendar")
        prereg_record = {"path": str(preregistration), "sha256": sha256_file(preregistration)}
    warmup_dir = _derived_path(warmup_dir, test_only)
    warmup_manifest_path = warmup_dir / "manifest.json"
    warmup_reference = {"path": str(warmup_dir), "manifest_sha256": sha256_file(warmup_manifest_path)}
    output_dir = _derived_path(output_dir, test_only)
    output_dir.mkdir(parents=True, exist_ok=False)
    fold = next((row for row in calendar["folds"] if row["fold_id"] == fold_id), None)
    if fold is None:
        raise ValueError(f"unknown real-dev fold: {fold_id}")
    search, search_manifest = load_real_dev_snapshot(
        panel_dir, feature_names, start=fold["search_snapshot_start"], end=fold["search_signal_end"],
        snapshot_dir=output_dir / "search", authorized_roots=authorized_roots,
        assumption_label="real_terminal_label", label_end=fold["search_label_end"],
        expected_label_dates=fold["search_expected_labels"],
    )
    audit, audit_manifest = load_real_dev_snapshot(
        panel_dir, feature_names, start=fold["audit_signal_start"], end=fold["audit_signal_end"],
        snapshot_dir=output_dir / "audit", authorized_roots=authorized_roots,
        assumption_label="real_terminal_label", label_end=fold["audit_label_end"],
        expected_label_dates=fold["audit_expected_labels"],
    )
    for snapshot_manifest in (search_manifest, audit_manifest):
        if snapshot_manifest["source_manifest_sha256"] != calendar["source_manifest_sha256"]:
            raise ValueError("source panel manifest drifted after fold-calendar freezing")
    warmup, _ = _load_warmup(warmup_reference, feature_names, test_only)
    warmup_hash = panel_sha256(warmup)
    search_hash = panel_sha256(pd.concat([warmup, search], ignore_index=True)
                               .sort_values(["date", "symbol"]).reset_index(drop=True))
    audit_panel = pd.concat([warmup, _history_without_labels(search), audit], ignore_index=True)
    audit_hash = panel_sha256(audit_panel.sort_values(["date", "symbol"]).reset_index(drop=True))
    config = {
        "schema": "AUTO-RESEARCH-LIVE-REALDEV-INPUT-1",
        "kind": "real_dev",
        "test_only": test_only,
        "fold_id": fold["fold_id"],
        "feature_names": list(feature_names),
        "fold_calendar": {"path": str(calendar_path), "sha256": calendar_hash},
        "warmup": {**warmup_reference, "panel_sha256": warmup_hash},
        "search_snapshot": {"path": str(output_dir / "search"),
                            "manifest_sha256": sha256_file(output_dir / "search/manifest.json")},
        "audit_snapshot": {"path": str(output_dir / "audit"),
                           "manifest_sha256": sha256_file(output_dir / "audit/manifest.json")},
        "expected_panel_hashes": {"warmup": warmup_hash, "search": search_hash, "audit": audit_hash},
        "initial_library": list(feature_names),
        "n_audit_folds": 2,
        "search_validation": calendar["search_validation"],
        "preregistration": prereg_record,
        "score_window_read": False,
        "oos_window_read": False,
        "embargo_read": False,
    }
    write_immutable_json(output_dir / "data_config.json", config)
    return config
