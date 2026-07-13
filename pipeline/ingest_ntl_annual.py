"""Annual harmonized night-time lights -> annual country panel (feeds Plot 1).

Plot 1 (Lights vs GDP residual) is a country-YEAR analysis, so it uses the
harmonized DMSP-VIIRS annual series (a single consistent 2000-2023 record)
rather than aggregating the shorter monthly VIIRS. ``Sum_NTL`` is the summed
radiance over the country — the GDP-correlated quantity — renamed
``sum_of_lights``.

  * ``--mode local``     reads ``Harmonized_NTL_2023/NTL_YYYY.csv``
                          (``ISO3, Country, Year, Mean_NTL, Sum_NTL, ...``;
                          ISO3 already present, no name mapping needed).
  * ``--mode synthetic`` derives an annual series from the synthetic monthly
                          NTL panel (sum over months) so out-of-box runs work.

Output: ``data/interim/ntl_annual.parquet`` (iso3, year, sum_of_lights, ...).

Usage:
    python -m pipeline.ingest_ntl_annual --mode local
"""
from __future__ import annotations

import argparse

import pandas as pd

import config
from pipeline.utils import get_logger, read_table, write_table

log = get_logger("pipeline.ingest_ntl_annual")


def _read_local(start_year: int, end_year: int) -> pd.DataFrame:
    files = sorted(config.DATASET_HARMONIZED_NTL_DIR.glob("NTL_*.csv"))
    if not files:
        raise FileNotFoundError(f"No harmonized NTL CSVs under {config.DATASET_HARMONIZED_NTL_DIR}")

    frames = []
    for path in files:
        df = pd.read_csv(path, encoding="utf-8-sig")
        frames.append(df)
        log.info("read %s (%d rows)", path.name, len(df))
    raw = pd.concat(frames, ignore_index=True)

    raw = raw.rename(columns={
        "ISO3": "iso3", "Country": "country", "Year": "year",
        "Sum_NTL": "sum_of_lights", "Mean_NTL": "mean_ntl",
        "Max_NTL": "max_ntl", "Pixel_Count": "pixel_count",
    })
    raw["year"] = pd.to_numeric(raw["year"], errors="coerce")
    raw["sum_of_lights"] = pd.to_numeric(raw["sum_of_lights"], errors="coerce")
    raw = raw[(raw["year"] >= start_year) & (raw["year"] <= end_year)]
    raw = raw[raw["iso3"].notna() & raw["sum_of_lights"].notna()]

    cols = ["iso3", "country", "year", "sum_of_lights", "mean_ntl", "max_ntl", "pixel_count"]
    return raw[[c for c in cols if c in raw.columns]].reset_index(drop=True)


def _synthetic(start_year: int, end_year: int) -> pd.DataFrame:
    """Annualize the synthetic monthly NTL panel (sum over months per year)."""
    monthly = read_table(config.NTL_PANEL_PARQUET)
    annual = monthly.groupby(["iso3", "year"], as_index=False)["sum_of_lights"].sum()
    return annual[(annual["year"] >= start_year) & (annual["year"] <= end_year)].reset_index(drop=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "synthetic"], default="synthetic")
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "local":
        df = _read_local(args.start, args.end)
    else:
        try:
            df = _synthetic(args.start, args.end)
        except FileNotFoundError:
            log.error("monthly NTL panel not found — run `python -m pipeline.gee_extract` first")
            return 1

    write_table(df, config.NTL_ANNUAL_PARQUET)
    log.info("annual NTL (%s): %d countries x %d years = %d rows",
              args.mode, df["iso3"].nunique(), df["year"].nunique(), len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
