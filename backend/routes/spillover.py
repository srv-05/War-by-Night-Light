"""
This module provides the 'spillover' blueprint, handling the retrieval of conflict
spillover network data. It serves data about how conflicts in an epicenter country
affect neighboring countries, using precomputed lagged correlation metrics and geographic
centroids for network visualization.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from flask import Blueprint

from backend import db
from backend.routes import envelope

bp = Blueprint("spillover", __name__)


def _centroids() -> dict:
    """
    Computes the geographical center point for each country based on its boundary polygon.
    Returns a mapping from ISO3 country codes to their respective longitude and latitude coordinates.
    """
    out = {}
    for f in db.geojson().get("features", []):
        iso3 = f["properties"].get("iso3")
        coords = f["geometry"]["coordinates"]
        lons, lats = [], []

        def walk(c):
            if isinstance(c[0], (int, float)):
                lons.append(c[0]); lats.append(c[1])
            else:
                for x in c:
                    walk(x)
        try:
            walk(coords)
            out[iso3] = [(min(lons) + max(lons)) / 2, (min(lats) + max(lats)) / 2]
        except Exception:
            pass
    return out


@bp.get("/spillover/<iso3>")
def spillover_network(iso3: str):
    """
    Retrieves the spillover network data for a given country (epicenter).
    Constructs a node-edge graph detailing the effects of the conflict on neighboring states.
    """
    iso3 = iso3.upper()
    edges_df = db.plot4_spillover()
    matched = edges_df[edges_df["source"] == iso3].copy()

    for col in ("onset_date", "window_start", "window_end"):
        if col in matched:
            matched[col] = pd.to_datetime(matched[col]).dt.strftime("%Y-%m-%d")
    edges = matched.replace({np.nan: None}).to_dict(orient="records")

    cen = _centroids()
    spillover_index = edges[0]["epicenter_spillover_index"] if edges else None
    epi_name = edges[0]["source_name"] if edges else iso3

    # Determine the onset year of the conflict for further anomaly calculations.
    onset_year = None
    if edges and edges[0].get("onset_date"):
        try:
            onset_year = int(edges[0]["onset_date"][:4])
        except ValueError:
            pass

    # Retrieve residual data to extract annual anomalies for the network nodes.
    res_df = db.plot1_residual()
    
    def get_anomalies(country_iso3):
        anomalies = {"year1": None, "year2": None, "year3": None}
        if onset_year is not None:
            c_df = res_df[res_df["iso3"] == country_iso3]
            for i in range(1, 4):
                y_df = c_df[c_df["year"] == onset_year + i]
                if not y_df.empty and not pd.isna(y_df.iloc[0]["residual_anomaly"]):
                    anomalies[f"year{i}"] = float(y_df.iloc[0]["residual_anomaly"])
        return anomalies

    epi_anom = get_anomalies(iso3)
    nodes = [{"iso3": iso3, "name": epi_name, "role": "epicenter",
              "lonlat": cen.get(iso3), "own_light_loss": None, 
              "year1_anomaly": epi_anom["year1"], "year2_anomaly": epi_anom["year2"], "year3_anomaly": epi_anom["year3"]}]
    
    for r in edges:
        t_iso = r["target"]
        t_anom = get_anomalies(t_iso)
        nodes.append({
            "iso3": t_iso, "name": r.get("target_name"), "role": "neighbor",
            "lonlat": cen.get(t_iso), "own_light_loss": r.get("own_light_loss"),
            "year1_anomaly": t_anom["year1"], "year2_anomaly": t_anom["year2"], "year3_anomaly": t_anom["year3"]
        })

    return envelope(
        {"nodes": nodes, "edges": edges},
        iso3=iso3, spillover_index=spillover_index,
    )
