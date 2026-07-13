"""ACLED conflict events -> event-level raw table + country-month panel (§3.2).

Raw event schema:
    event_id, iso3, event_date, event_type, sub_event_type, fatalities,
    latitude, longitude, admin1
Aggregated (country x year x month):
    iso3, year, month, event_count, total_fatalities

Three paths:
  * ``--mode local``     reads a hand-downloaded ACLED CSV dump from
                          ``config.DATASET_ACLED_DIR`` (one file per year /
                          range, standard ACLED export columns) and maps it to
                          the canonical schema. Use this for the DataSet folder.
  * ``--mode live``      pages the real ACLED read API (needs ACLED_API_KEY +
                          ACLED_EMAIL in .env — free registration at
                          developer.acleddata.com).
  * ``--mode synthetic`` (default / automatic fallback) generates event-level
                          conflict data for the fixture's conflict epicenters
                          (onset dates match ``gee_extract``'s synthetic NTL
                          shocks, so the anomaly/severity/recovery models see a
                          coherent story), plus a low background rate of unrest.

Usage:
    python -m pipeline.ingest_acled --mode local
    python -m pipeline.ingest_acled --mode live
    python -m pipeline.ingest_acled                 # synthetic
"""
from __future__ import annotations

import argparse
import os
import time

import numpy as np
import pandas as pd
import requests

import config
from pipeline.utils import FIXTURE_COUNTRIES, add_iso3, get_logger, write_table

log = get_logger("pipeline.ingest_acled")

API = "https://api.acleddata.com/acled/read"
FIELDS = [
    "event_id_cnty", "event_date", "event_type", "sub_event_type",
    "country", "fatalities", "latitude", "longitude", "admin1",
]


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------
def _fetch_live(start_year: int, end_year: int) -> pd.DataFrame:
    api_key, email = os.getenv("ACLED_API_KEY"), os.getenv("ACLED_EMAIL")
    if not api_key or not email:
        raise RuntimeError("ACLED_API_KEY / ACLED_EMAIL not set")

    rows: list[dict] = []
    page = 1
    while True:
        params = {
            "key": api_key, "email": email,
            "event_date": f"{start_year}-01-01|{end_year}-12-31",
            "event_date_where": "BETWEEN",
            "fields": "|".join(FIELDS), "limit": 5000, "page": page,
        }
        resp = requests.get(API, params=params, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("error"):
            raise RuntimeError(f"ACLED API error: {payload['error']}")
        data = payload.get("data", [])
        if not data:
            break
        rows.extend(data)
        if len(data) < 5000:
            break
        page += 1
        time.sleep(0.3)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.rename(columns={"event_id_cnty": "event_id"})
    df = add_iso3(df, name_col="country")
    return df[df["iso3"].notna()].copy()


# ---------------------------------------------------------------------------
# Synthetic fallback
# ---------------------------------------------------------------------------
_EVENT_TYPES = [
    ("Battles", "Armed clash", 0.35),
    ("Explosions/Remote violence", "Shelling/artillery/missile attack", 0.30),
    ("Violence against civilians", "Attack", 0.20),
    ("Riots", "Violent demonstration", 0.10),
    ("Strategic developments", "Looting/property destruction", 0.05),
]
_UNREST_TYPES = [
    ("Protests", "Peaceful protest", 0.6),
    ("Riots", "Mob violence", 0.3),
    ("Strategic developments", "Other", 0.1),
]


def _synthetic_acled_events(start_year: int, end_year: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    months = pd.period_range(f"{start_year}-01", f"{end_year}-12", freq="M")
    rows: list[dict] = []
    event_id = 0

    for c in FIXTURE_COUNTRIES:
        iso3, lon, lat = c["iso3"], c["lon"], c["lat"]
        onset = pd.Timestamp(c["onset"]) if c["conflict"] else None
        active_duration = int(rng.integers(30, 84)) if onset is not None else 0  # months of high intensity
        peak_intensity = rng.uniform(150, 500)   # peak monthly fatalities (Poisson mean)
        decay_rate = rng.uniform(0.02, 0.05)

        for period in months:
            date = period.to_timestamp()

            if onset is not None and date >= onset:
                months_since = (date.year - onset.year) * 12 + (date.month - onset.month)
                if months_since <= active_duration:
                    intensity = peak_intensity * np.exp(-decay_rate * months_since) + rng.uniform(5, 20)
                else:
                    intensity = rng.uniform(2, 15)  # post-active-phase residual violence
                types = _EVENT_TYPES
            else:
                intensity = rng.uniform(0.0, 1.5)  # peaceful-country background unrest
                types = _UNREST_TYPES

            n_events = int(rng.poisson(max(intensity / 15.0, 0.05)))
            for _ in range(n_events):
                event_id += 1
                fatalities = int(rng.poisson(max(intensity / max(n_events, 1), 0.1)))
                type_idx = rng.choice(len(types), p=[t[2] for t in types])
                event_type, sub_event_type, _ = types[type_idx]
                day = int(rng.integers(1, 28))
                rows.append({
                    "event_id": f"SYN{event_id:07d}",
                    "iso3": iso3,
                    "event_date": pd.Timestamp(year=date.year, month=date.month, day=day),
                    "event_type": event_type,
                    "sub_event_type": sub_event_type,
                    "fatalities": fatalities,
                    "latitude": round(lat + rng.uniform(-1.2, 1.2), 4),
                    "longitude": round(lon + rng.uniform(-1.2, 1.2), 4),
                    "admin1": f"{c['name']} Region {int(rng.integers(1, 5))}",
                })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Local dataset path (hand-downloaded ACLED CSVs)
# ---------------------------------------------------------------------------
def _read_local(start_year: int, end_year: int) -> pd.DataFrame:
    """Read + concatenate the ACLED CSV dump and map to the canonical schema.

    Handles the standard ACLED export columns (``event_id_cnty``,
    ``event_date``, ``event_type``, ``sub_event_type``, ``country``, ``iso``,
    ``admin1``, ``latitude``, ``longitude``, ``fatalities``). Files may cover a
    single year or a year range; we read every ``.csv`` under
    ``config.DATASET_ACLED_DIR``.
    """
    acled_dir = config.DATASET_ACLED_DIR
    files = sorted(acled_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No ACLED CSVs under {acled_dir}")

    keep = ["event_id_cnty", "event_date", "event_type", "sub_event_type",
            "country", "iso", "admin1", "latitude", "longitude", "fatalities"]
    frames = []
    for path in files:
        # utf-8-sig strips the BOM some ACLED exports carry on the first header.
        df = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        frames.append(df[[c for c in keep if c in df.columns]])
        log.info("read %s (%d rows)", path.name, len(df))
    raw = pd.concat(frames, ignore_index=True)

    raw = raw.rename(columns={"event_id_cnty": "event_id"})
    raw["event_date"] = pd.to_datetime(raw["event_date"], errors="coerce")
    raw = raw[raw["event_date"].notna()]
    raw = raw[(raw["event_date"].dt.year >= start_year) & (raw["event_date"].dt.year <= end_year)]

    # ACLED ships a numeric `iso`; map the country name to a stable ISO3 alpha-3.
    raw = add_iso3(raw, name_col="country")
    raw = raw[raw["iso3"].notna()].copy()

    raw["fatalities"] = pd.to_numeric(raw["fatalities"], errors="coerce").fillna(0)
    for col in ("latitude", "longitude"):
        raw[col] = pd.to_numeric(raw[col], errors="coerce")

    cols = ["event_id", "iso3", "event_date", "event_type", "sub_event_type",
            "fatalities", "latitude", "longitude", "admin1"]
    return raw[[c for c in cols if c in raw.columns]].reset_index(drop=True)


def _restrict_events_to_ntl_countries(events: pd.DataFrame) -> pd.DataFrame:
    """Keep only events for countries present in the NTL panel (the displayable
    universe) so the marker-layer table stays lean; no-op if NTL isn't built yet.
    """
    try:
        from pipeline.utils import read_table

        ntl = read_table(config.NTL_PANEL_PARQUET)
        iso3s = set(ntl["iso3"].unique())
        return events[events["iso3"].isin(iso3s)].reset_index(drop=True)
    except FileNotFoundError:
        return events


def aggregate(events: pd.DataFrame) -> pd.DataFrame:
    """Collapse event-level ACLED rows to the country x year x month grain."""
    df = events.copy()
    df["event_date"] = pd.to_datetime(df["event_date"])
    df["year"] = df["event_date"].dt.year
    df["month"] = df["event_date"].dt.month
    df["fatalities"] = pd.to_numeric(df["fatalities"], errors="coerce").fillna(0)

    agg = (
        df.groupby(["iso3", "year", "month"], as_index=False)
        .agg(event_count=("event_id", "count"), total_fatalities=("fatalities", "sum"))
    )
    return agg.sort_values(["iso3", "year", "month"]).reset_index(drop=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "live", "synthetic"], default="synthetic")
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    events = pd.DataFrame()
    if args.mode == "local":
        events = _read_local(args.start, args.end)
    elif args.mode == "live":
        try:
            events = _fetch_live(args.start, args.end)
        except Exception as exc:
            log.warning("live ACLED fetch unavailable (%s); falling back to synthetic", exc)
    if events.empty:
        events = _synthetic_acled_events(args.start, args.end)

    # The aggregated panel keeps all countries (build_master_panel joins it onto
    # the NTL country universe); the raw events table is trimmed to displayable
    # countries so the marker layer stays lean.
    panel = aggregate(events)
    write_table(panel, config.ACLED_PANEL_PARQUET)
    write_table(_restrict_events_to_ntl_countries(events), config.ACLED_EVENTS_PARQUET)
    log.info("ACLED (%s): %d events, %d country-months across %d countries",
              args.mode, len(events), len(panel), panel["iso3"].nunique())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
