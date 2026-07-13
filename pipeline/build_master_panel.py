"""Join NTL + ACLED + World Bank into the ISO3-keyed master panel (§2/§3.4).

Grain: one row per (iso3, year, month). NTL and ACLED are already at that
grain; World Bank is annual and broadcast onto every month of its year. A
dense month grid is generated per country so every timeline view has no holes
— missing ACLED counts mean zero events that month, not unknown.

Also builds the geo layer consumed by the choropleth and the spillover
network: ``data/geo/countries.geojson`` (boundary polygons), ``adjacency.json``
(land-border neighbor graph) and ``countries.json`` (the flat country registry
behind ``GET /api/countries``). Prefers a real Natural Earth download +
geopandas ``touches`` join; falls back to simplified synthetic polygons and the
fixture's hand-specified adjacency when either is unavailable.

Usage:
    python -m pipeline.build_master_panel
    python -m pipeline.build_master_panel --geo-mode live
"""
from __future__ import annotations

import argparse
import json

import pandas as pd

import config
from pipeline.utils import FIXTURE_ADJACENCY, FIXTURE_COUNTRIES, get_logger, read_table, write_table

log = get_logger("pipeline.build_master_panel")


# ---------------------------------------------------------------------------
# Master panel
# ---------------------------------------------------------------------------
def _month_grid(iso3s: list[str], start: int, end: int) -> pd.DataFrame:
    periods = pd.period_range(f"{start}-01", f"{end}-12", freq="M")
    grid = pd.MultiIndex.from_product([iso3s, periods], names=["iso3", "period"]).to_frame(index=False)
    grid["year"] = grid["period"].dt.year
    grid["month"] = grid["period"].dt.month
    grid["date"] = grid["period"].dt.to_timestamp()
    return grid.drop(columns="period")


def build_master_panel(ntl: pd.DataFrame, acled: pd.DataFrame, wb: pd.DataFrame,
                        start: int, end: int) -> pd.DataFrame:
    iso3s = sorted(ntl["iso3"].unique())
    # The monthly master only spans where monthly NTL actually exists (VIIRS
    # begins 2014) — don't scaffold years of empty pre-VIIRS rows just because
    # the annual window (for Plot 1) reaches back to 2000.
    if not ntl.empty:
        start = max(start, int(ntl["year"].min()))
        end = min(end, int(ntl["year"].max()))
    grid = _month_grid(iso3s, start, end)

    master = grid.merge(
        ntl[["iso3", "year", "month", "sum_of_lights", "mean_radiance", "valid_obs_count", "area_km2"]],
        on=["iso3", "year", "month"], how="left",
    )

    master = master.merge(
        acled[["iso3", "year", "month", "event_count", "total_fatalities"]],
        on=["iso3", "year", "month"], how="left",
    )
    master[["event_count", "total_fatalities"]] = master[["event_count", "total_fatalities"]].fillna(0)

    # World Bank is ANNUAL; the master panel is monthly. We join on (iso3, year)
    # so each annual value is broadcast (carried forward) across all 12 months
    # of its year. `gdp_carried_forward` flags years whose GDP was itself
    # imputed upstream (missing-year ffill); `gdp_is_annual_broadcast` marks
    # that every monthly GDP figure is a repeat of one annual observation —
    # i.e. within-year monthly variation is not real and shouldn't be modelled.
    wb_cols = list(config.WORLDBANK_INDICATORS.values())
    wb_join_cols = ["iso3", "year"] + wb_cols
    for extra in ("gdp_carried_forward", "population"):  # optional, if the source provides it
        if extra in wb.columns:
            wb_join_cols.append(extra)
    master = master.merge(wb[wb_join_cols], on=["iso3", "year"], how="left")
    if "gdp_carried_forward" in master.columns:
        master["gdp_carried_forward"] = master["gdp_carried_forward"].fillna(False)
    master["gdp_is_annual_broadcast"] = master["gdp_current_usd"].notna()

    return master.sort_values(["iso3", "year", "month"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Geo layer
# ---------------------------------------------------------------------------
def _synthetic_geojson() -> dict:
    features = []
    for c in FIXTURE_COUNTRIES:
        lon, lat = c["lon"], c["lat"]
        half_deg = max(0.4, min(6.0, (c["area_km2"] ** 0.5) / 111 / 2))
        ring = [
            [lon - half_deg, lat - half_deg], [lon + half_deg, lat - half_deg],
            [lon + half_deg, lat + half_deg], [lon - half_deg, lat + half_deg],
            [lon - half_deg, lat - half_deg],
        ]
        features.append({
            "type": "Feature",
            "properties": {"iso3": c["iso3"], "name": c["name"], "region": c["region"]},
            "geometry": {"type": "Polygon", "coordinates": [ring]},
        })
    return {"type": "FeatureCollection", "features": features}


NE_COUNTRIES_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/"
                    "geojson/ne_110m_admin_0_countries.geojson")


def _fetch_all_boundaries() -> dict:
    """Fetch Natural Earth admin-0 boundaries for EVERY country (not just the
    fixture), simplified and keyed by ISO3 with real name + region."""
    import requests
    from shapely.geometry import mapping, shape

    from pipeline.utils import country_name, region_for_iso3

    resp = requests.get(NE_COUNTRIES_URL, timeout=60)
    resp.raise_for_status()
    raw = resp.json()

    features = []
    for feat in raw.get("features", []):
        props = feat.get("properties", {})
        iso3 = props.get("ISO_A3") or props.get("ADM0_A3") or props.get("iso_a3")
        if not iso3 or iso3 == "-99":
            continue
        try:
            geom = shape(feat["geometry"]).buffer(0).simplify(0.08, preserve_topology=True)
        except Exception:
            continue
        features.append({
            "type": "Feature",
            "properties": {"iso3": iso3, "name": country_name(iso3), "region": region_for_iso3(iso3)},
            "geometry": mapping(geom),
        })
    if len(features) < 100:
        raise RuntimeError(f"only matched {len(features)} countries in Natural Earth")
    return {"type": "FeatureCollection", "features": features}


def _adjacency_and_borders(geojson: dict) -> tuple[dict, dict]:
    """Real land-border adjacency + shared border length (degrees) per pair,
    computed from the boundary geometry with shapely. Falls back to the fixture
    adjacency (no border lengths) if shapely/geometry isn't available."""
    try:
        from shapely.geometry import shape

        geoms = {}
        for f in geojson["features"]:
            try:
                geoms[f["properties"]["iso3"]] = shape(f["geometry"]).buffer(0)
            except Exception:
                pass
        items = list(geoms.items())
        adjacency: dict[str, list] = {i: [] for i, _ in items}
        border_len: dict[str, float] = {}

        for a in range(len(items)):
            ia, ga = items[a]
            axmin, aymin, axmax, aymax = ga.bounds
            for b in range(a + 1, len(items)):
                ib, gb = items[b]
                bxmin, bymin, bxmax, bymax = gb.bounds
                # bounding-box prefilter (with a small pad for near-touching borders)
                if axmax < bxmin - 0.1 or bxmax < axmin - 0.1 or aymax < bymin - 0.1 or bymax < aymin - 0.1:
                    continue
                if not ga.intersects(gb.buffer(0.05)):
                    continue
                shared = ga.boundary.intersection(gb.boundary).length
                if shared <= 0 and not ga.touches(gb):
                    # near-touch (NE polygons aren't perfectly topological)
                    shared = ga.buffer(0.05).intersection(gb.boundary).length
                if shared <= 0.02:
                    continue
                adjacency[ia].append(ib)
                adjacency[ib].append(ia)
                border_len[f"{min(ia, ib)}|{max(ia, ib)}"] = round(float(shared), 4)

        return {k: sorted(v) for k, v in adjacency.items()}, border_len
    except Exception as exc:
        log.warning("geometry-based adjacency unavailable (%s); using fixture adjacency", exc)
        return dict(FIXTURE_ADJACENCY), {}


def build_geo(mode: str) -> tuple[dict, dict, dict]:
    """Returns (geojson, adjacency, border_lengths). ``local``/``live`` fetch
    real Natural Earth boundaries and derive real land-border adjacency +
    shared border lengths; ``synthetic`` uses the fixture polygons/adjacency."""
    geojson = None
    if mode in ("live", "local"):
        try:
            geojson = _fetch_all_boundaries()
        except Exception as exc:
            log.warning("boundary fetch unavailable (%s); using synthetic polygons", exc)

    if geojson is None:
        return _synthetic_geojson(), dict(FIXTURE_ADJACENCY), {}
    adjacency, border_len = _adjacency_and_borders(geojson)
    return geojson, adjacency, border_len


def _detect_conflict_onset(acled: pd.DataFrame) -> dict[str, str]:
    """{iso3: onset YYYY-MM-01} for countries with a *major* sustained conflict.

    Runs the shared episode detector on each country's monthly fatalities and
    keeps episodes whose total exceeds ``config.MAJOR_CONFLICT_FATALITIES``, so
    a country is flagged "in conflict" for a real war, not sporadic unrest. The
    onset is the earliest qualifying episode.
    """
    from analysis.baseline import detect_conflict_episodes

    if acled.empty:
        return {}
    df = acled.copy()
    df["date"] = pd.to_datetime(dict(year=df["year"], month=df["month"], day=1))

    onsets: dict[str, str] = {}
    for iso3, g in df.groupby("iso3"):
        g = g.sort_values("date").reset_index(drop=True)
        episodes = detect_conflict_episodes(g, fatalities_col="total_fatalities")
        major = [e for e in episodes if e["total_fatalities"] >= config.MAJOR_CONFLICT_FATALITIES]
        if major:
            onsets[iso3] = pd.Timestamp(min(e["onset_date"] for e in major)).strftime("%Y-%m-01")
    return onsets


def build_countries_registry(master: pd.DataFrame, acled: pd.DataFrame) -> list[dict]:
    """Real registry over every country in the data: name (pycountry), region
    (continent), and conflict onset detected from ACLED."""
    from pipeline.utils import country_name, region_for_iso3

    onsets = _detect_conflict_onset(acled)
    iso3s = sorted(master["iso3"].dropna().unique())
    return [
        {
            "iso3": iso3,
            "name": country_name(iso3),
            "region": region_for_iso3(iso3),
            "has_conflict": iso3 in onsets,
            "conflict_onset": onsets.get(iso3),
        }
        for iso3 in iso3s
    ]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    p.add_argument("--geo-mode", choices=["local", "live", "synthetic"], default="synthetic")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    ntl = read_table(config.NTL_PANEL_PARQUET)
    acled = read_table(config.ACLED_PANEL_PARQUET)
    wb = read_table(config.WORLDBANK_PANEL_PARQUET)

    master = build_master_panel(ntl, acled, wb, args.start, args.end)
    write_table(master, config.MASTER_PANEL_PARQUET)
    log.info("master panel: %d countries x %d months = %d rows",
              master["iso3"].nunique(), master.groupby("iso3").size().max(), len(master))

    geojson, adjacency, border_len = build_geo(args.geo_mode)
    config.GEOJSON_PATH.write_text(json.dumps(geojson))
    config.ADJACENCY_JSON.write_text(json.dumps(adjacency, indent=2))
    config.BORDER_LENGTHS_JSON.write_text(json.dumps(border_len))
    config.COUNTRIES_JSON.write_text(json.dumps(build_countries_registry(master, acled), indent=2))
    n_borders = sum(len(v) for v in adjacency.values()) // 2
    log.info("geo: %d country polygons, %d land-border pairs -> %s",
              len(geojson["features"]), n_borders, config.DATA_GEO_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
