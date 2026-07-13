"""World Bank economics -> annual country panel (§3.3).

Schema: iso3, year, gdp_current_usd, gdp_constant_usd, gdp_per_capita,
military_exp_pct_gdp (plus gov_expenditure_pct_gdp, the §4.8 development-
spending proxy).

  * ``--mode local``     reads the hand-downloaded World Bank dump in
                          ``config.DATASET_DIR``: the standard wide bulk-export
                          CSVs (GDP, GDP-per-capita, population — ``Country
                          Code`` is already ISO3) plus the SDMX/Data360
                          WORLD_DEVELPMENT_INDEX.csv for military spend,
                          government spend and constant GDP.
  * ``--mode live``      pulls the public World Bank API (no key required) for
                          the fixture's ISO3 codes.
  * ``--mode synthetic`` (default / automatic fallback) derives GDP from the
                          *already-extracted* NTL panel via a log-log
                          elasticity, so the residual model in
                          ``analysis.residual`` has a real signal to recover.
                          For conflict countries post-onset, GDP is generated
                          from a *damped* blend of the true collapsed lights and
                          the pre-war counterfactual trend — modeling the lag of
                          official statistics behind an unfolding collapse, the
                          "hidden economic damage" the dashboard exposes.

Depends on ``pipeline.gee_extract`` having produced the NTL panel (synthetic mode).

Usage:
    python -m pipeline.ingest_worldbank --mode local
    python -m pipeline.ingest_worldbank --mode live
    python -m pipeline.ingest_worldbank                 # synthetic
"""
from __future__ import annotations

import argparse
import time

import numpy as np
import pandas as pd
import requests

import config
from pipeline.utils import FIXTURE_COUNTRIES, get_logger, read_table, write_table

log = get_logger("pipeline.ingest_worldbank")

API = "https://api.worldbank.org/v2/country/{countries}/indicator/{indicator}"


# ---------------------------------------------------------------------------
# Live path
# ---------------------------------------------------------------------------
def _fetch_indicator_live(countries: str, indicator: str, start_year: int, end_year: int) -> pd.DataFrame:
    rows: list[dict] = []
    page = 1
    while True:
        params = {"format": "json", "date": f"{start_year}:{end_year}", "per_page": 1000, "page": page}
        resp = requests.get(API.format(countries=countries, indicator=indicator), params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
            break
        meta, data = payload[0], payload[1]
        for rec in data:
            if rec["value"] is None:
                continue
            rows.append({"iso3": rec["countryiso3code"], "year": int(rec["date"]),
                         "indicator": indicator, "value": rec["value"]})
        if page >= meta["pages"]:
            break
        page += 1
        time.sleep(0.15)
    return pd.DataFrame(rows)


def _fetch_live(start_year: int, end_year: int) -> pd.DataFrame:
    countries = ";".join(c["iso3"] for c in FIXTURE_COUNTRIES)
    frames = [
        _fetch_indicator_live(countries, code, start_year, end_year)
        for code in config.WORLDBANK_INDICATORS
    ]
    long_df = pd.concat(frames, ignore_index=True)
    if long_df.empty:
        return long_df

    wide = (
        long_df.pivot_table(index=["iso3", "year"], columns="indicator", values="value", aggfunc="first")
        .rename(columns=config.WORLDBANK_INDICATORS)
        .reset_index()
    )
    wide = wide.sort_values(["iso3", "year"])
    value_cols = list(config.WORLDBANK_INDICATORS.values())

    # Carry-forward-with-a-flag: World Bank GDP lags by a year or two for many
    # countries. We forward-fill short gaps so the annual->monthly join stays
    # dense, but flag every year whose GDP we carried forward rather than
    # observed, so downstream analysis can distinguish real from imputed values.
    gdp_missing_before_fill = wide["gdp_current_usd"].isna()
    wide[value_cols] = wide.groupby("iso3", group_keys=False)[value_cols].apply(
        lambda g: g.ffill(limit=2)
    )
    wide["gdp_carried_forward"] = gdp_missing_before_fill & wide["gdp_current_usd"].notna()
    return wide


# ---------------------------------------------------------------------------
# Local dataset path (hand-downloaded World Bank CSVs)
# ---------------------------------------------------------------------------
def _read_wide_wb_csv(path, value_col: str, start_year: int, end_year: int) -> pd.DataFrame:
    """Read one standard World Bank wide bulk-export CSV to long (iso3, year,
    value_col).

    These files carry a 4-row metadata preamble, then a header row of
    ``Country Name, Country Code, Indicator Name, Indicator Code, 1960, ...``.
    ``Country Code`` is already ISO3, so no name mapping is needed.
    """
    df = pd.read_csv(path, skiprows=4, encoding="utf-8-sig")
    year_cols = [c for c in df.columns if c.strip().isdigit() and start_year <= int(c) <= end_year]
    long = df.melt(
        id_vars=["Country Code"], value_vars=year_cols,
        var_name="year", value_name=value_col,
    ).rename(columns={"Country Code": "iso3"})
    long["year"] = long["year"].astype(int)
    long[value_col] = pd.to_numeric(long[value_col], errors="coerce")
    return long.dropna(subset=[value_col])


def _read_wdi_indicators(path, start_year: int, end_year: int) -> pd.DataFrame:
    """Read the SDMX/Data360 WDI dump and pivot the configured indicators
    (military spend, government spend, constant GDP) to wide (iso3, year, ...).
    """
    usecols = ["REF_AREA", "INDICATOR", "TIME_PERIOD", "OBS_VALUE"]
    df = pd.read_csv(path, encoding="utf-8-sig", usecols=usecols, low_memory=False)
    df = df[df["INDICATOR"].isin(config.WDI_INDICATORS)].copy()
    df["year"] = pd.to_numeric(df["TIME_PERIOD"], errors="coerce")
    df = df[(df["year"] >= start_year) & (df["year"] <= end_year)]
    df["OBS_VALUE"] = pd.to_numeric(df["OBS_VALUE"], errors="coerce")
    df["column"] = df["INDICATOR"].map(config.WDI_INDICATORS)
    wide = df.pivot_table(index=["REF_AREA", "year"], columns="column", values="OBS_VALUE", aggfunc="first")
    return wide.reset_index().rename(columns={"REF_AREA": "iso3"})


def _read_local(start_year: int, end_year: int) -> pd.DataFrame:
    """Assemble the canonical annual World Bank panel from the local DataSet."""
    gdp = _read_wide_wb_csv(config.DATASET_WB_GDP_CSV, "gdp_current_usd", start_year, end_year)
    gdp_pc = _read_wide_wb_csv(config.DATASET_WB_GDP_PER_CAPITA_CSV, "gdp_per_capita", start_year, end_year)
    pop = _read_wide_wb_csv(config.DATASET_WB_POPULATION_CSV, "population", start_year, end_year)
    wdi = _read_wdi_indicators(config.DATASET_WB_WDI_CSV, start_year, end_year)

    wide = gdp.merge(gdp_pc, on=["iso3", "year"], how="outer")
    wide = wide.merge(pop, on=["iso3", "year"], how="outer")
    wide = wide.merge(wdi, on=["iso3", "year"], how="outer")

    # Real World Bank ISO3 codes are 3 letters; drop the aggregate rows (WLD,
    # ARB, EUU, income-group codes, ...) that aren't real countries.
    wide = wide[wide["iso3"].str.len() == 3].copy()

    # Canonical numeric columns = the core indicators + the WDI development
    # factors (deduped), all forward-filled and kept.
    value_cols = list(dict.fromkeys(
        list(config.WORLDBANK_INDICATORS.values()) + list(config.WDI_INDICATORS.values())
    ))
    for col in value_cols:
        if col not in wide.columns:
            wide[col] = np.nan

    wide = wide.sort_values(["iso3", "year"])
    gdp_missing_before_fill = wide["gdp_current_usd"].isna()
    wide[value_cols] = wide.groupby("iso3", group_keys=False)[value_cols].apply(
        lambda g: g.ffill(limit=2)
    )
    wide["gdp_carried_forward"] = gdp_missing_before_fill & wide["gdp_current_usd"].notna()

    keep = ["iso3", "year"] + value_cols + ["gdp_carried_forward"]
    if "population" in wide.columns:
        keep.append("population")
    return wide[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Synthetic fallback
# ---------------------------------------------------------------------------
def _synthetic_worldbank_panel(start_year: int, end_year: int, seed: int = 99) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ntl_panel = read_table(config.NTL_PANEL_PARQUET)
    annual_ntl = ntl_panel.groupby(["iso3", "year"], as_index=False)["sum_of_lights"].sum()

    beta = 0.85  # shared log-log elasticity of GDP with respect to lights
    rows: list[dict] = []

    for c in FIXTURE_COUNTRIES:
        iso3 = c["iso3"]
        sub = annual_ntl[annual_ntl["iso3"] == iso3].sort_values("year").reset_index(drop=True)
        if sub.empty:
            continue
        onset_year = pd.Timestamp(c["onset"]).year if c["conflict"] else None

        log_ntl = np.log(sub["sum_of_lights"].clip(lower=1e-3))
        first_ntl_log = log_ntl.iloc[0]
        alpha_c = np.log(c["base_gdp_usd"]) - beta * first_ntl_log
        beta_c = beta * rng.uniform(0.92, 1.08)

        # Peacetime counterfactual trend: fit on pre-onset years (or the whole
        # series for countries with no conflict) so we know what GDP *would*
        # have tracked absent the war.
        pre = sub[sub["year"] < onset_year] if onset_year is not None else sub
        fit_source = pre if len(pre) >= 2 else sub
        trend_slope, trend_intercept = np.polyfit(
            fit_source["year"], np.log(fit_source["sum_of_lights"].clip(lower=1e-3)), 1
        )

        damp = rng.uniform(0.20, 0.40)  # fraction of the true collapse official GDP actually reflects

        for _, row in sub.iterrows():
            year = int(row["year"])
            actual_log_ntl = np.log(max(row["sum_of_lights"], 1e-3))
            counterfactual_log_ntl = trend_slope * year + trend_intercept

            if onset_year is not None and year >= onset_year:
                gdp_input_log_ntl = counterfactual_log_ntl + damp * (actual_log_ntl - counterfactual_log_ntl)
            else:
                gdp_input_log_ntl = actual_log_ntl

            log_gdp = alpha_c + beta_c * gdp_input_log_ntl + rng.normal(0, 0.04)
            gdp_current = float(np.exp(log_gdp))
            gdp_constant = gdp_current * rng.uniform(0.90, 1.0)
            gdp_per_capita = c["base_gdp_pc"] * (gdp_current / c["base_gdp_usd"]) * rng.uniform(0.97, 1.03)
            military_pct = max(c["base_mil_pct"] + rng.normal(0, 0.3), 0.05)
            gov_pct = max(c["base_gov_pct"] + rng.normal(0, 1.0), 1.0)

            rows.append({
                "iso3": iso3, "year": year,
                "gdp_current_usd": round(gdp_current, 2),
                "gdp_constant_usd": round(gdp_constant, 2),
                "gdp_per_capita": round(gdp_per_capita, 2),
                "military_exp_pct_gdp": round(military_pct, 3),
                "gov_expenditure_pct_gdp": round(gov_pct, 3),
                # The fixture reports every year natively, so nothing is carried
                # forward — but the column exists so the schema matches the live
                # path and the annual->monthly join can propagate it uniformly.
                "gdp_carried_forward": False,
            })
    return pd.DataFrame(rows)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=["local", "live", "synthetic"], default="synthetic")
    p.add_argument("--start", type=int, default=config.START_YEAR)
    p.add_argument("--end", type=int, default=config.END_YEAR)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    df = pd.DataFrame()
    if args.mode == "local":
        df = _read_local(args.start, args.end)
    elif args.mode == "live":
        try:
            df = _fetch_live(args.start, args.end)
        except Exception as exc:
            log.warning("live World Bank fetch unavailable (%s); falling back to synthetic", exc)
    if df.empty and args.mode != "local":
        try:
            df = _synthetic_worldbank_panel(args.start, args.end)
        except FileNotFoundError:
            log.error("NTL panel not found — run `python -m pipeline.gee_extract` first")
            return 1

    write_table(df, config.WORLDBANK_PANEL_PARQUET)
    log.info("World Bank panel (%s): %d countries x %d years = %d rows",
              args.mode, df["iso3"].nunique(), df["year"].nunique(), len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
