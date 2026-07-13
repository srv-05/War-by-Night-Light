"""
This module provides the 'spillover' blueprint, handling the retrieval of conflict
spillover network data. It serves data about how conflicts in an epicenter country
affect neighboring countries, using precomputed lagged correlation metrics and geographic
centroids for network visualization.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from flask import Blueprint, request

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
    Retrieves the spillover network for a given epicenter country and year.

    Plot 4 is now computed per (epicenter, year): every year the country is in
    conflict has its own network. If ``?year=`` is supplied and available it is
    used; otherwise the most recent available year for that country is returned.
    ``available_years`` lets the client offer a year picker.
    """
    iso3 = iso3.upper()
    req_year = request.args.get("year", type=int)
    edges_df = db.plot4_spillover()
    matched = edges_df[edges_df["source"] == iso3].copy()

    if matched.empty:
        return envelope({"nodes": [], "edges": []}, iso3=iso3,
                        year=None, available_years=[], spillover_index=None)

    available_years = sorted(int(y) for y in matched["year"].dropna().unique())
    year_used = req_year if req_year in available_years else (available_years[-1] if available_years else None)
    yr = matched[matched["year"] == year_used].copy() if year_used is not None else matched

    for col in ("window_start", "window_end"):
        if col in yr:
            yr[col] = pd.to_datetime(yr[col]).dt.strftime("%Y-%m-%d")
    edges = yr.replace({np.nan: None}).to_dict(orient="records")

    cen = _centroids()
    spillover_index = edges[0]["epicenter_spillover_index"] if edges else None
    epi_name = edges[0]["source_name"] if edges else iso3

    nodes = [{"iso3": iso3, "name": epi_name, "role": "epicenter",
              "lonlat": cen.get(iso3), "own_light_loss": None}]
    for r in edges:
        nodes.append({
            "iso3": r["target"], "name": r.get("target_name"), "role": "neighbor",
            "lonlat": cen.get(r["target"]), "own_light_loss": r.get("own_light_loss"),
            "own_gdp_loss": r.get("own_gdp_loss"),
        })

    return envelope(
        {"nodes": nodes, "edges": edges},
        iso3=iso3, year=year_used, available_years=available_years,
        spillover_index=spillover_index,
    )
