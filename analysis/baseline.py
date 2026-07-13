"""§4.2-4.3 — NTL baseline and anomaly.

    baseline    = median(sum_of_lights over the trailing BASELINE_WINDOW_MONTHS
                  months immediately BEFORE conflict onset)   [median, not mean,
                  for outlier resistance]
    anomaly_t   = (sum_of_lights_t - baseline) / baseline

These are pure functions of a single country's date-sorted panel; every other
view (2, 3, 4) imports them rather than recomputing "normal" independently, so
the whole dashboard agrees on one definition of peacetime.

Also home to conflict-episode detection, since baseline/anomaly and episode
onset are the same concept from two sides — severity, synthetic_control and
spillover all detect episodes the same way by importing from here.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def detect_conflict_episodes(
    df: pd.DataFrame,
    *,
    fatalities_col: str = "total_fatalities",
    date_col: str = "date",
    threshold: float | None = None,
    max_gap: int = 2,
    min_len: int = 1,
) -> list[dict]:
    """Contiguous runs of conflict months for one country, sorted by date.

    A month is "in conflict" once ``fatalities_col >= threshold``. Gaps of up to
    ``max_gap`` quiet months are bridged into the same episode so a brief lull
    doesn't split one war into two episodes.

    ``df`` must already be a single country, sorted by date, default RangeIndex.
    Returns a list of dicts with onset/end positions and dates, duration, and
    fatality totals — the shared unit every downstream model keys off of.
    """
    threshold = config.CONFLICT_FATALITY_THRESHOLD if threshold is None else threshold
    df = df.reset_index(drop=True)
    hot = (df[fatalities_col].fillna(0) >= threshold).to_numpy()

    spans: list[tuple[int, int]] = []
    start = None
    end = None
    gap = 0
    for i, is_hot in enumerate(hot):
        if is_hot:
            if start is None:
                start = i
            end = i
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                spans.append((start, end))
                start = None
    if start is not None:
        spans.append((start, end))

    episodes = []
    for s, e in spans:
        if (e - s + 1) < min_len:
            continue
        window = df.iloc[s : e + 1]
        episodes.append(
            {
                "onset_idx": int(s),
                "end_idx": int(e),
                "onset_date": df.loc[s, date_col],
                "end_date": df.loc[e, date_col],
                "duration_months": int(e - s + 1),
                "total_fatalities": float(window[fatalities_col].sum()),
                "peak_fatalities": float(window[fatalities_col].max()),
            }
        )
    return episodes


def ntl_baseline(
    df: pd.DataFrame,
    onset_idx: int,
    *,
    value_col: str = "sum_of_lights",
    window_months: int | None = None,
) -> float:
    """Median ``value_col`` over the trailing window strictly before ``onset_idx``.

    ``onset_idx`` is a positional index into a date-sorted, single-country frame
    with a default RangeIndex (as produced by :func:`detect_conflict_episodes`).
    Falls back to whatever pre-onset history is available when the country's
    record starts less than a full window before onset; returns NaN if there is
    none at all (e.g. the war predates the VIIRS record).
    """
    window_months = config.BASELINE_WINDOW_MONTHS if window_months is None else window_months
    lo = max(0, onset_idx - window_months)
    pre = df.iloc[lo:onset_idx][value_col]
    if pre.notna().any():
        return float(np.nanmedian(pre))
    return float("nan")


def ntl_anomaly(values: pd.Series, baseline: float) -> pd.Series:
    """anomaly_t = (sum_of_lights_t - baseline) / baseline."""
    if baseline is None or not np.isfinite(baseline) or baseline == 0:
        return pd.Series(np.nan, index=values.index)
    return (values - baseline) / baseline


def compute_country_anomaly(
    df: pd.DataFrame,
    onset_idx: int,
    *,
    value_col: str = "sum_of_lights",
    window_months: int | None = None,
) -> pd.DataFrame:
    """Attach ``baseline`` (a single peacetime reference level) and ``anomaly``
    columns to one country's date-sorted panel, relative to one conflict onset.
    """
    out = df.copy()
    baseline = ntl_baseline(df, onset_idx, value_col=value_col, window_months=window_months)
    out["baseline"] = baseline
    out["anomaly"] = ntl_anomaly(out[value_col], baseline)
    return out


# ---------------------------------------------------------------------------
# Deseasonalization (§ Plot 2 — remove the calendar so anomaly = conflict)
# ---------------------------------------------------------------------------
def seasonal_factors(
    df: pd.DataFrame,
    *,
    value_col: str = "sum_of_lights",
    month_col: str = "month",
    peacetime_mask: pd.Series | None = None,
    min_peace_months: int = 24,
) -> pd.Series:
    """Multiplicative month-of-year factors (indexed 1..12, mean ~1) for one
    country.

    Monthly VIIRS swings ±40% on the calendar alone (short summer nights, winter
    snow, vegetation), so a raw anomaly can read *positive* mid-war (Ukraine,
    June 2022). We estimate the recurring monthly pattern from **peacetime**
    months (so a war doesn't distort the profile), normalize it to mean 1, and
    divide it out. Falls back to all months when there isn't enough peacetime
    history, and returns all-ones if even that is too thin to trust.
    """
    src = df
    if peacetime_mask is not None and peacetime_mask.sum() >= min_peace_months:
        src = df[peacetime_mask]

    by_month = src.groupby(month_col)[value_col].median()
    if len(by_month) < 6 or not np.isfinite(by_month.mean()) or by_month.mean() == 0:
        return pd.Series(1.0, index=range(1, 13))

    factors = by_month / by_month.mean()
    # Fill any missing months with 1 (no adjustment) and clip pathological values.
    factors = factors.reindex(range(1, 13)).fillna(1.0).clip(lower=0.2, upper=5.0)
    return factors


def deseasonalize(
    df: pd.DataFrame,
    factors: pd.Series,
    *,
    value_col: str = "sum_of_lights",
    month_col: str = "month",
) -> pd.Series:
    """Divide ``value_col`` by its month-of-year factor -> a calendar-free series."""
    f = df[month_col].map(factors).fillna(1.0)
    return df[value_col] / f
