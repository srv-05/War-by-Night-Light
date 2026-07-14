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


def _largest_ring(geom: dict) -> list:
    """Exterior ring of the polygon with the most vertices (the main landmass),
    so island/exclave clutter doesn't drag the position off the mainland."""
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return max(polys, key=lambda p: len(p[0]))[0]


def _centroid_lonlat(geom: dict) -> tuple:
    """(lon, lat, lon_span) of a country's main landmass, robust to the
    antimeridian. Russia's polygon wraps from +180 to -180 (Chukotka), which
    naively averages to ~0 deg E and floats the node into the Arctic; we unwrap
    negative longitudes by +360 before averaging so the centre lands on land."""
    ring = _largest_ring(geom)
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    if max(lons) - min(lons) > 180:  # crosses the antimeridian -> unwrap
        lons = [l + 360 if l < 0 else l for l in lons]
    span = max(lons) - min(lons)
    cx = sum(lons) / len(lons)
    cy = sum(lats) / len(lats)
    if cx > 180:
        cx -= 360
    return cx, cy, span


def _centroids(epicenter: str | None = None) -> dict:
    """iso3 -> [lon, lat] label point for the network.

    Each country sits at its main-landmass centroid, EXCEPT very wide countries
    (Russia, and other >90 deg-span states) whose centroid falls thousands of km
    from the shared border: those snap to the point of their territory nearest
    the epicenter, so e.g. Russia's node sits on western Russia by Ukraine rather
    than in central Siberia (which would also zoom the whole map out to Asia).
    """
    from shapely.geometry import Point, shape
    from shapely.ops import nearest_points

    feats = {f["properties"].get("iso3"): f for f in db.geojson().get("features", [])}

    epi_pt = None
    if epicenter and epicenter in feats:
        try:
            ex, ey, _ = _centroid_lonlat(feats[epicenter]["geometry"])
            epi_pt = Point(ex, ey)
        except Exception:
            epi_pt = None

    out = {}
    for iso3, f in feats.items():
        try:
            cx, cy, span = _centroid_lonlat(f["geometry"])
            if span > 90 and epi_pt is not None and iso3 != epicenter:
                p, _ = nearest_points(shape(f["geometry"]), epi_pt)
                out[iso3] = [round(p.x, 3), round(p.y, 3)]
            else:
                out[iso3] = [round(cx, 3), round(cy, 3)]
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

    cen = _centroids(iso3)
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
