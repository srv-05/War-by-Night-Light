"""VIIRS DNB nighttime lights -> tabular country-month panel (§3.1).

Schema (one row per country-month):
    iso3, year, month, sum_of_lights, mean_radiance, valid_obs_count, area_km2

``sum_of_lights`` (the summed radiance over the country polygon) is the
GDP-correlated quantity every downstream model uses — never ``mean_radiance``
for economic comparison (mean radiance is dominated by pixel density /
country shape, not total economic activity).

Extraction paths:
  * ``--mode live``      synchronous GEE pull via getInfo. Fine for a coarse
                          scale / small country set; will time out for all
                          countries at native resolution.
  * ``--mode export``    submits one asynchronous Earth Engine Export task per
                          year (native ``config.VIIRS_SCALE_M`` resolution, ALL
                          countries) writing CSVs to a Google Drive folder.
                          Use this for native 463 m over every country.
  * ``--mode assemble``  reads the exported CSVs from ``--csv-dir`` (once the
                          tasks finish and you've downloaded the Drive folder),
                          maps ISO3, combines them, and writes the parquet.
  * ``--mode synthetic`` (default) deterministic fixture for the countries in
                          ``pipeline.utils.FIXTURE_COUNTRIES`` (growth trend,
                          seasonality, conflict onset shock + scarred recovery).

NOTE: VIIRS monthly data only exists from 2012-04 (VCMCFG) / 2014-01
(VCMSLCFG). The start year is clamped to the collection's real start.

Native-resolution, all-countries workflow:
    python -m pipeline.gee_extract --mode export   --start 2014 --end 2024
    # ... wait for the 11 tasks to finish (code.earthengine.google.com/tasks),
    #     then download the Drive 'ntl_export' folder into data/interim/ntl_csv/
    python -m pipeline.gee_extract --mode assemble --csv-dir data/interim/ntl_csv
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import pandas as pd

import config
from pipeline.utils import FIXTURE_COUNTRIES, get_logger, write_table

log = get_logger("pipeline.gee_extract")

COUNTRIES_ASSET = "FAO/GAUL_SIMPLIFIED_500m/2015/level0"
NAME_PROP = "ADM0_NAME"
EXPORT_FOLDER = "ntl_export"


# ---------------------------------------------------------------------------
# Earth Engine helpers
# ---------------------------------------------------------------------------
def _init_ee():
    import ee

    # Current Earth Engine requires a Cloud project at init. Set GEE_PROJECT in
    # .env (e.g. "ee-yourname"); with a service account, also set
    # GEE_SERVICE_ACCOUNT + GEE_KEY_FILE for headless auth.
    sa, key = os.getenv("GEE_SERVICE_ACCOUNT"), os.getenv("GEE_KEY_FILE")
    project = os.getenv("GEE_PROJECT")
    init_kwargs = {"project": project} if project else {}
    if sa and key:
        ee.Initialize(ee.ServiceAccountCredentials(sa, key), **init_kwargs)
    else:
        # Assumes `earthengine authenticate` has been run once on this machine.
        ee.Initialize(**init_kwargs)
    return ee


def _build_iso3_map(names: list[str]) -> dict[str, str | None]:
    """Map GAUL ADM0_NAME -> ISO3 for every country.

    Explicit overrides for GAUL names that don't match ISO short names, then
    pycountry fuzzy search. Unresolvable names (disputed territories, etc.)
    map to None and are dropped.
    """
    import pycountry

    overrides = {
        "Bolivia (Plurinational State of)": "BOL",
        "Venezuela (Bolivarian Republic of)": "VEN",
        "Iran (Islamic Republic of)": "IRN",
        "Iran  (Islamic Republic of)": "IRN",
        "Democratic Republic of the Congo": "COD",
        "Congo": "COG",
        "Republic of the Congo": "COG",
        "United Republic of Tanzania": "TZA",
        "Republic of Korea": "KOR",
        "Korea, Republic of": "KOR",
        "Democratic People's Republic of Korea": "PRK",
        "Republic of Moldova": "MDA",
        "The former Yugoslav Republic of Macedonia": "MKD",
        "Lao People's Democratic Republic": "LAO",
        "Syrian Arab Republic": "SYR",
        "Viet Nam": "VNM",
        "Brunei Darussalam": "BRN",
        "Cote d'Ivoire": "CIV",
        "Côte d'Ivoire": "CIV",
        "United States of America": "USA",
        "Russian Federation": "RUS",
        "United Kingdom": "GBR",
        "Czech Republic": "CZE",
        "Swaziland": "SWZ",
        "Cape Verde": "CPV",
        "Gambia": "GMB",
        "The Gambia": "GMB",
        "Palestine": "PSE",
        "State of Palestine": "PSE",
        "West Bank": "PSE",
        "Gaza Strip": "PSE",
        "Kosovo": "XKX",
        "Micronesia (Federated States of)": "FSM",
        "Bahamas": "BHS",
        "The Bahamas": "BHS",
    }

    result: dict[str, str | None] = {}
    for name in names:
        if name is None:
            continue
        if name in result:
            continue
        if name in overrides:
            result[name] = overrides[name]
            continue
        try:
            result[name] = pycountry.countries.search_fuzzy(name)[0].alpha_3
        except LookupError:
            result[name] = None  # disputed / non-country -> dropped
    return result


def _countries_with_area(ee):
    """GAUL level-0 with an area_km2 property attached server-side."""
    return ee.FeatureCollection(COUNTRIES_ASSET).map(
        lambda f: f.set("area_km2", f.geometry().area(1).divide(1e6))
    )


def _reducer(ee):
    return (
        ee.Reducer.sum()
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.count(), sharedInputs=True)
    )


def _data_start_year(ee) -> int:
    coll = ee.ImageCollection(config.VIIRS_COLLECTION)
    return int(ee.Date(coll.aggregate_min("system:time_start")).get("year").getInfo())


def _month_image(ee, year: int, month: int):
    start = ee.Date.fromYMD(year, month, 1)
    end = start.advance(1, "month")
    img = ee.ImageCollection(config.VIIRS_COLLECTION).filterDate(start, end).first()
    return ee.Image(img).select([config.VIIRS_RAD_BAND, config.VIIRS_CVG_BAND])


# ---------------------------------------------------------------------------
# Export path (native resolution, all countries, asynchronous)
# ---------------------------------------------------------------------------
def submit_exports(start_year: int, end_year: int, folder: str = EXPORT_FOLDER) -> list:
    """Submit one Export.table.toDrive task per year at native resolution.

    Each task reduces ALL countries for all 12 months of the year and writes
    ntl_<year>.csv to the given Drive folder. Runs server-side (no getInfo
    timeout), so native config.VIIRS_SCALE_M works for every country.
    """
    ee = _init_ee()
    countries = _countries_with_area(ee)
    reducer = _reducer(ee)
    rad, cvg = config.VIIRS_RAD_BAND, config.VIIRS_CVG_BAND

    eff_start = max(start_year, _data_start_year(ee))
    if eff_start > start_year:
        log.warning("VIIRS begins %d; clamping start %d -> %d",
                    _data_start_year(ee), start_year, eff_start)

    selectors = [NAME_PROP, "year", "month", "area_km2",
                 f"{rad}_sum", f"{rad}_mean", f"{cvg}_mean"]

    tasks = []
    for year in range(eff_start, end_year + 1):
        for month in range(1, 13):
            img = _month_image(ee, year, month)
            fc = img.reduceRegions(collection=countries, reducer=reducer,
                                    scale=config.VIIRS_SCALE_M, tileScale=4)
            fc = fc.map(lambda f, y=year, m=month: f.set({"year": y, "month": m}))

            name = f"ntl_{year}_{month:02d}"
            task = ee.batch.Export.table.toDrive(
                collection=fc,
                description=name,
                folder=folder,
                fileNamePrefix=name,
                fileFormat="CSV",
                selectors=selectors,
            )
            task.start()
            tasks.append((year, month, task.id))
            log.info("submitted export %s  (task %s)", name, task.id)

    log.info("%d task(s) submitted -> Drive folder '%s'", len(tasks), folder)
    log.info("monitor at https://code.earthengine.google.com/tasks "
             "or run: earthengine task list")
    return tasks


def assemble_from_csvs(csv_dir: str) -> pd.DataFrame:
    """Combine exported ntl_*.csv files into the country-month panel schema."""
    paths = sorted(glob.glob(os.path.join(csv_dir, "ntl_*.csv")))
    if not paths:
        log.error("no ntl_*.csv files found in %s", csv_dir)
        return pd.DataFrame()
    log.info("assembling %d CSV file(s) from %s", len(paths), csv_dir)

    raw = pd.concat((pd.read_csv(p) for p in paths), ignore_index=True)
    rad, cvg = config.VIIRS_RAD_BAND, config.VIIRS_CVG_BAND

    iso3_by_name = _build_iso3_map(raw[NAME_PROP].dropna().unique().tolist())
    raw["iso3"] = raw[NAME_PROP].map(iso3_by_name)
    raw = raw.dropna(subset=["iso3"])

    df = pd.DataFrame({
        "iso3": raw["iso3"],
        "year": raw["year"].astype(int),
        "month": raw["month"].astype(int),
        "sum_of_lights": raw[f"{rad}_sum"],
        "mean_radiance": raw[f"{rad}_mean"],
        "valid_obs_count": raw[f"{cvg}_mean"],
        "area_km2": raw["area_km2"],
    })
    # Collapse any country split across multiple GAUL polygons into one row.
    df = df.groupby(["iso3", "year", "month"], as_index=False).agg(
        sum_of_lights=("sum_of_lights", "sum"),
        mean_radiance=("mean_radiance", "mean"),
        valid_obs_count=("valid_obs_count", "mean"),
        area_km2=("area_km2", "sum"),
    )
    return df


# ---------------------------------------------------------------------------
# Live path (synchronous; coarse scale / limited country set)
# ---------------------------------------------------------------------------
def _fetch_live(start_year: int, end_year: int) -> pd.DataFrame:
    ee = _init_ee()
    countries = _countries_with_area(ee)

    name_feats = countries.map(
        lambda f: ee.Feature(None, {NAME_PROP: f.get(NAME_PROP)})
    ).getInfo()["features"]
    all_names = [f["properties"].get(NAME_PROP) for f in name_feats]
    iso3_by_name = _build_iso3_map(all_names)
    log.info("resolved ISO3 for %d/%d countries",
             sum(v is not None for v in iso3_by_name.values()), len(iso3_by_name))

    eff_start = max(start_year, _data_start_year(ee))
    if eff_start > start_year:
        log.warning("VIIRS begins; clamping start %d -> %d", start_year, eff_start)

    reducer = _reducer(ee)
    rad, cvg = config.VIIRS_RAD_BAND, config.VIIRS_CVG_BAND

    country_list = countries.toList(countries.size())
    n_countries = country_list.size().getInfo()
    BATCH = 40

    rows: list[dict] = []
    for year in range(eff_start, end_year + 1):
        for month in range(1, 13):
            try:
                img = _month_image(ee, year, month)
                if img.bandNames().size().getInfo() == 0:
                    continue
            except Exception as exc:
                log.warning("skip %04d-%02d image (%s)", year, month, exc)
                continue

            feats: list = []
            for offset in range(0, n_countries, BATCH):
                batch_fc = ee.FeatureCollection(country_list.slice(offset, offset + BATCH))
                try:
                    fc = img.reduceRegions(collection=batch_fc, reducer=reducer,
                                            scale=1000, tileScale=8)
                    feats.extend(fc.getInfo()["features"])
                except Exception as exc:
                    log.warning("  skip %04d-%02d batch @%d (%s)", year, month, offset, exc)

            kept = 0
            for feat in feats:
                props = feat["properties"]
                iso3 = iso3_by_name.get(props.get(NAME_PROP))
                if iso3 is None:
                    continue
                rows.append({
                    "iso3": iso3, "year": year, "month": month,
                    "sum_of_lights": props.get(f"{rad}_sum"),
                    "mean_radiance": props.get(f"{rad}_mean"),
                    "valid_obs_count": props.get(f"{cvg}_mean"),
                    "area_km2": props.get("area_km2"),
                })
                kept += 1
            log.info("%04d-%02d: reduced %d countries", year, month, kept)

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.groupby(["iso3", "year", "month"], as_index=False).agg(
            sum_of_lights=("sum_of_lights", "sum"),
            mean_radiance=("mean_radiance", "mean"),
            valid_obs_count=("valid_obs_count", "mean"),
            area_km2=("area_km2", "sum"),
        )
    return df


# ---------------------------------------------------------------------------
# Synthetic fallback
# ---------------------------------------------------------------------------
def _synthetic_ntl_panel(start_year: int, end_year: int, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    months = pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M")

    rows: list[dict] = []
    for c in FIXTURE_COUNTRIES:
        iso3, base, area = c["iso3"], c["base_ntl"], c["area_km2"]
        onset = pd.Timestamp(c["onset"]) if c["conflict"] else None

        growth = rng.uniform(0.01, 0.04)
        seasonal_amp = rng.uniform(0.03, 0.08)
        noise_sd = rng.uniform(0.02, 0.05)
        recovery_rate = rng.uniform(0.015, 0.035)
        scarring_floor = rng.uniform(0.55, 0.75)
        shock = 1.0

        for i, period in enumerate(months):
            date = period.to_timestamp()
            trend = base * (1 + growth) ** (i / 12.0)
            seasonal = 1 + seasonal_amp * np.sin(2 * np.pi * (date.month - 1) / 12)

            if onset is not None and date >= onset:
                months_since_onset = (date.year - onset.year) * 12 + (date.month - onset.month)
                if months_since_onset == 0:
                    shock = rng.uniform(0.25, 0.45)
                else:
                    shock += recovery_rate * (scarring_floor - shock)
                    if rng.random() < 0.08:
                        shock *= rng.uniform(0.85, 0.97)
                    shock = float(np.clip(shock, 0.05, 1.05))

            value = max(trend * seasonal * shock * (1 + rng.normal(0, noise_sd)), 0.0)
            pixel_count = area / 0.25
            mean_radiance = value / pixel_count * 1000.0

            valid_obs = int(np.clip(rng.normal(24, 3), 0, 30))
            if rng.random() < 0.05:
                valid_obs = int(rng.uniform(0, 5))

            rows.append({
                "iso3": iso3, "year": date.year, "month": date.month,
                "sum_of_lights": round(value, 3),
                "mean_radiance": round(mean_radiance, 6),
                "valid_obs_count": valid_obs,
                "area_km2": area,
            })
    return pd.DataFrame(rows)


def extract(mode: str, start_year: int, end_year: int) -> pd.DataFrame:
    if mode == "live":
        try:
            df = _fetch_live(start_year, end_year)
            if not df.empty:
                return df
            log.warning("live GEE extraction returned no rows; falling back to synthetic")
        except Exception as exc:
            log.warning("live GEE extraction unavailable (%s); falling back to synthetic", exc)
    return _synthetic_ntl_panel(start_year, end_year)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "live", "export", "assemble", "synthetic"], default="synthetic")
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    p.add_argument("--csv-dir", default="data/interim/ntl_csv",
                   help="folder holding exported ntl_*.csv files (assemble mode)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.mode == "export":
        submit_exports(args.start, args.end)
        log.info("exports submitted. When tasks finish, download the Drive "
                 "'%s' folder into %s and run: "
                 "python -m pipeline.gee_extract --mode assemble --csv-dir %s",
                 EXPORT_FOLDER, args.csv_dir, args.csv_dir)
        return 0

    if args.mode == "assemble":
        df = assemble_from_csvs(args.csv_dir)
    elif args.mode == "local":
        # Read the user's monthly VIIRS dump (ntl_YYYY_MM.csv, same schema as an
        # Earth Engine export) directly — no GEE needed.
        df = assemble_from_csvs(str(config.DATASET_VIIRS_NTL_DIR))
        df = df[(df["year"] >= args.start) & (df["year"] <= args.end)]
    else:
        df = extract(args.mode, args.start, args.end)

    if df.empty:
        log.error("no NTL rows produced")
        return 1
    write_table(df, config.NTL_PANEL_PARQUET)
    log.info("NTL panel: %d countries x %d months = %d rows",
              df["iso3"].nunique(), df.groupby("iso3").size().max(), len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())