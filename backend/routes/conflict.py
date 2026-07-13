"""
Defines endpoints that retrieve aggregated conflict data.
Handles logic involving night-light intensity vs. conflict occurrence,
conflict type breakdowns, trade flow diagrams, and key performance indicators.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from flask import Blueprint, request

from backend import db
from backend.routes import envelope

# Set up the Flask blueprint for the conflict API endpoints
bp = Blueprint("conflict", __name__)


@bp.get("/conflict-light-overview")
def conflict_light_overview():
    """
    Retrieves a broad summary comparing light anomaly measurements to conflict occurrences.
    Collects data across all countries that have recorded conflicts.
    Provides peak loss values and cumulative fatality counts.
    """
    # Load light loss dataset and registry mapping
    df = db.plot2_conflict_light().copy()
    reg = db.countries_by_iso3()
    
    # Identify countries registered as having a conflict
    conflict_isos = {iso for iso, c in reg.items() if c.get("has_conflict")}

    # Filter dataframe to only include relevant countries
    sub = df[df["iso3"].isin(conflict_isos)].copy()
    if sub.empty:
        return envelope({"dates": [], "countries": []}, n_countries=0)
    
    # Convert dates to Month-Year string formats
    sub["ym"] = pd.to_datetime(sub["date"]).dt.strftime("%Y-%m")

    # Generate a sorted list of unique months and track their indices
    dates = sorted(sub["ym"].unique())
    pos = {d: i for i, d in enumerate(dates)}

    def _anom(r):
        # Format the adjusted anomaly values unless they're explicitly masked or missing
        return None if (bool(r.masked) or pd.isna(r.anomaly_adj)) else round(float(r.anomaly_adj), 4)

    out = []
    # Process each country individually
    for iso, g in sub.groupby("iso3"):
        # Access country specific metadata from the registry
        c = reg.get(iso, {})
        onset = c.get("conflict_onset")
        onset_ym = onset[:7] if onset else None

        # Build an array mapped to the uniform date index
        arr = [None] * len(dates)
        for r in g.itertuples(index=False):
            arr[pos[r.ym]] = _anom(r)

        # Slice the dataset to calculate metrics starting at the conflict onset
        win = g[g["ym"] >= onset_ym] if onset_ym else g
        loss = (-win["anomaly_smoothed"]).clip(lower=0)
        
        # Append compiled statistics for the current country
        out.append({
            "iso3": iso, "name": c.get("name", iso), "region": c.get("region"),
            "onset": onset, "onset_idx": pos.get(onset_ym),
            "anomaly": arr,
            "fatalities_total": float(win["total_fatalities"].sum()),
            "peak_light_loss": round(float(loss.max()), 4) if loss.notna().any() else 0.0,
            "months_observed": int(win["anomaly_smoothed"].notna().sum()),
        })

    # Sort results in descending order by total fatalities
    out.sort(key=lambda d: d["fatalities_total"], reverse=True)
    return envelope({"dates": dates, "countries": out}, n_countries=len(out))


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

@bp.get("/trade-sankey/<iso3>")
def trade_sankey(iso3: str):
    """
    Constructs node and link structures for visualizing trade impacts through Sankey diagrams.
    """
    # Access trade sankey parquet data
    df = db.plot5_trade_sankey()
    country_data = df[df["origin"] == iso3].copy()
    
    if country_data.empty:
        return envelope({"links": [], "nodes": []}, meta={"country": iso3, "message": "No Sankey trade data."})
    
    nodes = []
    links = []
    
    node_idx = {}
    def get_node(name, meta=None):
        # Append node to list if not present, return node index
        if name not in node_idx:
            node_idx[name] = len(nodes)
            nodes.append({"name": name, "meta": meta or {}})
        return node_idx[name]
    
    link_values = {}
    # Parse dataset rows into connected source and target linkages
    for _, row in country_data.iterrows():
        origin_idx = get_node(row["origin"])
        comm_name = row["commodity_name"]
        comm_meta = {"world_price_change_pct": row["world_price_change_pct"]}
        comm_idx = get_node(comm_name, comm_meta)
        dest_idx = get_node(row["destination"])
        
        # Accumulate trade flow value from origin to commodity
        link_values[(origin_idx, comm_idx)] = link_values.get((origin_idx, comm_idx), 0) + row["trade_value_kusd"]
        # Accumulate trade flow value from commodity to destination
        link_values[(comm_idx, dest_idx)] = link_values.get((comm_idx, dest_idx), 0) + row["trade_value_kusd"]
        
    # Flatten link dictionary back into a standardized list structure
    for (s, t), v in link_values.items():
        links.append({"source": s, "target": t, "value": v})
        
    return envelope({"nodes": nodes, "links": links}, meta={"country": iso3})


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
