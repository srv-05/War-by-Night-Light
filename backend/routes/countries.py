"""``GET /api/countries`` — populates every country selector in the frontend."""
from __future__ import annotations

from flask import Blueprint, jsonify

from backend import db
from backend.routes import envelope

bp = Blueprint("countries", __name__)


@bp.get("/countries")
def list_countries():
    rows = [
        {"iso3": c["iso3"], "name": c["name"], "region": c["region"], "has_conflict": c["has_conflict"]}
        for c in db.countries()
    ]
    return envelope(rows, count=len(rows))


@bp.get("/geo/countries")
def countries_geojson():
    """Boundary polygons behind the choropleth (View 1) and network layout
    (View 4). Returned as a raw GeoJSON FeatureCollection rather than the usual
    ``{data, meta}`` envelope, since that's the shape D3/Plotly's geo layers
    expect to consume directly.
    """
    return jsonify(db.geojson())
