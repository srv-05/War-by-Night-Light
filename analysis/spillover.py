"""§4.9 — Spillover edge weight.

For an epicenter and each land-border neighbor, tests whether the epicenter's
NTL-anomaly series moved in step with the neighbor's over the conflict window —
a light-loss "ripple" crossing the border.

A raw correlation of two neighboring countries' anomalies is contaminated by
shared regional trends (a regional drought, a regional recession, a shared
power grid) that would show up as "spillover" even with no real connection.
So we first partial out a regional common factor: regress each country's
anomaly on the regional-mean anomaly (or first PC) across the epicenter +
neighbor set, and keep only the residuals. The edge weight is then the
correlation of those residualized series — optionally at the lag that
maximizes |correlation|; a peak at a positive lag means the epicenter's
anomaly leads the neighbor's (consistent with the epicenter causing the
ripple rather than the reverse).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
from analysis import baseline as baseline_module


def anomaly_reference_matrix(wide_lights: pd.DataFrame, iso3s: list[str], onset_idx: int) -> pd.DataFrame:
    """(positional x iso3) NTL-anomaly matrix for ``iso3s``, all referenced to
    the *same* onset — each country's own trailing-window median before that
    shared onset is its baseline. Lets an epicenter be compared against
    neighbors that have no conflict onset of their own (or a different one),
    by asking "what was normal for you right before *this* war began".
    """
    out = pd.DataFrame(index=wide_lights.index)
    for iso3 in iso3s:
        if iso3 not in wide_lights.columns:
            continue
        series = wide_lights[iso3].reset_index(drop=True)
        base = baseline_module.ntl_baseline(
            pd.DataFrame({"sum_of_lights": series}), onset_idx, value_col="sum_of_lights"
        )
        out[iso3] = baseline_module.ntl_anomaly(series, base).to_numpy()
    return out


def partial_out_regional_factor(anomaly_wide: pd.DataFrame, region_cols: list[str]) -> pd.DataFrame:
    """Regress each of ``region_cols`` on the *leave-one-out* regional-mean
    anomaly (the mean of every other column in the region) and return the
    residuals, same shape as the input subset.

    Leave-one-out matters: regressing a country on a regional mean that
    includes its own value contaminates the "regional factor" with the
    country's own noise, which can manufacture spurious residual correlation
    between otherwise-unrelated countries (most visible with few countries in
    the region or one high-variance member). Excluding the target itself keeps
    the regional factor a genuine external reference.

    Uses simple OLS (``np.polyfit``, degree 1) per column rather than a shared
    regional model, since each country's sensitivity to the regional factor can
    differ. Falls back to a plain de-meaned series when there isn't enough
    regional variation to fit a slope, or when the region has only one member.
    """
    region = anomaly_wide[region_cols]

    residuals = pd.DataFrame(index=region.index, columns=region.columns, dtype=float)
    for col in region.columns:
        others = [c for c in region.columns if c != col]
        y = region[col]
        if not others:
            residuals[col] = y - y.mean()
            continue
        x = region[others].mean(axis=1)
        valid = y.notna() & x.notna()
        if valid.sum() < 3 or np.isclose(x[valid].std(), 0):
            residuals[col] = y - y.mean()
            continue
        slope, intercept = np.polyfit(x[valid].to_numpy(), y[valid].to_numpy(), 1)
        predicted = intercept + slope * x
        residuals[col] = y - predicted
    return residuals


def lagged_correlation(a: pd.Series, b: pd.Series, max_lag: int) -> tuple[float, int]:
    """Peak |correlation| between ``a`` and ``b`` across lags in [-max_lag, max_lag].

    Positive lag shifts ``b`` to align with an earlier ``a`` (i.e. correlates
    a_t with b_{t+lag}) — a peak at positive lag means ``a`` leads ``b``.
    Returns (nan, 0) if no lag has enough overlapping, non-NaN months.
    """
    best_corr, best_lag = float("nan"), 0
    a = a.reset_index(drop=True)
    b = b.reset_index(drop=True)
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a_shift = a.iloc[: len(a) - lag] if lag > 0 else a
            b_shift = b.iloc[lag:]
        else:
            a_shift = a.iloc[-lag:]
            b_shift = b.iloc[: len(b) + lag]
        pair = pd.concat(
            [a_shift.reset_index(drop=True), b_shift.reset_index(drop=True)], axis=1
        ).dropna()
        if len(pair) < 3:
            continue
        corr = pair.iloc[:, 0].corr(pair.iloc[:, 1])
        if np.isfinite(corr) and (not np.isfinite(best_corr) or abs(corr) > abs(best_corr)):
            best_corr, best_lag = float(corr), lag
    return best_corr, best_lag


def compute_spillover_edges(
    anomaly_wide: pd.DataFrame,
    epicenter: str,
    neighbors: list[str],
    window_mask: pd.Series,
    *,
    max_lag: int | None = None,
    min_overlap: int | None = None,
) -> dict:
    """Build the spillover network for one epicenter over one conflict window.

    ``anomaly_wide`` is a (date x iso3) matrix of NTL anomaly values.
    ``window_mask`` is a boolean mask over ``anomaly_wide.index`` selecting the
    conflict window (+ buffer) to correlate over.

    Returns ``{"nodes": [...], "edges": [...]}`` ready to hand to the D3
    force-directed network: edge ``weight`` is the residualized correlation,
    ``lag_months`` the lag at which it peaked.
    """
    max_lag = config.SPILLOVER_MAX_LAG_MONTHS if max_lag is None else max_lag
    min_overlap = config.SPILLOVER_MIN_OVERLAP_MONTHS if min_overlap is None else min_overlap

    region_cols = [c for c in [epicenter, *neighbors] if c in anomaly_wide.columns]
    windowed = anomaly_wide.loc[window_mask, region_cols]
    residualized = partial_out_regional_factor(windowed, region_cols)

    nodes = [{"iso3": epicenter, "role": "epicenter"}]
    edges = []
    if epicenter not in residualized.columns:
        return {"nodes": nodes, "edges": edges}

    epicenter_series = residualized[epicenter]
    for neighbor in neighbors:
        if neighbor not in residualized.columns:
            continue
        neighbor_series = residualized[neighbor]
        overlap = pd.concat([epicenter_series, neighbor_series], axis=1).dropna()
        if len(overlap) < min_overlap:
            continue
        corr, lag = lagged_correlation(epicenter_series, neighbor_series, max_lag)
        if not np.isfinite(corr):
            continue
        nodes.append({"iso3": neighbor, "role": "neighbor"})
        edges.append({
            "source": epicenter,
            "target": neighbor,
            "weight": corr,
            "lag_months": lag,
            "n_months": int(len(overlap)),
        })
    return {"nodes": nodes, "edges": edges}
