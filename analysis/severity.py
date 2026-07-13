"""§4.5 — Conflict severity index.

Combines three signals per conflict episode into one comparable 0-1 score, so
small and large countries — and short and long wars — can be ranked on the
same scale:

    ntl_pct_drop              — depth of the light blackout (fraction below baseline)
    total_fatalities          — log-scaled first (fatalities are heavy-tailed;
                                 a handful of huge episodes would otherwise
                                 swamp the min-max scale)
    blackout_duration_months  — months until lights recovered (right-censored /
                                 permanent blackouts take the worst *observed*
                                 duration, so they rank at the top rather than
                                 falling out of the scale as NaN)

Each is min-max scaled to [0, 1] and averaged with ``config.SEVERITY_WEIGHTS``
(equal-weight by default). The three scaled components are kept alongside the
score — the stacked-bar decomposition and the bubble size in View 3 both need
them individually, not just the final number.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _minmax(s: pd.Series) -> pd.Series:
    """Scale a series to [0, 1]; a constant (or all-NaN) series maps to 0."""
    lo, hi = s.min(), s.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def severity_index(
    episodes: pd.DataFrame,
    *,
    drop_col: str = "ntl_pct_drop",
    fatalities_col: str = "total_fatalities",
    duration_col: str = "blackout_duration_months",
) -> pd.DataFrame:
    """Score each conflict episode; returns the input frame plus the three
    scaled components (``component_*``) and ``severity_score``.
    """
    df = episodes.copy()

    drop_signal = df[drop_col].clip(lower=0).fillna(0)
    fatalities_signal = np.log1p(df[fatalities_col].clip(lower=0).fillna(0))

    worst_duration = df[duration_col].max()
    worst_duration = worst_duration if np.isfinite(worst_duration) else 0.0
    duration_signal = df[duration_col].fillna(worst_duration)  # censored -> worst observed

    weights = config.SEVERITY_WEIGHTS
    df["component_ntl_pct_drop"] = _minmax(drop_signal)
    df["component_total_fatalities"] = _minmax(fatalities_signal)
    df["component_blackout_duration_months"] = _minmax(duration_signal)

    df["severity_score"] = (
        df["component_ntl_pct_drop"] * weights["ntl_pct_drop"]
        + df["component_total_fatalities"] * weights["total_fatalities"]
        + df["component_blackout_duration_months"] * weights["blackout_duration_months"]
    )
    return df.sort_values("severity_score", ascending=False).reset_index(drop=True)
