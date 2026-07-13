"""§4.4 — GDP-NTL log-log residual (constant-GDP, population-controlled, robust).

Fits, per year, the power-law between night-lights and the economy on a log-log
scale, then scores every country-year by how far its observed brightness sits
from what that year's fit predicts.

Design choices (see the project's Plot-1 notes):

  * **Constant-USD GDP**, not current-USD. Current-USD GDP moves with inflation
    and exchange rates, so a currency collapse (Syria, Venezuela) craters
    GDP-in-dollars even when real activity didn't — inflating the residual for
    reasons that have nothing to do with a hidden economy. Real (constant) GDP
    makes the residual reflect genuine economic divergence.
  * **Population control.** Sum-of-lights conflates *how much* economy with *how
    big/populous* a country is, so small rich countries look artificially dim.
    We regress on log(population) alongside log(GDP) to net that out:
        log(lights) = alpha + beta_gdp*log(gdp) + beta_pop*log(pop) + eps
  * **Robust regression (Huber).** A few extreme small countries (Iceland,
    Maldives) tug an OLS line and distort the slope for everyone; Huber
    down-weights them so the line reflects the economic mass.

    predicted_log_ntl = alpha_t + beta_gdp_t*log_gdp + beta_pop_t*log_pop
    residual           = observed log_ntl - predicted_log_ntl

Positive residual => brighter than its GDP (and size) predict. Non-positive
sum_of_lights is floored (config.NTL_LOG_FLOOR) before logging.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config


def _fit_year(sub: pd.DataFrame, gdp_col: str, ntl_col: str, pop_col: str | None) -> dict:
    """Robust (Huber) fit of log_ntl ~ log_gdp [+ log_pop] on one year's rows.

    Returns a dict with alpha, beta_gdp, beta_pop, r2, n. NaN coefficients when
    too few rows to trust a fit (n < 5, since we now fit up to 2 slopes).
    """
    from sklearn.linear_model import HuberRegressor

    cols = [gdp_col, ntl_col] + ([pop_col] if pop_col else [])
    valid = sub[(sub[gdp_col] > 0) & (sub[ntl_col] > config.NTL_LOG_FLOOR)]
    if pop_col:
        valid = valid[valid[pop_col] > 0]
    valid = valid.dropna(subset=cols)
    if len(valid) < 5:
        return {"alpha": np.nan, "beta_gdp": np.nan, "beta_pop": np.nan, "r2": np.nan, "n": len(valid)}

    features = [np.log(valid[gdp_col].to_numpy())]
    if pop_col:
        features.append(np.log(valid[pop_col].to_numpy()))
    x = np.column_stack(features)
    y = np.log(valid[ntl_col].to_numpy())

    # alpha=0: no L2 shrinkage (we want the honest robust line, not a regularized
    # one); generous max_iter so the IRLS reliably converges on real data.
    reg = HuberRegressor(alpha=0.0, max_iter=2000).fit(x, y)
    r2 = float(reg.score(x, y))
    beta_pop = float(reg.coef_[1]) if pop_col else 0.0
    return {
        "alpha": float(reg.intercept_),
        "beta_gdp": float(reg.coef_[0]),
        "beta_pop": beta_pop,
        "r2": r2,
        "n": len(valid),
    }


def fit_gdp_ntl_residual(
    df: pd.DataFrame,
    *,
    iso3_col: str = "iso3",
    year_col: str = "year",
    gdp_col: str = "gdp_constant_usd",
    ntl_col: str = "sum_of_lights",
    pop_col: str | None = "population",
) -> pd.DataFrame:
    """Fit one robust log-log regression per year and score every country-year.

    ``df`` is a country-year panel with GDP (constant), night-lights and
    population columns. Returns the per-year fit coefficients (``alpha``,
    ``beta_gdp``, ``beta_pop``, ``r2``, ``fit_n``) broadcast onto every row,
    plus ``log_gdp``, ``log_pop``, ``log_ntl``, ``predicted_log_ntl`` and
    ``residual``. Falls back to a GDP-only fit if ``pop_col`` is absent.
    """
    use_pop = pop_col if (pop_col and pop_col in df.columns) else None

    rows = []
    for year, sub in df.groupby(year_col):
        fit = _fit_year(sub, gdp_col, ntl_col, use_pop)

        keep = [iso3_col, year_col, gdp_col, ntl_col] + ([use_pop] if use_pop else [])
        out = sub[keep].copy()
        out["alpha"] = fit["alpha"]
        out["beta"] = fit["beta_gdp"]           # kept as `beta` for backward compatibility
        out["beta_gdp"] = fit["beta_gdp"]
        out["beta_pop"] = fit["beta_pop"]
        out["fit_r2"] = fit["r2"]
        out["fit_n"] = fit["n"]

        floored_ntl = out[ntl_col].clip(lower=config.NTL_LOG_FLOOR)
        with np.errstate(divide="ignore", invalid="ignore"):
            log_gdp = np.log(out[gdp_col].where(out[gdp_col] > 0))
            log_pop = np.log(out[use_pop].where(out[use_pop] > 0)) if use_pop else 0.0
            log_ntl = np.log(floored_ntl)

        out["log_gdp"] = log_gdp
        out["log_pop"] = log_pop if use_pop else np.nan
        out["log_ntl"] = log_ntl
        predicted = fit["alpha"] + fit["beta_gdp"] * log_gdp + (fit["beta_pop"] * log_pop if use_pop else 0.0)
        out["predicted_log_ntl"] = predicted
        out["residual"] = out["log_ntl"] - out["predicted_log_ntl"]
        rows.append(out)

    result = pd.concat(rows, ignore_index=True)
    return result.sort_values([iso3_col, year_col]).reset_index(drop=True)
