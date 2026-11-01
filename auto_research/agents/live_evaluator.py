#!/usr/bin/env python3
"""Raw-IC evaluation with search-frozen direction and fail-closed coverage."""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "auto_research/scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research/scripts"))
if str(ROOT / "auto_research") not in sys.path:
    sys.path.insert(0, str(ROOT / "auto_research"))
import expr_search as es
from agents.live_data import coverage_report


class EvaluationError(RuntimeError):
    pass


class CoverageStop(EvaluationError):
    def __init__(self, phase: str, formula: str, coverage: float, n_dates: int, planned_dates: int,
                 detail: dict):
        super().__init__(f"coverage stop in {phase} for {formula}: {coverage:.6f} < 0.96")
        self.phase = phase
        self.formula = formula
        self.coverage = float(coverage)
        self.n_dates = int(n_dates)
        self.planned_dates = int(planned_dates)
        self.detail = detail


def finite(value) -> float | None:
    value = float(value)
    return value if math.isfinite(value) else None


class LiveEvaluator:
    def __init__(self, min_coverage: float = 0.96, search_min_ic: float | None = None,
                 search_min_icir: float | None = None, min_pairs: int | None = None,
                 pit_safe_dates=(), assumption_label="synthetic_terminal_label"):
        self.min_coverage = float(min_coverage)
        self.search_min_ic = float(es.EP.PASS_MIN_IC if search_min_ic is None else search_min_ic)
        self.search_min_icir = float(es.EP.PASS_MIN_ICIR if search_min_icir is None else search_min_icir)
        self.min_pairs = int(es.EP.MIN_IC_PAIRS if min_pairs is None else min_pairs)
        self.pit_safe_dates = tuple(pit_safe_dates)
        self.assumption_label = assumption_label
        self.calls = {"raw_ic": 0, "coverage_stops": 0}

    def _raw(self, node: dict, panel: pd.DataFrame, eval_dates, phase: str) -> tuple[pd.Series, pd.Series, dict]:
        dates = pd.DatetimeIndex(eval_dates)
        formula = es.canonical(node)
        if not len(dates):
            raise EvaluationError(f"no planned dates for {phase}: {formula}")
        values = es.eval_tree(node, panel)
        if values is None:
            raise EvaluationError(f"expression could not be evaluated: {formula}")
        values = pd.to_numeric(values, errors="coerce").replace([np.inf, -np.inf], np.nan)
        series, n_dates = es.EP.ic_series(panel, values, dates, self.min_pairs)
        coverage = float(n_dates / len(dates))
        detail = coverage_report(series.index, dates, pit_safe_dates=self.pit_safe_dates,
                                 assumption_label=self.assumption_label)
        self.calls["raw_ic"] += 1
        if coverage < self.min_coverage:
            self.calls["coverage_stops"] += 1
            raise CoverageStop(phase, formula, coverage, n_dates, len(dates), detail)
        if not len(series):
            raise EvaluationError(f"no evaluable IC dates: {formula}")
        raw_mean = float(series.mean())
        raw_std = float(series.std(ddof=1)) if len(series) > 1 else float("nan")
        raw_icir = raw_mean / raw_std if math.isfinite(raw_std) and raw_std > 0 else float("nan")
        metrics = {
            "formula": formula,
            "raw_mean_ic": raw_mean,
            "raw_icir": finite(raw_icir),
            "coverage": coverage,
            "n_dates": int(n_dates),
            "planned_dates": int(len(dates)),
            "coverage_detail": detail,
        }
        return values, series, metrics

    def _oriented(self, values: pd.Series, series: pd.Series, metrics: dict, panel: pd.DataFrame,
                  dates, direction: int) -> dict:
        oriented_values = values * int(direction)
        oriented_series = series * int(direction)
        signed_mean = float(oriented_series.mean())
        std = float(oriented_series.std(ddof=1)) if len(oriented_series) > 1 else float("nan")
        icir = signed_mean / std if math.isfinite(std) and std > 0 else float("nan")
        turnover = es.EP._turnover_proxy(panel, [{"values": oriented_values}], pd.DatetimeIndex(dates))
        return {
            **metrics,
            "direction": int(direction),
            "direction_frozen": True,
            "signed_mean_ic": signed_mean,
            "icir": finite(icir),
            "turnover": finite(turnover),
            "turnover_proxy": finite(turnover),
        }

    def search(self, node: dict, panel: pd.DataFrame, eval_dates) -> dict:
        dates = pd.DatetimeIndex(eval_dates)
        values, series, metrics = self._raw(node, panel, dates, "search")
        direction = 1 if metrics["raw_mean_ic"] >= 0 else -1
        result = self._oriented(values, series, metrics, panel, dates, direction)
        icir = result["icir"]
        result["search_valid"] = bool(result["signed_mean_ic"] >= self.search_min_ic
                                      and icir is not None and icir >= self.search_min_icir)
        fitness = (result["signed_mean_ic"] + 0.3 * (icir or 0.0)
                   - 0.1 * (result["turnover"] or 0.0))
        result["search_fitness"] = float(fitness)
        result["fitness"] = float(fitness)
        result["direction_source"] = "search_raw_mean_ic"
        return result

    def audit(self, node: dict, panel: pd.DataFrame, eval_dates, frozen_direction: int) -> dict:
        if frozen_direction not in (-1, 1) or isinstance(frozen_direction, bool):
            raise EvaluationError("audit requires the explicit search-frozen direction (-1 or 1)")
        dates = pd.DatetimeIndex(eval_dates)
        values, series, metrics = self._raw(node, panel, dates, "audit")
        result = self._oriented(values, series, metrics, panel, dates, int(frozen_direction))
        result["direction_source"] = "frozen_search_direction"
        return result
