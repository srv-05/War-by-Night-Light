"""Compute trade impact Sankey data.

Extracts top exported commodities for conflict countries before onset,
their destination regions, and the world price change of these commodities
after the conflict onset.

Output: data/processed/plot5_trade_sankey.parquet
"""
import argparse
import glob
import os
import pandas as pd
import json

import config
from pipeline.utils import get_logger, write_table, to_iso3, region_for_iso3

log = get_logger("pipeline.compute_sankey")

CHUNK = 2_000_000

def _code_to_iso3() -> dict[int, str]:
    codes = pd.read_csv(config.DATASET_TRADE_DIR / "country_codes_V202601.csv")
    col = "country_iso3" if "country_iso3" in codes.columns else codes.columns[-1]
    return {int(r["country_code"]): str(r[col]) for _, r in codes.iterrows()
            if pd.notna(r[col]) and str(r[col]) != "nan"}

def _product_codes() -> dict[int, str]:
    codes = pd.read_csv(config.DATASET_TRADE_DIR / "product_codes_HS96_V202601.csv")
    result = {}
    for _, r in codes.iterrows():
        try:
            result[int(r["code"])] = str(r["description"])
        except ValueError:
            pass
    return result

def parse_args():
    p = argparse.ArgumentParser()
    return p.parse_args()

def main():
    args = parse_args()
    
    # Load registry
    if not config.COUNTRIES_JSON.exists():
        log.error("countries.json missing")
        return 1
        
    with open(config.COUNTRIES_JSON) as f:
        registry = json.load(f)
        
    conflict_countries = [c for c in registry if c.get("has_conflict") and c.get("conflict_onset")]
    if not conflict_countries:
        log.warning("No conflict countries found.")
        return 0
        
    code2iso = _code_to_iso3()
    iso2code = {v: k for k, v in code2iso.items()}
    product_desc = _product_codes()
    
    # Group required years for conflict countries
    # Y_before = onset_year - 1
    # Y_after = onset_year + 2
    
    country_years = []
    required_years = set()
    for c in conflict_countries:
        onset_year = int(c["conflict_onset"][:4])
        y_before = onset_year - 1
        y_after = onset_year + 2
        # restrict to valid BACI range
        y_before = max(2000, min(y_before, 2024))
        y_after = max(2000, min(y_after, 2024))
        
        country_code = iso2code.get(c["iso3"])
        if not country_code:
            continue
            
        country_years.append({
            "iso3": c["iso3"],
            "code": country_code,
            "y_before": y_before,
            "y_after": y_after
        })
        required_years.add(y_before)
        required_years.add(y_after)
        
    # Phase 1: Determine top commodities and destination breakdowns in Y_before
    # We only need to read files for y_before
    top_commodities = {} # iso3 -> list of top 5 product codes
    trade_flows = [] # list of dicts (origin, commodity, destination, value)
    
    for y in sorted(list(set([c["y_before"] for c in country_years]))):
        path = config.DATASET_TRADE_DIR / f"BACI_HS96_Y{y}_V202601.csv"
        if not path.exists():
            continue
            
        log.info(f"Reading {path.name} to find top commodities...")
        # We need rows where exporter is one of our target countries for this year
        target_codes = [c["code"] for c in country_years if c["y_before"] == y]
        if not target_codes:
            continue
            
        df_list = []
        for chunk in pd.read_csv(path, usecols=["t", "i", "j", "k", "v"], chunksize=CHUNK):
            subset = chunk[chunk["i"].isin(target_codes)]
            if not subset.empty:
                df_list.append(subset)
                
        if not df_list:
            continue
            
        df = pd.concat(df_list, ignore_index=True)
        
        for code in target_codes:
            country_df = df[df["i"] == code]
            if country_df.empty:
                continue
                
            # Aggregate by product to find top 5
            prod_totals = country_df.groupby("k")["v"].sum().reset_index()
            prod_totals = prod_totals.sort_values("v", ascending=False).head(6)
            top_codes = prod_totals["k"].tolist()
            
            iso3 = code2iso[code]
            top_commodities[iso3] = top_codes
            
            # Now get destination breakdown for these top codes
            top_df = country_df[country_df["k"].isin(top_codes)]
            
            # Map importers to region
            def get_dest(j_code):
                j_iso = code2iso.get(j_code)
                if not j_iso:
                    return "Other"
                # Keep top individual countries or use region
                return region_for_iso3(j_iso)
                
            top_df = top_df.copy()
            top_df["dest"] = top_df["j"].apply(get_dest)
            
            dest_totals = top_df.groupby(["k", "dest"])["v"].sum().reset_index()
            
            for _, row in dest_totals.iterrows():
                trade_flows.append({
                    "origin": iso3,
                    "commodity_code": int(row["k"]),
                    "destination": row["dest"],
                    "trade_value_kusd": float(row["v"])
                })
                
    # Phase 2: Compute world prices for these commodities in y_before and y_after
    # World price = sum(v) / sum(q) for the commodity across the entire world
    all_needed_products = set()
    for codes in top_commodities.values():
        all_needed_products.update(codes)
        
    world_prices = {} # (year, product_code) -> price
    
    for y in sorted(list(required_years)):
        path = config.DATASET_TRADE_DIR / f"BACI_HS96_Y{y}_V202601.csv"
        if not path.exists():
            continue
            
        log.info(f"Reading {path.name} to compute world prices...")
        v_sum = {}
        q_sum = {}
        
        for chunk in pd.read_csv(path, usecols=["t", "k", "v", "q"], chunksize=CHUNK):
            subset = chunk[chunk["k"].isin(all_needed_products)]
            if subset.empty:
                continue
                
            g = subset.groupby("k")[["v", "q"]].sum()
            for k, row in g.iterrows():
                v_sum[k] = v_sum.get(k, 0) + row["v"]
                q_sum[k] = q_sum.get(k, 0) + row["q"]
                
        for k in v_sum:
            if q_sum[k] > 0:
                world_prices[(y, k)] = v_sum[k] / q_sum[k]
                
    # Assemble final dataset
    final_rows = []
    for flow in trade_flows:
        iso3 = flow["origin"]
        k = flow["commodity_code"]
        
        # Find the country's timeline
        c_info = next((c for c in country_years if c["iso3"] == iso3), None)
        if not c_info:
            continue
            
        p_before = world_prices.get((c_info["y_before"], k))
        p_after = world_prices.get((c_info["y_after"], k))
        
        price_change_pct = 0.0
        if p_before and p_after and p_before > 0:
            price_change_pct = ((p_after - p_before) / p_before) * 100.0
            
        # Clean product description (take first part before colon/comma)
        desc = product_desc.get(k, str(k))
        short_desc = desc.split(":")[0].split(",")[0].strip().capitalize()
        if len(short_desc) > 30:
            short_desc = short_desc[:27] + "..."
            
        final_rows.append({
            "origin": iso3,
            "commodity_code": k,
            "commodity_name": short_desc,
            "destination": flow["destination"],
            "trade_value_kusd": flow["trade_value_kusd"],
            "world_price_change_pct": round(price_change_pct, 1)
        })
        
    if final_rows:
        df_out = pd.DataFrame(final_rows)
        write_table(df_out, config.PLOT5_TRADE_SANKEY_PARQUET)
        log.info(f"Wrote Sankey dataset with {len(df_out)} rows.")
    else:
        log.warning("No data found for Sankey dataset.")
        
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
