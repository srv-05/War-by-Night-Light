"""
Defines endpoints that retrieve aggregated conflict data.
Handles logic involving night-light intensity vs. conflict occurrence,
conflict type breakdowns, trade flow diagrams, and key performance indicators.
"""
from __future__ import annotations

import pandas as pd
from flask import Blueprint, request

from backend import db
from backend.routes import envelope

# Set up the Flask blueprint for the conflict API endpoints
bp = Blueprint("conflict", __name__)


@bp.get("/conflict-type-decay")
def conflict_type_decay():
    """
    Fetches the relation between specific conflict event types and light loss anomalies.
    Can be filtered by a specific country code.
    """
    # Fetch the conflict type dataset
    df = db.plot2_conflict_type().copy()
    
    # Check query parameters for country filtering
    iso3 = request.args.get("iso3")
    if iso3:
        df = df[df["iso3"] == iso3]
    
    out = []
    # Collect light decay distributions grouped by event types
    for event_type, g in df.groupby("event_type"):
        # Retrieve valid anomaly values
        anomalies = g["residual_anomaly"].dropna().tolist()
        if not anomalies:
            continue
            
        # Register statistical sums and means
        out.append({
            "event_type": event_type,
            "anomalies": anomalies,
            "mean_anomaly": float(g["residual_anomaly"].mean()),
            "fatalities": float(g["total_fatalities"].sum())
        })
        
    # Sort results by the average light decay value
    out.sort(key=lambda d: d["mean_anomaly"])
    return envelope({"conflict_types": out})

@bp.get("/kpis")
def kpis():
    """
    Computes global performance indicators based on the current parsed state of conflict and recovery data.
    """
    year = request.args.get("year", type=int)
    
    # Retrieve necessary analysis files
    df3 = db.plot3_shock_recovery()
    df4 = db.plot4_spillover()
    
    if year:
        df3_year = df3[df3["year"] == year]
        active_isos = df3_year[df3_year["total_fatalities"] > 0]["iso3"].unique()
        countries_in_conflict = len(active_isos)
        df3_unique = df3_year[df3_year["iso3"].isin(active_isos)].drop_duplicates("iso3")
        
        if not df4.empty:
            df4["start_year"] = pd.to_datetime(df4["window_start"]).dt.year
            df4["end_year"] = pd.to_datetime(df4["window_end"]).dt.year
            df4 = df4[(df4["start_year"] <= year) & (df4["end_year"] >= year)]
    else:
        reg = db.countries_by_iso3()
        conflict_isos = {iso for iso, c in reg.items() if c.get("has_conflict")}
        countries_in_conflict = len(conflict_isos)
        df3_unique = df3.drop_duplicates("iso3")
        
    # Calculate global averages and package them into an envelope
    return envelope({
        "countries_in_conflict": countries_in_conflict,
        "avg_light_loss_pct": round(float(df3_unique["ntl_pct_drop"].mean() * 100), 1) if not df3_unique.empty and not pd.isna(df3_unique["ntl_pct_drop"].mean()) else None,
        "avg_recovery_pct": round(float(df3_unique["recovery_gap_closed_pct"].mean() * 100), 1) if not df3_unique.empty and not pd.isna(df3_unique["recovery_gap_closed_pct"].mean()) else None,
        "median_recovery_time_months": int(df3_unique["recovery_time_months"].median()) if not df3_unique.empty and not pd.isna(df3_unique["recovery_time_months"].median()) else None,
        "countries_with_spillovers": df4["source"].nunique() if not df4.empty else 0
    })
