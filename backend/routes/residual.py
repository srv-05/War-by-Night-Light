"""
This module defines the 'residual' blueprint for the War-by-Night-Light application.
It provides an endpoint to serve data for a residual choropleth map. The map displays
the difference between a country's predicted light output (based on GDP and population)
and its actual light output, which represents an anomaly score.
"""
from __future__ import annotations

import numpy as np
from flask import Blueprint, request

from backend import db
from backend.routes import envelope

bp = Blueprint("residual", __name__)


@bp.get("/residual-choropleth")
def residual_choropleth():
    """
    Handles GET requests for the residual choropleth map data.
    Returns the residual anomaly data for each country for a specific year.
    """
    # Fetch the country-year residual and anomaly data from the database.
    df = db.plot1_residual()
    # Filter the available years to 2014-2023
    valid_years = sorted(int(y) for y in df["year"].dropna().unique() if 2014 <= int(y) <= 2023)
    default_year = max(valid_years) if valid_years else 2023
    
    year = request.args.get("year", type=int) or default_year
    
    # Filter the data to include only the requested year and exclude missing anomalies.
    sub = df[(df["year"] == year) & df["residual_anomaly"].notna()]

    cols = ["iso3", "country", "region", "residual", "residual_anomaly", "residual_baseline"]
    if "population" in sub.columns:
        cols.append("population")
    cols = [c for c in cols if c in sub.columns]
    
    # Fetch conflict episode summary data to enrich the residual data.
    df3 = db.plot3_shock_recovery()
    if not df3.empty:
        # Extract unique conflict episodes per country and join with the residual data.
        df3_unique = df3.drop_duplicates(subset=["iso3"]).set_index("iso3")
        sub = sub.join(df3_unique[["conflict_type", "severity_score", "recovery_gap_closed_pct"]], on="iso3")
        cols.extend(["conflict_type", "severity_score", "recovery_gap_closed_pct"])
        
    rows = sub[cols].replace({np.nan: None}).to_dict(orient="records")

    # Calculate statistics and a color range for the frontend rendering.
    anom = sub["residual_anomaly"].to_numpy()
    fit = {}
    if len(sub):
        r0 = sub.iloc[0]
        fit = {
            "beta_gdp": _num(r0.get("beta_gdp")),
            "beta_pop": _num(r0.get("beta_pop")),
            "r2": _num(r0.get("fit_r2")),
            "n": int(r0["fit_n"]) if np.isfinite(r0.get("fit_n", np.nan)) else None,
        }
    color_range = float(np.nanpercentile(np.abs(anom), 88)) if len(anom) else 1.0

    return envelope(
        rows, year=year,
        available_years=valid_years,
        fit=fit, color_range=round(max(color_range, 0.4), 3),
    )


def _num(v):
    return float(v) if v is not None and np.isfinite(v) else None
