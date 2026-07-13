"""CEPII BACI bilateral trade -> aggregated (exporter, importer, year) panel.

BACI ships year × exporter × importer × HS6-product rows (``t,i,j,k,v,q``) —
tens of millions of rows per year, ~8 GB total. We stream each year file in
chunks, drop the product dimension, and aggregate to bilateral country-year
trade value (thousand US$). ``i``/``j`` are numeric BACI codes mapped to ISO3
via ``country_codes_*.csv``.

Feeds:
  * Plot 4 (spillover) — pre-conflict bilateral trade *dependence* between an
    epicenter and each neighbor (a real spillover channel).
  * Plot 5 (trade) — country-year export/import totals.

Output: ``data/interim/bilateral_trade.parquet`` (exporter, importer, year, value_kusd).

Usage:
    python -m pipeline.ingest_trade --mode local
"""
from __future__ import annotations

import argparse
import glob
import os

import pandas as pd

import config
from pipeline.utils import get_logger, write_table

log = get_logger("pipeline.ingest_trade")

CHUNK = 2_000_000


def _code_to_iso3() -> dict[int, str]:
    codes = pd.read_csv(config.DATASET_TRADE_DIR / "country_codes_V202601.csv")
    col = "country_iso3" if "country_iso3" in codes.columns else codes.columns[-1]
    return {int(r["country_code"]): str(r[col]) for _, r in codes.iterrows()
            if pd.notna(r[col]) and str(r[col]) != "nan"}


def _aggregate_year(path: str, code2iso: dict, start: int, end: int) -> pd.DataFrame:
    """Stream one BACI year file -> (exporter_iso3, importer_iso3, year, value)."""
    parts: list[pd.DataFrame] = []
    for chunk in pd.read_csv(path, usecols=["t", "i", "j", "v"], chunksize=CHUNK):
        chunk = chunk[(chunk["t"] >= start) & (chunk["t"] <= end)]
        if chunk.empty:
            continue
        g = chunk.groupby(["t", "i", "j"], as_index=False)["v"].sum()
        parts.append(g)
    if not parts:
        return pd.DataFrame(columns=["exporter", "importer", "year", "value_kusd"])
    agg = pd.concat(parts, ignore_index=True).groupby(["t", "i", "j"], as_index=False)["v"].sum()
    agg["exporter"] = agg["i"].map(code2iso)
    agg["importer"] = agg["j"].map(code2iso)
    agg = agg.dropna(subset=["exporter", "importer"])
    agg = agg.rename(columns={"t": "year", "v": "value_kusd"})
    return agg[["exporter", "importer", "year", "value_kusd"]]


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local"], default="local")
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    files = sorted(glob.glob(str(config.DATASET_TRADE_DIR / "BACI_HS96_Y*.csv")))
    if not files:
        log.error("no BACI files under %s", config.DATASET_TRADE_DIR)
        return 1
    code2iso = _code_to_iso3()

    frames = []
    for path in files:
        yr = int(os.path.basename(path).split("_Y")[1][:4])
        if yr < args.start or yr > args.end:
            continue
        agg = _aggregate_year(path, code2iso, args.start, args.end)
        frames.append(agg)
        log.info("%s: %d bilateral pairs", os.path.basename(path), len(agg))

    if not frames:
        log.error("no trade rows in window")
        return 1
    panel = pd.concat(frames, ignore_index=True)
    write_table(panel, config.BILATERAL_TRADE_PARQUET)
    log.info("bilateral trade: %d rows, %d exporters, %d years",
              len(panel), panel["exporter"].nunique(), panel["year"].nunique())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
