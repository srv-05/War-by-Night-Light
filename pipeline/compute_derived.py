"""Apply the analysis/ primitives (§4) to the master panel and persist the
results the backend serves.

``analysis/*.py`` holds pure functions; this script is the one place they get
run over real data and cached to Parquet, per §6 ("cache expensive
computations... to Parquet").

Produces:
    derived_panel.parquet      country-month: + baseline, anomaly, and the
                                synthetic-control counterfactual for every
                                conflict country (§4.2, 4.3, 4.6). Feeds Views
                                2 and 3.
    residual_panel.parquet     country-year: GDP-NTL log-log residual (§4.4).
                                Feeds View 1.
    recovery_episodes.parquet  one row per conflict episode: severity
                                components + score (§4.5), recovery time +
                                censoring flag (§4.7), recovery-capacity score
                                (§4.8). Feeds View 3.
    spillover_edges.parquet    one row per (epicenter, neighbor, episode):
                                residualized, lagged correlation (§4.9). Feeds
                                View 4.

Usage:
    python -m pipeline.compute_derived
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

import config
from analysis import baseline, residual, severity, spillover, synthetic_control
from pipeline.utils import get_logger, read_table, write_table

log = get_logger("pipeline.compute_derived")


def _load_countries() -> list[dict]:
    return json.loads(config.COUNTRIES_JSON.read_text())


def _load_adjacency() -> dict:
    return json.loads(config.ADJACENCY_JSON.read_text())


def compute(master: pd.DataFrame, countries: list[dict], adjacency: dict,
            residual_input: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    countries_by_iso3 = {c["iso3"]: c for c in countries}
    donor_iso3 = [c["iso3"] for c in countries if not c["has_conflict"]]

    wide_dates = sorted(master["date"].unique())
    wide_lights = master.pivot_table(index="date", columns="iso3", values="sum_of_lights").reindex(wide_dates)
    wide_lights_pos = wide_lights.reset_index(drop=True)

    derived_frames = []
    episode_rows = []
    spillover_rows = []

    for c in countries:
        iso3 = c["iso3"]
        country_df = master[master["iso3"] == iso3].sort_values("date").reset_index(drop=True)
        out = country_df.copy()
        out["baseline"] = np.nan
        out["anomaly"] = np.nan
        out["counterfactual"] = np.nan

        # Synthetic control / recovery need a pre-onset window inside the
        # monthly record. Skip countries with no conflict, and chronic wars
        # whose onset predates the monthly VIIRS series (no clean "before").
        onset_ts = pd.Timestamp(c["conflict_onset"]) if c.get("conflict_onset") else None
        onset_match = country_df.index[country_df["date"] == onset_ts] if onset_ts is not None else []
        if not c["has_conflict"] or onset_ts is None or len(onset_match) == 0 or onset_match[0] == 0:
            derived_frames.append(out)
            continue

        onset_date = onset_ts
        onset_idx = int(onset_match[0])

        baseline_val = baseline.ntl_baseline(country_df, onset_idx, value_col="sum_of_lights")
        out["baseline"] = baseline_val
        out["anomaly"] = baseline.ntl_anomaly(out["sum_of_lights"], baseline_val)

        # --- §4.6 synthetic control -----------------------------------------
        target = wide_lights_pos[iso3]
        donor_cols = [d for d in donor_iso3 if d in wide_lights_pos.columns]
        donors = wide_lights_pos[donor_cols]
        pre_mask = pd.Series([d < onset_date for d in wide_dates])

        sc = synthetic_control.fit_synthetic_control(target, donors, pre_mask)
        out["counterfactual"] = sc["counterfactual"].to_numpy()

        # --- §4.7 recovery time ----------------------------------------------
        rec = synthetic_control.recovery_time(target, sc["counterfactual"], onset_idx)
        trough_val = rec["trough_value"]
        ntl_pct_drop = (
            float((baseline_val - trough_val) / baseline_val)
            if np.isfinite(baseline_val) and baseline_val != 0 and np.isfinite(trough_val)
            else np.nan
        )

        end_idx = rec["recovery_idx"] if rec["recovery_idx"] is not None else len(country_df) - 1
        episode_fatalities = float(country_df["total_fatalities"].iloc[onset_idx:end_idx + 1].sum())
        top_donors = sc["weights"].sort_values(ascending=False)
        top_donors = top_donors[top_donors > 0.01]

        episode_rows.append({
            "iso3": iso3, "country": c["name"], "region": c["region"],
            "onset_date": onset_date,
            "onset_idx": onset_idx,
            "trough_date": country_df["date"].iloc[rec["trough_idx"]] if rec["trough_idx"] is not None else pd.NaT,
            "baseline_ntl": baseline_val,
            "trough_ntl": trough_val,
            "ntl_pct_drop": ntl_pct_drop,
            "total_fatalities": episode_fatalities,
            "recovery_date": country_df["date"].iloc[rec["recovery_idx"]] if rec["recovery_idx"] is not None else pd.NaT,
            "recovery_time_months": rec["recovery_time_months"],
            "blackout_duration_months": rec["recovery_time_months"],
            "censored": rec["censored"],
            "pre_fit_rmse": sc["pre_fit_rmse"],
            "donor_weights": json.dumps({k: round(float(v), 4) for k, v in top_donors.items()}),
        })

        # --- §4.9 spillover: this episode's epicenter -> its neighbors --------
        neighbors = [n for n in adjacency.get(iso3, []) if n in countries_by_iso3]
        if neighbors:
            window_end_idx = min(
                len(wide_dates) - 1,
                end_idx + config.SPILLOVER_POST_BUFFER_MONTHS,
            )
            window_mask = pd.Series(False, index=range(len(wide_dates)))
            window_mask.iloc[onset_idx:window_end_idx + 1] = True

            anomaly_ref = spillover.anomaly_reference_matrix(wide_lights_pos, [iso3, *neighbors], onset_idx)
            net = spillover.compute_spillover_edges(anomaly_ref, iso3, neighbors, window_mask)
            for edge in net["edges"]:
                spillover_rows.append({
                    **edge,
                    "onset_date": onset_date,
                    "window_start": wide_dates[onset_idx],
                    "window_end": wide_dates[window_end_idx],
                })

        derived_frames.append(out)

    derived_panel = pd.concat(derived_frames, ignore_index=True).sort_values(["iso3", "date"]).reset_index(drop=True)

    # --- §4.4 GDP-NTL residual (country-year, Plot 1) ------------------------
    # Uses the annual harmonized NTL series (a single consistent 2000-2023
    # record) joined to constant-USD GDP + population, with a robust,
    # population-controlled fit. Falls back to aggregating the monthly master
    # only if no annual input was supplied.
    if residual_input is None or residual_input.empty:
        residual_input = master.groupby(["iso3", "year"], as_index=False).agg(
            sum_of_lights=("sum_of_lights", "sum"),
            gdp_constant_usd=("gdp_constant_usd", "first"),
            population=("population", "first") if "population" in master.columns else ("sum_of_lights", "size"),
        )
    residual_panel = residual.fit_gdp_ntl_residual(
        residual_input, gdp_col="gdp_constant_usd", ntl_col="sum_of_lights", pop_col="population"
    )

    # --- §4.5 severity + §4.8 recovery capacity, on the episode table --------
    episodes = pd.DataFrame(episode_rows)
    episodes_scored = severity.severity_index(episodes) if len(episodes) else episodes
    if len(episodes_scored):
        capacity_inputs = master.groupby("iso3", as_index=False)[
            ["military_exp_pct_gdp", "gov_expenditure_pct_gdp"]
        ].mean()
        episodes_scored = episodes_scored.merge(capacity_inputs, on="iso3", how="left")
        episodes_scored["recovery_capacity_score"] = synthetic_control.recovery_capacity_score(episodes_scored)

    spillover_edges = pd.DataFrame(spillover_rows)

    return {
        "master": master,
        "derived_panel": derived_panel,
        "residual_panel": residual_panel,
        "recovery_episodes": episodes_scored,
        "spillover_edges": spillover_edges,
    }


# ---------------------------------------------------------------------------
# Assemble the five per-plot files (one self-contained file per view, §5)
# ---------------------------------------------------------------------------
# Minimal event fields the View 2 marker layer needs (keep plot2 lean).
_MARKER_FIELDS = ["event_date", "event_type", "sub_event_type", "fatalities", "latitude", "longitude"]

# Per-country episode columns (View 3 timeline shading + recovery-tradeoff bubble).
# The episode's own fatalities total is renamed episode_total_fatalities so it
# doesn't collide with the monthly series' total_fatalities in plot3.
_EPISODE_COLS = [
    "onset_date", "trough_date", "recovery_date", "ntl_pct_drop", "episode_total_fatalities",
    "recovery_time_months", "blackout_duration_months", "censored", "severity_score",
    "component_ntl_pct_drop", "component_total_fatalities", "component_blackout_duration_months",
    "recovery_capacity_score",
]


# Cap the marker layer per country-month (thousands of ACLED points can't be
# read on a map anyway; keep the deadliest so the file stays small and legible).
_MARKER_MAX_PER_MONTH = 150


def _events_json_by_month(acled_events: pd.DataFrame) -> dict:
    """{(iso3, year, month): json-string list of marker dicts} for View 2,
    capped to the deadliest ``_MARKER_MAX_PER_MONTH`` events per country-month."""
    if acled_events is None or acled_events.empty:
        return {}
    ev = acled_events.copy()
    ev["event_date"] = pd.to_datetime(ev["event_date"])
    ev["year"] = ev["event_date"].dt.year
    ev["month"] = ev["event_date"].dt.month
    ev["fatalities"] = pd.to_numeric(ev.get("fatalities"), errors="coerce").fillna(0)
    ev["event_date"] = ev["event_date"].dt.strftime("%Y-%m-%d")
    cols = [c for c in _MARKER_FIELDS if c in ev.columns]
    out: dict = {}
    for (iso3, year, month), grp in ev.groupby(["iso3", "year", "month"]):
        if len(grp) > _MARKER_MAX_PER_MONTH:
            grp = grp.nlargest(_MARKER_MAX_PER_MONTH, "fatalities")
        out[(iso3, int(year), int(month))] = grp[cols].replace({np.nan: None}).to_dict(orient="records")
    return out


def _enrich_country_lights(g: pd.DataFrame) -> pd.DataFrame:
    """Attach deseasonalized lights + trailing-baseline anomaly (+ smoothed) to
    one country's date-sorted monthly frame. Shared by Plot 2 and Plot 3 so the
    'normal' is defined once."""
    threshold = config.CONFLICT_FATALITY_THRESHOLD
    win = config.BASELINE_WINDOW_MONTHS
    g = g.sort_values("date").reset_index(drop=True)
    peace = g["total_fatalities"].fillna(0) < threshold

    factors = baseline.seasonal_factors(g, value_col="sum_of_lights", month_col="month", peacetime_mask=peace)
    des = baseline.deseasonalize(g, factors, value_col="sum_of_lights", month_col="month")
    g["ntl_deseasonalized"] = des
    trailing = des.rolling(window=win, min_periods=max(3, win // 2)).median().shift(1)
    g["baseline"] = trailing
    g["anomaly"] = (des - trailing) / trailing
    g["anomaly_smoothed"] = g["anomaly"].rolling(3, center=True, min_periods=1).median()
    g["masked"] = g["valid_obs_count"] < config.VALID_OBS_MIN_COUNT
    return g


def _detect_shock_onset(g: pd.DataFrame, min_drop: float = 0.20) -> int | None:
    """Positional onset index of the *largest sustained* deseasonalized light
    decline (a data-driven shock), or None if the country never drops by
    ``min_drop``. Anchors onset to the real light collapse (Ukraine 2022), not
    the earliest fatality flare (which mislabelled Ukraine as 2018)."""
    a = g["anomaly_smoothed"]
    if a.notna().sum() < 6 or a.min() > -min_drop:
        return None
    trough = int(a.idxmin())
    # Walk back to the last month the country was near its normal (>= -5%).
    onset = trough
    for i in range(trough - 1, -1, -1):
        if pd.isna(a.iloc[i]) or a.iloc[i] >= -0.05:
            onset = i + 1
            break
        onset = i
    return onset if onset < trough else max(trough - 1, 0)


def _dominant_conflict_type(events_iso3: pd.DataFrame, start_date, end_date) -> str:
    """Fatality-weighted modal ACLED event_type over [start, end] for a country."""
    if events_iso3.empty or "event_type" not in events_iso3.columns:
        return "Unknown"
    ev = events_iso3[(events_iso3["event_date"] >= start_date) & (events_iso3["event_date"] <= end_date)]
    if ev.empty:
        return "Unknown"
    weight = ev.groupby("event_type")["fatalities"].sum()
    if weight.sum() == 0:  # no fatalities in window -> fall back to event counts
        weight = ev.groupby("event_type").size()
    return str(weight.idxmax())


def _build_plot2(master: pd.DataFrame, acled_events: pd.DataFrame, countries: list[dict]) -> pd.DataFrame:
    """Plot 2 (conflict & light), for EVERY country: deseasonalized, trailing-
    baseline anomaly, smoothed, cloud-masked, with that month's (capped) events.
    """
    events_by_month = _events_json_by_month(acled_events)

    out_frames = []
    for iso3, g in master.groupby("iso3"):
        g = _enrich_country_lights(g)
        g["events_json"] = [
            json.dumps(events_by_month.get((iso3, int(r.year), int(r.month)), []))
            for r in g.itertuples(index=False)
        ]
        out_frames.append(g)

    plot2 = pd.concat(out_frames, ignore_index=True)

    # Remove a common regional time factor. The deseasonalized anomaly still
    # carries a shared component — VIIRS calibration drift and noisy low-light
    # regions (e.g. a synchronized +0.74 median spike across the Sahel in early
    # 2017) — that is NOT conflict. Subtract the per-region median that month
    # (falling back to the global median where a region is too small to be
    # robust), so each series keeps only its idiosyncratic, conflict-relevant
    # signal — the same common-factor removal used for spillover (§4.9).
    # ``anomaly_adj`` is what View 2 reads.
    region_by = {c["iso3"]: c.get("region", "Other") for c in countries}
    plot2["region"] = plot2["iso3"].map(region_by).fillna("Other")
    plot2["_v"] = plot2["anomaly_smoothed"].where(~plot2["masked"].fillna(False))
    reg_med = plot2.groupby(["region", "date"])["_v"].transform("median")
    reg_n = plot2.groupby(["region", "date"])["_v"].transform("count")
    glob_med = plot2.groupby("date")["_v"].transform("median")
    plot2["anomaly_common"] = reg_med.where(reg_n >= 5, glob_med)
    plot2["anomaly_adj"] = plot2["anomaly_smoothed"] - plot2["anomaly_common"]
    plot2 = plot2.drop(columns="_v")

    # Blank non-physical low-light artifacts: a zero/negative monthly total, or an
    # implausible >100% one-month brightening (stray light / aurora in the early
    # VIIRS years). War darkens, so these upward spikes are never conflict —
    # leaving them unblanked painted a spurious bright band across the Sahel in
    # 2016–17. A genuine blackout (large NEGATIVE anomaly) is kept but floored at
    # a full 100% loss.
    artifact = (plot2["sum_of_lights"] <= 0) | (plot2["anomaly_smoothed"] > config.ANOMALY_MAX_GAIN)
    plot2.loc[artifact, "anomaly_adj"] = np.nan
    plot2["anomaly_adj"] = plot2["anomaly_adj"].clip(lower=-1.0)

    cols = ["iso3", "year", "month", "date", "sum_of_lights", "ntl_deseasonalized",
            "baseline", "anomaly", "anomaly_smoothed", "anomaly_common", "anomaly_adj",
            "event_count", "total_fatalities", "valid_obs_count", "masked", "events_json"]
    return plot2[[c for c in cols if c in plot2.columns]]


def _build_plot3(master: pd.DataFrame, acled_events: pd.DataFrame,
                 worldbank: pd.DataFrame, countries: list[dict]) -> pd.DataFrame:
    """Plot 3 (shock vs recovery, reframed): how a country's *development
    profile* and the *type* of conflict shape its recovery.

    For each country we detect the dominant NTL shock, fit an INDEXED synthetic
    control (so the counterfactual matches trajectory shape, not absolute level),
    measure recovery as the % of the light gap closed within 24 months, tag the
    dominant conflict type, and attach the country's pre-conflict development
    factors (GDP/capita, health, education, governance, electricity, capital,
    military). Returns the country-month series (observed + counterfactual +
    deseasonalized) with the per-country episode summary broadcast on.
    """
    name_by = {c["iso3"]: c["name"] for c in countries}
    region_by = {c["iso3"]: c["region"] for c in countries}
    dev_cols = [c for c in config.DEVELOPMENT_FACTORS if c in worldbank.columns]

    # Enrich every country once; build a wide deseasonalized matrix for donors.
    enriched: dict[str, pd.DataFrame] = {}
    dates = sorted(master["date"].unique())
    wide_des = pd.DataFrame(index=range(len(dates)))
    date_pos = {d: i for i, d in enumerate(dates)}
    for iso3, g in master.groupby("iso3"):
        g = _enrich_country_lights(g)
        enriched[iso3] = g
        col = pd.Series(np.nan, index=range(len(dates)))
        for r in g.itertuples(index=False):
            col.iloc[date_pos[r.date]] = r.ntl_deseasonalized
        wide_des[iso3] = col

    # Detect shocks; donors = countries with NO detected shock (stable lights).
    onsets = {iso3: _detect_shock_onset(g) for iso3, g in enriched.items()}
    donor_cols = [iso3 for iso3, o in onsets.items() if o is None and wide_des[iso3].notna().sum() > 24]

    # ACLED event-level with parsed dates, for the conflict-type tag.
    ev_all = acled_events.copy()
    if not ev_all.empty:
        ev_all["event_date"] = pd.to_datetime(ev_all["event_date"])
        ev_all["fatalities"] = pd.to_numeric(ev_all.get("fatalities"), errors="coerce").fillna(0)

    series_frames = []
    episode_rows = []
    for iso3, g in enriched.items():
        g = g.copy()
        g["counterfactual"] = np.nan
        onset_idx = onsets[iso3]

        if onset_idx is not None and onset_idx > 0:
            target = wide_des[iso3]
            donors = wide_des[[d for d in donor_cols if d != iso3]]
            pre_mask = pd.Series([i < onset_idx for i in range(len(dates))])
            sc = synthetic_control.fit_synthetic_control(target, donors, pre_mask)
            cf = sc["counterfactual"]
            # Map counterfactual (positional over `dates`) back onto this country's rows.
            g["counterfactual"] = [
                float(cf.iloc[date_pos[d]]) if pd.notna(cf.iloc[date_pos[d]]) else np.nan
                for d in g["date"]
            ]

            # Smooth both series (3-mo median) before measuring trough/drop/
            # recovery, so a single noisy low month can't manufacture a "100%
            # blackout" — important for low-light, cloud-prone countries.
            obs_des = g["ntl_deseasonalized"].reset_index(drop=True).rolling(3, center=True, min_periods=1).median()
            cf_country = g["counterfactual"].reset_index(drop=True).rolling(3, center=True, min_periods=1).median()
            g_onset_pos = int(g.index[g["date"] == dates[onset_idx]][0]) if dates[onset_idx] in set(g["date"]) else 0
            rec = synthetic_control.gap_closed_recovery(obs_des, cf_country, g_onset_pos, horizon_months=24)
            rec_time = synthetic_control.recovery_time(obs_des, cf_country, g_onset_pos)

            onset_date = pd.Timestamp(dates[onset_idx])
            trough_pos = rec.get("trough_idx")
            trough_date = g["date"].iloc[trough_pos] if trough_pos is not None else pd.NaT
            end_date = g["date"].iloc[-1]

            ev_iso = ev_all[ev_all["iso3"] == iso3] if not ev_all.empty else pd.DataFrame()
            conflict_type = _dominant_conflict_type(ev_iso, onset_date, end_date)
            episode_fatalities = float(g[g["date"] >= onset_date]["total_fatalities"].sum())

            # Pre-conflict development profile: the year before onset.
            dev = {}
            if dev_cols:
                wb_pre = worldbank[(worldbank["iso3"] == iso3) & (worldbank["year"] <= onset_date.year - 1)]
                if len(wb_pre):
                    last = wb_pre.sort_values("year").iloc[-1]
                    dev = {c: (float(last[c]) if pd.notna(last[c]) else None) for c in dev_cols}
            # GDP per capita may live under WORLDBANK_INDICATORS too.
            if "gdp_per_capita" not in dev:
                wb_pre = worldbank[(worldbank["iso3"] == iso3) & (worldbank["year"] <= onset_date.year - 1)]
                if len(wb_pre) and "gdp_per_capita" in worldbank.columns:
                    dev["gdp_per_capita"] = float(wb_pre.sort_values("year").iloc[-1]["gdp_per_capita"]) \
                        if pd.notna(wb_pre.sort_values("year").iloc[-1]["gdp_per_capita"]) else None

            episode_rows.append({
                "iso3": iso3, "country": name_by.get(iso3, iso3), "region": region_by.get(iso3, "Other"),
                "onset_date": onset_date, "trough_date": trough_date,
                "conflict_type": conflict_type,
                "ntl_pct_drop": rec["ntl_pct_drop"],
                "recovery_gap_closed_pct": rec["gap_closed_pct"],
                "months_observed": rec["months_observed"],
                "recovery_time_months": rec_time.get("recovery_time_months"),
                "censored": rec["censored"],
                "episode_total_fatalities": episode_fatalities,
                "pre_fit_rmse": sc["pre_fit_rmse"],
                **dev,
            })
        series_frames.append(g)

    plot3 = pd.concat(series_frames, ignore_index=True)

    # Severity from the episodes (depth + log fatalities + duration proxy).
    episodes = pd.DataFrame(episode_rows)
    if len(episodes):
        episodes["blackout_duration_months"] = episodes["months_observed"]
        scored = severity.severity_index(
            episodes, drop_col="ntl_pct_drop",
            fatalities_col="episode_total_fatalities",
            duration_col="blackout_duration_months",
        )
        episodes = scored

    # Broadcast the per-country episode summary onto the monthly series.
    ep_cols = ["iso3", "country", "region", "onset_date", "trough_date", "conflict_type",
               "ntl_pct_drop", "recovery_gap_closed_pct", "months_observed", "recovery_time_months", "censored",
               "episode_total_fatalities", "severity_score"] + dev_cols
    if "gdp_per_capita" in episodes.columns and "gdp_per_capita" not in ep_cols:
        ep_cols.append("gdp_per_capita")
    ep_cols = [c for c in ep_cols if c in episodes.columns]
    if len(episodes):
        plot3 = plot3.merge(episodes[ep_cols], on="iso3", how="left")

    keep = ["iso3", "year", "month", "date", "sum_of_lights", "ntl_deseasonalized",
            "counterfactual", "anomaly", "total_fatalities"] + [c for c in ep_cols if c != "iso3"]
    return plot3[[c for c in keep if c in plot3.columns]]


def _corr_pvalue(r: float, n: int) -> float:
    """Two-sided p-value for a Pearson correlation ``r`` on ``n`` points."""
    if n is None or n < 3 or not np.isfinite(r) or abs(r) >= 1.0:
        return 1.0
    from scipy import stats

    t = r * np.sqrt((n - 2) / max(1e-9, 1 - r * r))
    return float(2 * stats.t.sf(abs(t), n - 2))


def _trade_lookups(bilateral: pd.DataFrame):
    """Return (pair_value[(a,b,year)] , total_trade[(c,year)]) from bilateral trade."""
    if bilateral is None or bilateral.empty:
        return {}, {}
    pair = {(r.exporter, r.importer, int(r.year)): float(r.value_kusd) for r in bilateral.itertuples(index=False)}
    exp = bilateral.groupby(["exporter", "year"])["value_kusd"].sum()
    imp = bilateral.groupby(["importer", "year"])["value_kusd"].sum()
    total: dict = {}
    for (c, y), v in exp.items():
        total[(c, int(y))] = total.get((c, int(y)), 0.0) + float(v)
    for (c, y), v in imp.items():
        total[(c, int(y))] = total.get((c, int(y)), 0.0) + float(v)
    return pair, total


def _trade_dependence(pair: dict, total: dict, neighbor: str, epicenter: str, year: int) -> float | None:
    """Fraction of the neighbor's total trade that is with the epicenter (both
    directions) in ``year`` — how exposed the neighbor is via trade."""
    tot = total.get((neighbor, year))
    if not tot:
        return None
    with_epi = pair.get((neighbor, epicenter, year), 0.0) + pair.get((epicenter, neighbor, year), 0.0)
    return float(with_epi / tot) if tot > 0 else None


def _build_plot4(master: pd.DataFrame, adjacency: dict, border_lengths: dict,
                 bilateral_trade: pd.DataFrame, countries: list[dict]) -> pd.DataFrame:
    """Plot 4 (spillover), for every real conflict epicenter.

    Edge = residualized, lagged NTL-anomaly correlation between the epicenter and
    a land-border neighbor (regional common factor partialled out), enriched
    with: the neighbor's OWN light loss during the window, statistical
    significance, and exposure (shared border length + pre-war trade
    dependence). A per-epicenter spillover index summarizes total exported loss.
    """
    from analysis import spillover as sp

    name_by = {c["iso3"]: c["name"] for c in countries}
    # Epicenters must be actual conflict countries — the NTL-shock detector also
    # fires on non-conflict crashes (e.g. South Africa's load-shedding power
    # crisis rippling through the shared regional grid), which isn't *conflict*
    # spillover.
    conflict_isos = {c["iso3"] for c in countries if c.get("has_conflict")}
    pair_value, total_trade = _trade_lookups(bilateral_trade)

    enriched: dict[str, pd.DataFrame] = {}
    dates = sorted(master["date"].unique())
    date_pos = {d: i for i, d in enumerate(dates)}
    wide_des = pd.DataFrame(index=range(len(dates)))
    for iso3, g in master.groupby("iso3"):
        g = _enrich_country_lights(g)
        enriched[iso3] = g
        col = pd.Series(np.nan, index=range(len(dates)))
        for r in g.itertuples(index=False):
            col.iloc[date_pos[r.date]] = r.ntl_deseasonalized
        wide_des[iso3] = col
    wide_pos = wide_des.reset_index(drop=True)

    onsets = {iso3: _detect_shock_onset(g) for iso3, g in enriched.items()}
    buffer = config.SPILLOVER_POST_BUFFER_MONTHS

    edge_rows = []
    for epi, onset_idx in onsets.items():
        if onset_idx is None or onset_idx == 0 or epi not in conflict_isos:
            continue
        neighbors = [n for n in adjacency.get(epi, []) if n in wide_pos.columns]
        if not neighbors:
            continue

        onset_date = pd.Timestamp(dates[onset_idx])
        window_end_idx = min(len(dates) - 1, onset_idx + buffer + 12)
        window_mask = pd.Series(False, index=range(len(dates)))
        window_mask.iloc[onset_idx:window_end_idx + 1] = True

        anomaly_ref = sp.anomaly_reference_matrix(wide_pos, [epi, *neighbors], onset_idx)
        net = sp.compute_spillover_edges(anomaly_ref, epi, neighbors, window_mask)

        epi_edges = []
        for e in net["edges"]:
            nb = e["target"]
            # Neighbor's OWN light loss during the window (positive = it dimmed).
            nb_g = enriched[nb]
            nb_win = nb_g[(nb_g["date"] >= onset_date) & (nb_g["date"] <= pd.Timestamp(dates[window_end_idx]))]
            own_loss = float(-(nb_win["anomaly_smoothed"].min())) if len(nb_win) else 0.0
            own_loss = max(own_loss, 0.0) if np.isfinite(own_loss) else 0.0

            key = f"{min(epi, nb)}|{max(epi, nb)}"
            border = float(border_lengths.get(key, 0.0))
            dep = _trade_dependence(pair_value, total_trade, nb, epi, onset_date.year - 1)
            pval = _corr_pvalue(e["weight"], e["n_months"])
            significant = bool(e["n_months"] >= config.SPILLOVER_MIN_OVERLAP_MONTHS and pval < 0.10)

            row = {
                "source": epi, "source_name": name_by.get(epi, epi),
                "target": nb, "target_name": name_by.get(nb, nb),
                "weight": e["weight"], "lag_months": e["lag_months"], "n_months": e["n_months"],
                "p_value": pval, "significant": significant,
                "own_light_loss": own_loss, "border_length": border,
                "trade_dependence": dep, "onset_date": onset_date,
                "window_start": dates[onset_idx], "window_end": dates[window_end_idx],
            }
            epi_edges.append(row)

        # Per-epicenter spillover index: total neighbor light loss weighted by
        # exposure (trade dependence, falling back to a small floor).
        index = sum(r["own_light_loss"] * (0.1 + (r["trade_dependence"] or 0.0))
                    for r in epi_edges if r["significant"])
        for r in epi_edges:
            r["epicenter_spillover_index"] = round(float(index), 4)
        edge_rows.extend(epi_edges)

    if not edge_rows:
        return pd.DataFrame(columns=["source", "target", "weight"])
    return pd.DataFrame(edge_rows)


def _partner_concentration(bilateral: pd.DataFrame) -> pd.DataFrame:
    """Herfindahl index of trade-partner concentration per (country, year).

    Combines a country's trade with each partner in both directions, then sums
    the squared partner shares: ~0 = perfectly diversified, 1 = a single
    partner. A rise after conflict onset means the country was forced onto
    fewer partners (lost resilience), not just lost volume.
    """
    if bilateral is None or bilateral.empty:
        return pd.DataFrame(columns=["iso3", "year", "hhi"])
    out = bilateral.rename(columns={"exporter": "iso3", "importer": "partner"})[["iso3", "partner", "year", "value_kusd"]]
    inn = bilateral.rename(columns={"importer": "iso3", "exporter": "partner"})[["iso3", "partner", "year", "value_kusd"]]
    both = pd.concat([out, inn], ignore_index=True)
    per = both.groupby(["iso3", "year", "partner"], as_index=False)["value_kusd"].sum()
    tot = per.groupby(["iso3", "year"], as_index=False)["value_kusd"].sum().rename(columns={"value_kusd": "tot"})
    per = per.merge(tot, on=["iso3", "year"])
    per["sh2"] = (per["value_kusd"] / per["tot"].where(per["tot"] > 0)) ** 2
    return per.groupby(["iso3", "year"], as_index=False)["sh2"].sum().rename(columns={"sh2": "hhi"})


def _index_to_baseline(g: pd.DataFrame, cols: list[str], base_years: list[int]) -> pd.DataFrame:
    """Add ``<col>_index`` = 100 * value / mean(value over ``base_years``)."""
    base = g[g["year"].isin(base_years)] if base_years else g.iloc[0:0]
    for c in cols:
        b = base[c].mean() if len(base) else np.nan
        g[c + "_index"] = (100.0 * g[c] / b) if (np.isfinite(b) and b != 0) else np.nan
    return g


def _build_plot5(bilateral: pd.DataFrame, ntl_annual: pd.DataFrame,
                 worldbank: pd.DataFrame, countries: list[dict]) -> pd.DataFrame:
    """Plot 5 (trade), country-year for every country.

    Aggregates BACI bilateral trade to country-year exports/imports, then
    indexes each trajectory — plus night-lights, GDP/capita and the *world*
    trade benchmark — to the pre-conflict baseline (=100). Indexing against the
    world nets out global slumps (2009, 2020), so a conflict country's EXCESS
    trade collapse stands out. Also carries partner concentration (HHI), an
    excess-trade-loss score for the cross-country ranking, and commodity flags.
    """
    if bilateral is None or bilateral.empty:
        return pd.DataFrame(columns=["iso3", "year", "exports_kusd", "imports_kusd"])

    from pipeline.utils import country_name

    name_by = {c["iso3"]: c["name"] for c in countries}
    region_by = {c["iso3"]: c["region"] for c in countries}
    onset_by = {c["iso3"]: c.get("conflict_onset") for c in countries if c.get("has_conflict")}

    exp = (bilateral.groupby(["exporter", "year"], as_index=False)["value_kusd"].sum()
           .rename(columns={"exporter": "iso3", "value_kusd": "exports_kusd"}))
    imp = (bilateral.groupby(["importer", "year"], as_index=False)["value_kusd"].sum()
           .rename(columns={"importer": "iso3", "value_kusd": "imports_kusd"}))
    panel = exp.merge(imp, on=["iso3", "year"], how="outer")
    panel[["exports_kusd", "imports_kusd"]] = panel[["exports_kusd", "imports_kusd"]].fillna(0.0)
    panel["total_trade_kusd"] = panel["exports_kusd"] + panel["imports_kusd"]

    # World benchmark: total world trade per year (broadcast to every country).
    world = (panel.groupby("year", as_index=False)["exports_kusd"].sum()
             .rename(columns={"exports_kusd": "world_total_kusd"}))
    panel = panel.merge(world, on="year", how="left")

    # Night-lights (annual harmonized) + GDP/capita overlays for triangulation.
    if ntl_annual is not None and not ntl_annual.empty:
        panel = panel.merge(
            ntl_annual[["iso3", "year", "sum_of_lights"]].rename(columns={"sum_of_lights": "ntl_sum"}),
            on=["iso3", "year"], how="left")
    else:
        panel["ntl_sum"] = np.nan
    if worldbank is not None and "gdp_per_capita" in getattr(worldbank, "columns", []):
        panel = panel.merge(worldbank[["iso3", "year", "gdp_per_capita"]], on=["iso3", "year"], how="left")
    else:
        panel["gdp_per_capita"] = np.nan

    panel = panel.merge(_partner_concentration(bilateral), on=["iso3", "year"], how="left")
    # Trade spans more countries than the NTL-derived registry (e.g. Türkiye has
    # no monthly VIIRS row) — fall back to pycountry so every partner has a name.
    panel["country"] = panel["iso3"].map(lambda i: name_by.get(i) or country_name(i))
    panel["region"] = panel["iso3"].map(region_by).fillna("Other")
    panel["is_commodity_exporter"] = panel["iso3"].isin(config.COMMODITY_EXPORTERS)

    idx_cols = ["exports_kusd", "imports_kusd", "total_trade_kusd", "world_total_kusd", "ntl_sum", "gdp_per_capita"]
    parts = []
    for iso3, g in panel.groupby("iso3"):
        g = g.sort_values("year").reset_index(drop=True)
        # Drop implausible UPWARD spikes in the harmonized NTL overlay (flaring /
        # wildfire contamination in the 2023 file); war-driven drops are kept.
        if g["ntl_sum"].notna().sum() > 1:
            yoy = g["ntl_sum"] / g["ntl_sum"].shift(1)
            g.loc[yoy > config.NTL_ANNUAL_MAX_YOY, "ntl_sum"] = np.nan
        onset = onset_by.get(iso3)
        has_conflict = onset is not None
        if has_conflict:
            onset_year = int(str(onset)[:4])
            base_years = [y for y in range(onset_year - config.TRADE_BASELINE_YEARS, onset_year)
                          if y in set(g["year"])]
        else:
            onset_year = None
            base_years = []
        if not base_years and len(g):  # pre-onset years missing (or peaceful) -> anchor on first year
            base_years = [int(g["year"].min())]
        g = _index_to_baseline(g, idx_cols, base_years)
        g["conflict_onset"] = onset
        g["has_conflict"] = has_conflict
        g["baseline_year"] = onset_year if has_conflict else (base_years[0] if base_years else None)

        # Excess trade loss: how far total trade fell below its own baseline vs
        # how far *world* trade fell over the post-onset window (>0 = worse than
        # the world — the conflict signal net of the global cycle).
        excess = np.nan
        if has_conflict:
            post_years = set(range(onset_year, onset_year + config.TRADE_POST_WINDOW + 1))
            post = g[g["year"].isin(post_years)]
            if len(post):
                excess = float((post["world_total_kusd_index"] - post["total_trade_kusd_index"]).mean())
        g["excess_trade_loss"] = excess
        parts.append(g)

    result = pd.concat(parts, ignore_index=True)
    return result.rename(columns={
        "exports_kusd_index": "exports_index", "imports_kusd_index": "imports_index",
        "total_trade_kusd_index": "total_index", "world_total_kusd_index": "benchmark_index",
        "ntl_sum_index": "ntl_index", "gdp_per_capita_index": "gdp_pc_index",
    })


def assemble_plot_files(results: dict, acled_events: pd.DataFrame, countries: list[dict],
                        names_all: pd.DataFrame | None = None,
                        worldbank: pd.DataFrame | None = None,
                        adjacency: dict | None = None, border_lengths: dict | None = None,
                        bilateral_trade: pd.DataFrame | None = None,
                        ntl_annual: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    names = pd.DataFrame([{"iso3": c["iso3"], "country": c["name"], "region": c["region"]} for c in countries])
    region_by_iso3 = {c["iso3"]: c["region"] for c in countries}

    # --- plot1: GDP-NTL residual (country-year, ~185 countries) -----------
    plot1 = results["residual_panel"].copy()
    if names_all is not None and not names_all.empty:
        plot1 = plot1.merge(names_all, on="iso3", how="left")
    else:
        plot1 = plot1.merge(names[["iso3", "country"]], on="iso3", how="left")
    # Region is only defined for the tracked conflict cluster; others get "Other".
    plot1["region"] = plot1["iso3"].map(region_by_iso3).fillna("Other")

    # Within-country residual ANOMALY (a country fixed effect): the residual
    # minus the country's OWN median residual across years. Permanent structural
    # over/under-lighting (Australia's empty interior, China's efficient grid)
    # cancels out and reads neutral, so only economies whose GDP–light link
    # genuinely shifted from their norm stand out — the mapped quantity in View 1.
    counts = plot1.groupby("iso3")["residual"].transform("count")
    baseline = plot1.groupby("iso3")["residual"].transform("median")
    plot1["residual_baseline"] = baseline.where(counts >= config.RESIDUAL_BASELINE_MIN_YEARS)
    plot1["residual_anomaly"] = plot1["residual"] - plot1["residual_baseline"]

    # --- plot2: conflict & light — deseasonalized, cloud-masked, smoothed --
    plot2 = _build_plot2(results["master"], acled_events, countries)

    # --- plot3: recovery vs development profile, by conflict type ----------
    wb = worldbank if worldbank is not None else pd.DataFrame(columns=["iso3", "year"])
    plot3 = _build_plot3(results["master"], acled_events, wb, countries)

    # --- plot4: spillover — real borders, own light loss, exposure, index --
    plot4 = _build_plot4(
        results["master"], adjacency or {}, border_lengths or {},
        bilateral_trade if bilateral_trade is not None else pd.DataFrame(), countries,
    )

    # --- plot5: trade — indexed vs world benchmark, realignment, HHI, ranking
    plot5 = _build_plot5(
        bilateral_trade if bilateral_trade is not None else pd.DataFrame(),
        ntl_annual if ntl_annual is not None else pd.DataFrame(),
        wb, countries,
    )

    return {"plot1": plot1, "plot2": plot2, "plot3": plot3, "plot4": plot4, "plot5": plot5}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    return p.parse_args(argv)


def _build_residual_input(worldbank: pd.DataFrame) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Join the annual harmonized NTL to constant-GDP + population for Plot 1.

    Returns (residual_input, names_all). ``names_all`` carries the real country
    names (Plot 1 spans ~185 countries, far beyond the fixture registry).
    """
    try:
        ntl_annual = read_table(config.NTL_ANNUAL_PARQUET)
    except FileNotFoundError:
        return None, None

    # Drop implausible one-year UPWARD spikes in the harmonized NTL (the 2023
    # layer is flaring/wildfire-contaminated for some high-latitude economies,
    # e.g. Russia/Norway/Canada). Such a country-year then has no light value and
    # simply gets no residual that year, rather than a spurious bright anomaly.
    ntl_annual = ntl_annual.sort_values(["iso3", "year"]).copy()
    yoy = ntl_annual.groupby("iso3")["sum_of_lights"].transform(lambda s: s / s.shift(1))
    ntl_annual.loc[yoy > config.NTL_ANNUAL_MAX_YOY, "sum_of_lights"] = np.nan

    wb_cols = ["iso3", "year", "gdp_constant_usd"]
    if "population" in worldbank.columns:
        wb_cols.append("population")
    joined = ntl_annual.merge(worldbank[wb_cols], on=["iso3", "year"], how="inner")

    names_all = ntl_annual[["iso3"]].drop_duplicates().copy()
    if "country" in ntl_annual.columns:
        names_all = ntl_annual[["iso3", "country"]].dropna().drop_duplicates("iso3")
    return joined, names_all


def main(argv=None) -> int:
    parse_args(argv)

    master = read_table(config.MASTER_PANEL_PARQUET)
    countries = _load_countries()
    adjacency = _load_adjacency()
    try:
        acled_events = read_table(config.ACLED_EVENTS_PARQUET)
    except FileNotFoundError:
        acled_events = pd.DataFrame()
    try:
        worldbank = read_table(config.WORLDBANK_PANEL_PARQUET)
    except FileNotFoundError:
        worldbank = pd.DataFrame(columns=["iso3", "year"])
    border_lengths = json.loads(config.BORDER_LENGTHS_JSON.read_text()) if config.BORDER_LENGTHS_JSON.exists() else {}
    try:
        bilateral_trade = read_table(config.BILATERAL_TRADE_PARQUET)
    except FileNotFoundError:
        bilateral_trade = pd.DataFrame()
    try:
        ntl_annual = read_table(config.NTL_ANNUAL_PARQUET)
    except FileNotFoundError:
        ntl_annual = pd.DataFrame()

    residual_input, names_all = _build_residual_input(worldbank)

    results = compute(master, countries, adjacency, residual_input=residual_input)
    plots = assemble_plot_files(
        results, acled_events, countries, names_all=names_all, worldbank=worldbank,
        adjacency=adjacency, border_lengths=border_lengths, bilateral_trade=bilateral_trade,
        ntl_annual=ntl_annual,
    )

    write_table(plots["plot1"], config.PLOT1_RESIDUAL_PARQUET)
    write_table(plots["plot2"], config.PLOT2_CONFLICT_LIGHT_PARQUET)
    write_table(plots["plot3"], config.PLOT3_SHOCK_RECOVERY_PARQUET)
    write_table(plots["plot4"], config.PLOT4_SPILLOVER_PARQUET)
    write_table(plots["plot5"], config.PLOT5_TRADE_PARQUET)

    log.info("processed 5-plot files: plot1=%d rows, plot2=%d, plot3=%d, plot4=%d edges, plot5=%d rows",
              len(plots["plot1"]), len(plots["plot2"]), len(plots["plot3"]), len(plots["plot4"]), len(plots["plot5"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
