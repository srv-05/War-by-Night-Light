"""
This module implements the 'trade' blueprint, which serves data relating to
conflict-induced trade shocks. It provides endpoints to access country-specific
trade metrics, top export commodities, and trade partner realignments over time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from flask import Blueprint

import config
from backend import db
from backend.routes import envelope

bp = Blueprint("trade", __name__)

CAVEATS = [
    "Trade is annual and nominal US$ — indexing to the pre-war baseline and to "
    "the world benchmark nets out the global cycle (2009, 2020) and inflation, "
    "but within-year onset timing is coarse.",
    "Onset is the first *major* ACLED conflict episode; later escalations (e.g. "
    "a 2022 invasion after a 2014 war) are marked separately where a sanctions "
    "regime applies.",
    "BACI under-reports sanctioned/illicit trade, so sanction-driven collapses "
    "read steeper than reality (re-export via third countries is missed).",
]


def _clean(records: list[dict]) -> list[dict]:
    """
    Replaces non-finite numeric values (NaN or infinity) in a list of dictionaries with None.
    This ensures the output is compatible with JSON serialization.
    """
    out = []
    for r in records:
        out.append({k: (None if isinstance(v, float) and not np.isfinite(v) else v) for k, v in r.items()})
    return out


def _partner_realignment(iso3: str, onset_year, min_year: int, max_year: int, name_map: dict) -> dict:
    """
    Computes the shifts in top trading partners for a specific country before and after a conflict onset.
    It returns a list of top partners and their associated trade values for the respective time windows.
    """
    try:
        bt = db.bilateral_trade()
    except FileNotFoundError:
        return {"partners": [], "before_window": None, "after_window": None}

    mine = bt[(bt["exporter"] == iso3) | (bt["importer"] == iso3)].copy()
    if mine.empty:
        return {"partners": [], "before_window": None, "after_window": None}
    mine["partner"] = np.where(mine["exporter"] == iso3, mine["importer"], mine["exporter"])

    span = config.TRADE_BASELINE_YEARS
    if onset_year is not None:
        b0, b1 = onset_year - span, onset_year - 1
        a0, a1 = onset_year + 1, min(max_year, onset_year + span)
    else:
        # Determine the earliest and latest available years to compare for countries without a specific conflict onset.
        b0, b1 = min_year, min_year + span - 1
        a0, a1 = max_year - span + 1, max_year

    def window_avg(y0: int, y1: int) -> dict:
        w = mine[(mine["year"] >= y0) & (mine["year"] <= y1)]
        if w.empty:
            return {}
        return (w.groupby("partner")["value_kusd"].sum() / max(1, y1 - y0 + 1)).to_dict()

    before, after = window_avg(b0, b1), window_avg(a0, a1)
    top = set(sorted(after, key=after.get, reverse=True)[: config.TRADE_TOP_PARTNERS]) | \
        set(sorted(before, key=before.get, reverse=True)[: config.TRADE_TOP_PARTNERS])
    partners = [{
        "iso3": p, "name": name_map.get(p, p),
        "before_kusd": round(before.get(p, 0.0), 1), "after_kusd": round(after.get(p, 0.0), 1),
    } for p in top]
    partners.sort(key=lambda d: d["after_kusd"], reverse=True)
    return {"partners": partners, "before_window": [b0, b1], "after_window": [a0, a1]}

def _commodities_export(iso3: str) -> list[dict]:
    """
    Identifies the top exported commodities for a given country and breaks down the export values by destination.
    """
    try:
        df = db.plot5_trade_sankey()
    except FileNotFoundError:
        return []
    
    country_df = df[df["origin"] == iso3]
    if country_df.empty:
        return []
        
    agg = country_df.groupby(["commodity_code", "commodity_name"]).agg({
        "trade_value_kusd": "sum",
        "world_price_change_pct": "first"
    }).reset_index()
    
    top = agg.sort_values("trade_value_kusd", ascending=False).head(6)
    top_names = top["commodity_name"].tolist()
    
    # Isolate trade flows specific to the top commodities identified above.
    flows = country_df[country_df["commodity_name"].isin(top_names)]
    # Group the filtered data by commodity and destination to summarize total trade values.
    flow_agg = flows.groupby(["commodity_name", "destination"])["trade_value_kusd"].sum().reset_index()
    
    results = []
    for _, row in top.iterrows():
        c_name = row["commodity_name"]
        c_flows = flow_agg[flow_agg["commodity_name"] == c_name]
        
        # Filter destinations to include only those with positive trade values, and sort them in descending order.
        destinations = [{
            "destination": f_row["destination"],
            "trade_value_kusd": round(float(f_row["trade_value_kusd"]), 1)
        } for _, f_row in c_flows.iterrows() if f_row["trade_value_kusd"] > 0]
        
        destinations.sort(key=lambda x: x["trade_value_kusd"], reverse=True)
        
        results.append({
            "name": c_name,
            "trade_value_kusd": round(float(row["trade_value_kusd"]), 1),
            "price_change_pct": round(float(row["world_price_change_pct"]), 1),
            "destinations": destinations
        })
    return results


@bp.get("/trade/<iso3>")
def trade(iso3: str):
    """
    Handles GET requests to provide trade disruption metrics for a specific country.
    Returns timeseries data, partner realignment, commodity details, and a ranking among conflict countries.
    """
    iso3 = iso3.upper()
    panel = db.plot5_trade()
    sub = panel[panel["iso3"] == iso3].sort_values("year")

    if sub.empty:
        return envelope(
            {"series": [], "realignment": {"partners": []}}, iso3=iso3,
            message="No trade data for this country.", caveats=CAVEATS,
        )

    baseline_year = sub["baseline_year"].dropna()
    onset_year = int(baseline_year.iloc[0]) if len(baseline_year) else None
    min_year, max_year = int(sub["year"].min()), int(sub["year"].max())

    # Establish the most significant point of trade disruption, either due to a sanction event or the initial conflict onset.
    sanctions = config.SANCTION_EVENTS.get(iso3, [])
    sanction_years = [s["year"] for s in sanctions if s["year"] <= max_year - 1]
    pivot_year = max(sanction_years) if sanction_years else onset_year
    name_map = dict(zip(panel["iso3"], panel["country"]))
    realignment = _partner_realignment(iso3, pivot_year, min_year, max_year, name_map)
    commodities = _commodities_export(iso3)

    # Compute a ranking of countries by their degree of excess trade loss.
    conf = panel[panel["has_conflict"]].dropna(subset=["excess_trade_loss"])
    ranking = (conf.groupby("iso3")
               .agg(country=("country", "first"),
                    excess_trade_loss=("excess_trade_loss", "first"),
                    is_commodity_exporter=("is_commodity_exporter", "first"))
               .reset_index()
               .sort_values("excess_trade_loss", ascending=False)
               .head(15))

    return envelope(
        {"series": _clean(sub.to_dict(orient="records")), "realignment": realignment, "commodities": commodities},
        iso3=iso3,
        country=sub["country"].iloc[0],
        onset_year=onset_year,
        has_conflict=bool(sub["has_conflict"].iloc[0]),
        is_commodity_exporter=bool(sub["is_commodity_exporter"].iloc[0]),
        excess_trade_loss=(None if not np.isfinite(sub["excess_trade_loss"].iloc[0]) else round(float(sub["excess_trade_loss"].iloc[0]), 1)),
        sanctions=sanctions,
        ranking=_clean(ranking.to_dict(orient="records")),
        caveats=CAVEATS,
    )
