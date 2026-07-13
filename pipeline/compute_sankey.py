"""Compute per-year trade-impact Sankey data for every conflict country.

For each conflict country and each calendar year it is "in conflict" (ACLED
fatalities that year >= config.ANNUAL_CONFLICT_FATALITIES), we extract its top
export commodities that year, the regional destinations of those exports, and
the world-price change of each commodity from the previous year (world unit
price = global export value / global export quantity, year-over-year).

Output: data/processed/plot5_trade_sankey.parquet
    (origin, year, commodity_code, commodity_name, destination,
     trade_value_kusd, world_price_change_pct)
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict

import pandas as pd

import config
from pipeline.utils import get_logger, region_for_iso3, write_table

log = get_logger("pipeline.compute_sankey")

CHUNK = 2_000_000
TOP_COMMODITIES = 6
MIN_YEAR, MAX_YEAR = 2000, 2024


def _code_to_iso3() -> dict[int, str]:
    codes = pd.read_csv(config.DATASET_TRADE_DIR / "country_codes_V202601.csv")
    col = "country_iso3" if "country_iso3" in codes.columns else codes.columns[-1]
    return {int(r["country_code"]): str(r[col]) for _, r in codes.iterrows()
            if pd.notna(r[col]) and str(r[col]) != "nan"}


def _product_codes() -> dict[int, str]:
    codes = pd.read_csv(config.DATASET_TRADE_DIR / "product_codes_HS96_V202601.csv")
    out = {}
    for _, r in codes.iterrows():
        try:
            out[int(r["code"])] = str(r["description"])
        except ValueError:
            pass
    return out


def _active_years() -> dict[str, list[int]]:
    """iso3 -> years with ACLED fatalities >= the annual conflict threshold."""
    ac = pd.read_parquet(config.ACLED_PANEL_PARQUET)
    cy = ac.groupby(["iso3", "year"], as_index=False)["total_fatalities"].sum()
    cy = cy[cy["total_fatalities"] >= config.ANNUAL_CONFLICT_FATALITIES]
    out: dict[str, list[int]] = defaultdict(list)
    for _, r in cy.iterrows():
        y = int(r["year"])
        if MIN_YEAR <= y <= MAX_YEAR:
            out[r["iso3"]].append(y)
    return {k: sorted(v) for k, v in out.items()}


def _short_desc(desc: str) -> str:
    s = desc.split(":")[0].split(",")[0].strip().capitalize()
    return s[:27] + "..." if len(s) > 30 else s


def parse_args(argv=None):
    return argparse.ArgumentParser(description=__doc__).parse_args(argv)


def main(argv=None) -> int:
    parse_args(argv)

    if not config.COUNTRIES_JSON.exists():
        log.error("countries.json missing")
        return 1
    registry = json.loads(config.COUNTRIES_JSON.read_text())
    conflict = {c["iso3"] for c in registry if c.get("has_conflict")}

    active = {iso: yrs for iso, yrs in _active_years().items() if iso in conflict}
    if not active:
        log.warning("no active conflict-years found")
        return 0

    code2iso = _code_to_iso3()
    iso2code = {v: k for k, v in code2iso.items()}
    product_desc = _product_codes()
    region_cache: dict[int, str] = {}

    def region_of(j_code: int) -> str:
        if j_code not in region_cache:
            iso = code2iso.get(j_code)
            region_cache[j_code] = region_for_iso3(iso) if iso else "Other"
        return region_cache[j_code]

    # Years to read: every active year (needs flows + world price) plus the year
    # before each (needs world price only), within the BACI range.
    active_years_all = {y for yrs in active.values() for y in yrs}
    read_years = sorted(y for y in (active_years_all | {y - 1 for y in active_years_all})
                        if MIN_YEAR <= y <= MAX_YEAR)

    world_price: dict[tuple[int, int], float] = {}          # (year, product) -> unit price
    prod_dest: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))  # (iso, year, k) -> {region: value}
    prod_total: dict[tuple, float] = defaultdict(float)     # (iso, year, k) -> total value

    for y in read_years:
        path = config.DATASET_TRADE_DIR / f"BACI_HS96_Y{y}_V202601.csv"
        if not path.exists():
            log.warning("missing %s", path.name)
            continue
        # conflict countries active in THIS year need their export flows captured
        active_codes = {iso2code[iso]: iso for iso in active if y in active[iso] and iso in iso2code}
        wv: dict[int, float] = defaultdict(float)
        wq: dict[int, float] = defaultdict(float)
        log.info("reading %s (active countries: %d) ...", path.name, len(active_codes))

        for chunk in pd.read_csv(path, usecols=["t", "i", "j", "k", "v", "q"], chunksize=CHUNK):
            # world value/quantity per product (all exporters) -> world unit price
            for k, v in chunk.groupby("k")["v"].sum().items():
                wv[int(k)] += float(v)
            for k, q in chunk.groupby("k")["q"].sum().items():
                wq[int(k)] += float(q)
            # export flows for the conflict countries active this year
            if active_codes:
                sub = chunk[chunk["i"].isin(active_codes)]
                if not sub.empty:
                    sub = sub.copy()
                    sub["origin"] = sub["i"].map(active_codes)
                    sub["region"] = sub["j"].map(region_of)
                    for (iso, k, region), v in sub.groupby(["origin", "k", "region"])["v"].sum().items():
                        prod_dest[(iso, y, int(k))][region] += float(v)
                        prod_total[(iso, y, int(k))] += float(v)

        for k in wv:
            if wq[k] > 0:
                world_price[(y, k)] = wv[k] / wq[k]

    # Assemble: top commodities per (country, active year) + YoY world-price change.
    by_iy: dict[tuple, list] = defaultdict(list)
    for (iso, y, k), tot in prod_total.items():
        by_iy[(iso, y)].append((k, tot))

    rows = []
    for iso, yrs in active.items():
        for y in yrs:
            top = sorted(by_iy.get((iso, y), []), key=lambda kv: kv[1], reverse=True)[:TOP_COMMODITIES]
            for k, _tot in top:
                pb, pa = world_price.get((y - 1, k)), world_price.get((y, k))
                pct = ((pa - pb) / pb * 100.0) if (pb and pa and pb > 0) else 0.0
                name = _short_desc(product_desc.get(k, str(k)))
                for region, v in prod_dest[(iso, y, k)].items():
                    if v <= 0:
                        continue
                    rows.append({
                        "origin": iso, "year": y,
                        "commodity_code": k, "commodity_name": name,
                        "destination": region,
                        "trade_value_kusd": round(float(v), 1),
                        "world_price_change_pct": round(float(pct), 1),
                    })

    if not rows:
        log.warning("no sankey rows produced")
        return 0
    df_out = pd.DataFrame(rows)
    write_table(df_out, config.PLOT5_TRADE_SANKEY_PARQUET)
    log.info("trade sankey: %d rows, %d countries, years %d-%d",
             len(df_out), df_out["origin"].nunique(), df_out["year"].min(), df_out["year"].max())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
