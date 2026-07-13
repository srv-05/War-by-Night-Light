"""§4.6-4.8 — Synthetic-control counterfactual, recovery time, recovery capacity.

§4.6 Synthetic control
    Given a target country and a pool of non-conflict donor countries, find
    donor weights w >= 0, sum(w) = 1, minimizing the pre-conflict fit error
    between the target and the weighted donor trajectory. We solve this with
    ``scipy.optimize.nnls`` (non-negative least squares) on the pre-onset
    window and then normalize the weights onto the simplex — the
    "nnls + normalization" approach the spec calls out as an acceptable
    alternative to a full SLSQP solve, and considerably cheaper for a country
    picker that needs to refit on every click.

§4.7 Recovery time
    Months from the post-onset trough until observed NTL re-crosses within
    ``config.RECOVERY_THRESHOLD_PCT`` of the counterfactual. If it never
    recovers within ``config.MAX_RECOVERY_HORIZON_MONTHS``, the episode is
    right-censored — returned with ``censored=True`` and a NaN recovery time,
    never dropped, since censoring itself is the signal for the bubble chart.

§4.8 Recovery-capacity score
    min-max scaled military spend and a development-spending proxy, averaged.
    No sign is assumed for military spending's relationship to recovery — that
    relationship is exactly what View 3's bubble chart is for.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.optimize import nnls

import config


def fit_synthetic_control(
    target: pd.Series,
    donors: pd.DataFrame,
    pre_mask: pd.Series,
    max_donors: int = 25,
) -> dict:
    """Fit donor weights on the pre-onset window and project the counterfactual
    across the full series.

    ``target`` and ``donors`` must share the same (positional/date) index;
    ``pre_mask`` is a boolean mask over that same index marking the pre-conflict
    fit window. Donor columns with any missing pre-period value are dropped
    before fitting (NNLS can't handle NaNs).

    Returns a dict with ``weights`` (Series over surviving donor columns,
    summing to 1), ``counterfactual`` (Series over the full index, in the
    target's absolute units) and ``pre_fit_rmse`` (fit quality, indexed).

    Crucially the fit is done on **indexed** series — each series divided by its
    own pre-onset median (=1 at baseline). A big country like Ukraine emits more
    total light than any donor, so a convex combination of donors on *absolute*
    levels can never reach it (the old counterfactual was 15-100x too low).
    Indexing matches the *trajectory shape*, and we rescale the result back to
    the target's level for display.
    """
    pre_target_raw = target[pre_mask].to_numpy(dtype=float)
    pre_donors_full = donors.loc[pre_mask]

    valid_cols = list(pre_donors_full.columns[pre_donors_full.notna().all(axis=0)])

    # Index everything to its own pre-onset median (baseline = 1.0).
    target_base = np.nanmedian(pre_target_raw) if len(pre_target_raw) else np.nan
    if not np.isfinite(target_base) or target_base <= 0:
        empty = pd.Series(np.nan, index=target.index)
        return {"weights": pd.Series(dtype=float), "counterfactual": empty, "pre_fit_rmse": np.nan}

    donor_base = {c: np.nanmedian(pre_donors_full[c].to_numpy(dtype=float)) for c in valid_cols}
    valid_cols = [c for c in valid_cols if np.isfinite(donor_base[c]) and donor_base[c] > 0]

    pre_target = pre_target_raw / target_base
    donors_idx_full = donors[valid_cols].apply(lambda s: s / donor_base[s.name])
    pre_donors = (pre_donors_full[valid_cols].to_numpy(dtype=float)
                  / np.array([donor_base[c] for c in valid_cols]))

    # Keep the donors whose indexed pre-trajectory best correlates with the target.
    if len(valid_cols) > max_donors and len(pre_target) > 2:
        corrs = {}
        for j, col in enumerate(valid_cols):
            v = pre_donors[:, j]
            if np.std(v) > 0:
                corrs[col] = abs(np.corrcoef(v, pre_target)[0, 1])
        keep = [c for c, _ in sorted(corrs.items(), key=lambda kv: kv[1], reverse=True)[:max_donors]]
        idx = [valid_cols.index(c) for c in keep]
        valid_cols, pre_donors = keep, pre_donors[:, idx]
        donors_idx_full = donors_idx_full[valid_cols]

    if len(valid_cols) == 0 or len(pre_target) == 0 or not np.isfinite(pre_target).all():
        empty = pd.Series(np.nan, index=target.index)
        return {"weights": pd.Series(dtype=float), "counterfactual": empty, "pre_fit_rmse": np.nan}

    try:
        raw_weights, _ = nnls(pre_donors, pre_target, maxiter=5000)
    except RuntimeError:
        raw_weights = np.ones(pre_donors.shape[1])
    total = raw_weights.sum()
    weights = raw_weights / total if total > 0 else np.full_like(raw_weights, 1.0 / len(raw_weights))

    weights_series = pd.Series(weights, index=valid_cols)
    # Counterfactual in indexed units, then rescaled back to the target's level.
    cf_indexed = donors_idx_full.to_numpy(dtype=float) @ weights
    counterfactual = pd.Series(cf_indexed * target_base, index=target.index)

    pre_predicted = pre_donors @ weights
    pre_fit_rmse = float(np.sqrt(np.mean((pre_target - pre_predicted) ** 2)))

    return {"weights": weights_series, "counterfactual": counterfactual, "pre_fit_rmse": pre_fit_rmse}


def gap_closed_recovery(
    observed: pd.Series,
    counterfactual: pd.Series,
    onset_idx: int,
    *,
    horizon_months: int = 24,
) -> dict:
    """How much of the light gap the country closed within ``horizon_months`` of
    its post-onset trough — a graded recovery outcome (0 = still fully dark,
    1 = back to the counterfactual), which unlike binary recovery-time works for
    every episode instead of censoring almost all of them.

        gap_t          = counterfactual_t - observed_t         (the war deficit)
        gap_closed_pct = 1 - gap_at_horizon / gap_at_trough     (clamped 0..1)

    ``censored`` is True when fewer than ``horizon_months`` of post-trough data
    exist (an ongoing war we haven't watched long enough), in which case the
    percentage reflects recovery so far.
    """
    observed = observed.reset_index(drop=True)
    counterfactual = counterfactual.reset_index(drop=True)

    post = observed.iloc[onset_idx:]
    if not post.notna().any():
        return {"trough_idx": None, "trough_date_idx": None, "gap_closed_pct": np.nan,
                "ntl_pct_drop": np.nan, "censored": True, "months_observed": 0}

    trough_idx = int(post.idxmin())
    gap = counterfactual - observed
    gap_trough = float(gap.iloc[trough_idx])

    # Depth of the blackout vs the counterfactual (clamped to [0,1]).
    cf_trough = float(counterfactual.iloc[trough_idx])
    ntl_pct_drop = float(np.clip(gap_trough / cf_trough, 0.0, 1.0)) if cf_trough > 0 else np.nan

    if gap_trough <= 0:  # never actually fell below the counterfactual
        return {"trough_idx": trough_idx, "gap_closed_pct": 1.0, "ntl_pct_drop": 0.0,
                "censored": False, "months_observed": len(observed) - 1 - trough_idx}

    end = min(len(observed) - 1, trough_idx + horizon_months)
    gap_end = float(gap.iloc[end])
    gap_closed = float(np.clip(1.0 - gap_end / gap_trough, 0.0, 1.0))
    months_observed = end - trough_idx
    censored = months_observed < horizon_months
    return {
        "trough_idx": trough_idx,
        "gap_closed_pct": gap_closed,
        "ntl_pct_drop": ntl_pct_drop,
        "censored": censored,
        "months_observed": months_observed,
    }


def recovery_time(
    observed: pd.Series,
    counterfactual: pd.Series,
    onset_idx: int,
    *,
    threshold_pct: float | None = None,
    max_horizon_months: int | None = None,
) -> dict:
    """Months from the post-onset trough to re-crossing within ``threshold_pct``
    of the counterfactual, searching at most ``max_horizon_months`` past the
    trough. Both series must share a positional (0..n-1) index.
    """
    threshold_pct = config.RECOVERY_THRESHOLD_PCT if threshold_pct is None else threshold_pct
    max_horizon = config.MAX_RECOVERY_HORIZON_MONTHS if max_horizon_months is None else max_horizon_months

    observed = observed.reset_index(drop=True)
    counterfactual = counterfactual.reset_index(drop=True)

    post_obs = observed.iloc[onset_idx:]
    if not post_obs.notna().any():
        return {"trough_idx": None, "trough_value": np.nan, "recovery_idx": None,
                "recovery_time_months": np.nan, "censored": True}

    trough_idx = int(post_obs.idxmin())
    trough_value = float(observed.iloc[trough_idx])

    horizon_end = min(len(observed), trough_idx + max_horizon + 1)
    window = pd.DataFrame({
        "observed": observed.iloc[trough_idx:horizon_end],
        "counterfactual": counterfactual.iloc[trough_idx:horizon_end],
    })
    with np.errstate(divide="ignore", invalid="ignore"):
        gap_pct = (window["observed"] - window["counterfactual"]).abs() / window["counterfactual"].abs()
    within_threshold = window[gap_pct <= threshold_pct]

    if len(within_threshold):
        recovery_idx = int(within_threshold.index[0])
        return {
            "trough_idx": trough_idx,
            "trough_value": trough_value,
            "recovery_idx": recovery_idx,
            "recovery_time_months": recovery_idx - trough_idx,
            "censored": False,
        }

    # Right-censored: never recovered within the horizon. Keep the row —
    # censoring is exactly what the recovery-tradeoff bubble needs to show.
    return {
        "trough_idx": trough_idx,
        "trough_value": trough_value,
        "recovery_idx": None,
        "recovery_time_months": np.nan,
        "censored": True,
    }


def _minmax(s: pd.Series) -> pd.Series:
    lo, hi = s.min(), s.max()
    if not np.isfinite(lo) or not np.isfinite(hi) or hi == lo:
        return pd.Series(0.0, index=s.index)
    return (s - lo) / (hi - lo)


def recovery_capacity_score(
    df: pd.DataFrame,
    *,
    military_col: str = "military_exp_pct_gdp",
    dev_spending_col: str = "gov_expenditure_pct_gdp",
) -> pd.Series:
    """Equal-weight average of min-max-scaled military spend and a
    public/development-spending proxy. Deliberately does not hard-code a sign
    for either input — whether higher military spending predicts faster or
    slower recovery is an empirical question the bubble chart (x = this score,
    y = recovery time) answers, not an assumption baked into the score.
    """
    military_scaled = _minmax(df[military_col].astype(float))
    if dev_spending_col in df.columns:
        dev_scaled = _minmax(df[dev_spending_col].astype(float))
    else:
        dev_scaled = pd.Series(0.0, index=df.index)
    return 0.5 * military_scaled + 0.5 * dev_scaled
