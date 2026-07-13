"""
Defines endpoints that retrieve data related to post-conflict recovery.
Gathers information concerning conflict episodes, synthetic-control counterfactuals,
and development profiles to measure structural recoveries following conflict shocks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from flask import Blueprint

import config
from backend import db
from backend.routes import envelope

# Initialize the Flask blueprint for recovery API endpoints
bp = Blueprint("recovery", __name__)

# Constants designating date-centric columns and overarching episode details
_DATE_COLS = ("onset_date", "trough_date")
_EPISODE_COLS = [
    "iso3", "country", "region", "onset_date", "trough_date", "conflict_type",
    "ntl_pct_drop", "recovery_gap_closed_pct", "months_observed", "recovery_time_months", "censored",
    "episode_total_fatalities", "severity_score",
]


def _dev_cols(df: pd.DataFrame) -> list[str]:
    """
    Identifies and returns all configured development factor columns currently present in the dataframe.
    """
    return [c for c in config.DEVELOPMENT_FACTORS if c in df.columns]


def _stringify_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Formats parsed date columns into standard string representations.
    """
    df = df.copy()
    for col in _DATE_COLS:
        if col in df:
            df[col] = pd.to_datetime(df[col]).dt.strftime("%Y-%m-%d")
    return df


@bp.get("/country/<iso3>/shock-recovery")
def shock_recovery(iso3: str):
    """
    Extracts time series data capturing the timeline of light observations and conflict severity
    for a given country. Merges episodic details directly onto the final output payload.
    """
    # Normalize country identifier
    iso3 = iso3.upper()
    df = db.plot3_shock_recovery()
    
    # Filter for the target country and structure chronologically
    sub = df[df["iso3"] == iso3].sort_values("date").copy()

    # Apply date string formatting
    series = sub.copy()
    series["date"] = series["date"].dt.strftime("%Y-%m-%d")
    
    # Restrict columns strictly to essential attributes
    series_cols = ["date", "sum_of_lights", "ntl_deseasonalized", "counterfactual", "total_fatalities", "anomaly"]
    series_out = series[[c for c in series_cols if c in series.columns]].replace({np.nan: None}).to_dict(orient="records")

    # Assess whether there are episodes linked to this dataset
    ep_cols = [c for c in (_EPISODE_COLS + _dev_cols(sub)) if c in sub.columns]
    episode = None
    if ep_cols and not sub.empty and sub.get("onset_date") is not None and sub["onset_date"].notna().any():
        episode = _stringify_dates(sub[ep_cols].iloc[[0]]).replace({np.nan: None}).to_dict(orient="records")[0]

    return envelope({"series": series_out, "episode": episode}, iso3=iso3)


@bp.get("/recovery-tradeoff")
def recovery_tradeoff():
    """
    Collects recovery statistics relative to various development constraints,
    outputting one aggregated row per conflict episode.
    Used for comparative visualization elements.
    """
    # Fetch base dataframe
    df = db.plot3_shock_recovery()
    if "onset_date" not in df.columns:
        return envelope([], count=0, development_factors={})

    # Concatenate episode metrics with broad development factors
    dev = _dev_cols(df)
    cols = [c for c in (_EPISODE_COLS + dev) if c in df.columns]
    
    # Ensure one data point per ISO3 code with formatted dates
    episodes = df[df["onset_date"].notna()][cols].drop_duplicates("iso3")
    episodes = _stringify_dates(episodes)
    rows = episodes.replace({np.nan: None}).to_dict(orient="records")

    # Expose friendly labels associated with current development factors
    factor_labels = {c: config.DEVELOPMENT_FACTORS[c] for c in dev}
    return envelope(rows, count=len(rows), development_factors=factor_labels)
