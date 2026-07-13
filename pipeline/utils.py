"""Shared pipeline helpers: logging, parquet/CSV I/O, ISO3 tagging, and the
synthetic-fixture country registry.

None of the live data sources (Google Earth Engine, ACLED, live World Bank
network access, Natural Earth) are guaranteed to be reachable wherever this
runs, so every ``pipeline/ingest_*`` / ``gee_extract`` script falls back to a
deterministic synthetic fixture built from the registry below when the real
source is unavailable. The fixture is *internally consistent* on purpose
(GDP is generated as a function of the same synthetic lights, with conflict
shocks hitting lights harder/faster than GDP) so the derived analytics
(residuals, severity, recovery) tell a coherent story end-to-end rather than
juxtaposing real GDP with fake lights.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# I/O — write parquet by default, fall back to CSV when pyarrow is missing
# ---------------------------------------------------------------------------
def write_table(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
        log.info("wrote %s rows -> %s", f"{len(df):,}", path)
        return path
    except Exception as exc:  # pyarrow not installed
        csv_path = path.with_suffix(".csv")
        df.to_csv(csv_path, index=False)
        log.warning("parquet unavailable (%s); wrote CSV -> %s", exc, csv_path)
        return csv_path


def read_table(path: Path) -> pd.DataFrame:
    path = Path(path)
    if path.exists():
        return pd.read_parquet(path)
    csv_path = path.with_suffix(".csv")
    if csv_path.exists():
        return pd.read_csv(csv_path, parse_dates=[c for c in ("date",) if True], keep_default_na=True)
    raise FileNotFoundError(f"No parquet or CSV found at {path}")


# ---------------------------------------------------------------------------
# ISO3 country-code tagging (for real ACLED/World Bank/Natural Earth pulls)
# ---------------------------------------------------------------------------
_NAME_OVERRIDES = {
    "russia": "RUS", "russian federation": "RUS",
    "syria": "SYR", "syrian arab republic": "SYR",
    "iran": "IRN", "iran, islamic rep.": "IRN",
    "egypt": "EGY", "egypt, arab rep.": "EGY",
    "yemen": "YEM", "yemen, rep.": "YEM",
    "turkey": "TUR", "turkiye": "TUR",
    "myanmar": "MMR", "burma": "MMR",
    "ethiopia": "ETH", "kenya": "KEN", "sudan": "SDN",
    "djibouti": "DJI", "eritrea": "ERI",
    "ukraine": "UKR", "poland": "POL", "romania": "ROU",
    "moldova": "MDA", "moldova, rep.": "MDA", "belarus": "BLR",
    "jordan": "JOR", "lebanon": "LBN", "iraq": "IRQ",
    "morocco": "MAR", "vietnam": "VNM", "viet nam": "VNM", "peru": "PER",
    # Names ACLED/World Bank spell differently from pycountry's canonical form.
    "democratic republic of congo": "COD", "dr congo": "COD", "congo, dem. rep.": "COD",
    "republic of congo": "COG", "congo": "COG", "congo, rep.": "COG",
    "ivory coast": "CIV", "cote d'ivoire": "CIV",
    "east timor": "TLS", "timor-leste": "TLS",
    "cape verde": "CPV", "cabo verde": "CPV",
    "south korea": "KOR", "korea, rep.": "KOR",
    "north korea": "PRK", "korea, dem. people's rep.": "PRK",
    "laos": "LAO", "lao pdr": "LAO",
    "slovakia": "SVK", "slovak republic": "SVK",
    "venezuela": "VEN", "venezuela, rb": "VEN",
    "tanzania": "TZA", "gambia": "GMB", "the gambia": "GMB",
    "bolivia": "BOL", "brunei": "BRN",
    "kyrgyzstan": "KGZ", "kyrgyz republic": "KGZ",
    "eswatini": "SWZ", "swaziland": "SWZ",
    "united states": "USA", "united states of america": "USA",
    "palestine": "PSE", "west bank and gaza": "PSE",
}


def to_iso3(name: str) -> str | None:
    """Best-effort country name -> ISO3. Manual overrides first, then pycountry."""
    if not isinstance(name, str) or not name.strip():
        return None
    key = name.strip().lower()
    if key in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[key]
    try:
        import pycountry

        match = pycountry.countries.get(name=name.strip())
        if match is None:
            results = pycountry.countries.search_fuzzy(name.strip())
            match = results[0] if results else None
        return match.alpha_3 if match else None
    except Exception:
        return None


def country_name(iso3: str) -> str:
    """ISO3 -> human country name (pycountry), falling back to the code itself."""
    try:
        import pycountry

        c = pycountry.countries.get(alpha_3=iso3)
        return c.name if c else iso3
    except Exception:
        return iso3


def region_for_iso3(iso3: str) -> str:
    """ISO3 -> continent/region name for coloring & grouping. 'Other' if unknown."""
    try:
        import pycountry_convert as pc

        a2 = pc.country_alpha3_to_country_alpha2(iso3)
        return pc.convert_continent_code_to_continent_name(pc.country_alpha2_to_continent_code(a2))
    except Exception:
        return "Other"


def add_iso3(df: pd.DataFrame, name_col: str, out_col: str = "iso3") -> pd.DataFrame:
    # Resolve each *unique* country name once, then map — the pycountry fuzzy
    # lookup is expensive and event tables can have millions of rows.
    df = df.copy()
    lookup = {name: to_iso3(name) for name in df[name_col].dropna().unique()}
    df[out_col] = df[name_col].map(lookup)
    missing = [name for name, iso in lookup.items() if iso is None]
    if missing:
        log.warning("Unmatched country names (no ISO3): %s", sorted(map(str, missing)))
    return df


# ---------------------------------------------------------------------------
# Synthetic-fixture registry
# ---------------------------------------------------------------------------
# Three geographic clusters (Levant, Eastern Europe, Horn of Africa), each with
# a primary conflict epicenter and land-border neighbors; two of those neighbors
# (Iraq, Sudan) are *also* independent conflict epicenters on their own timeline,
# so the spillover model has to disentangle two overlapping shocks rather than
# one. Three unrelated donor-only countries widen the synthetic-control pool.
#
#   iso3, name, region, is_conflict, onset (YYYY-MM-01), lon, lat (fixture
#   polygon centroid), area_km2, base_ntl (arbitrary sum-of-lights units at
#   window start), base_gdp_usd, base_gdp_per_capita, base_military_pct_gdp,
#   base_gov_expenditure_pct_gdp
FIXTURE_COUNTRIES = [
    dict(iso3="SYR", name="Syria", region="Middle East", conflict=True, onset="2011-03-01",
         lon=38.0, lat=35.0, area_km2=185180, base_ntl=900.0, base_gdp_usd=60e9,
         base_gdp_pc=3200, base_mil_pct=4.5, base_gov_pct=13.0),
    dict(iso3="JOR", name="Jordan", region="Middle East", conflict=False, onset=None,
         lon=36.5, lat=31.5, area_km2=89342, base_ntl=320.0, base_gdp_usd=35e9,
         base_gdp_pc=4200, base_mil_pct=4.8, base_gov_pct=18.0),
    dict(iso3="LBN", name="Lebanon", region="Middle East", conflict=False, onset=None,
         lon=35.8, lat=33.8, area_km2=10452, base_ntl=260.0, base_gdp_usd=50e9,
         base_gdp_pc=7500, base_mil_pct=3.8, base_gov_pct=14.0),
    dict(iso3="TUR", name="Turkiye", region="Middle East", conflict=False, onset=None,
         lon=35.0, lat=39.0, area_km2=783562, base_ntl=4200.0, base_gdp_usd=800e9,
         base_gdp_pc=9500, base_mil_pct=1.9, base_gov_pct=15.0),
    dict(iso3="IRQ", name="Iraq", region="Middle East", conflict=True, onset="2014-06-01",
         lon=43.5, lat=33.0, area_km2=438317, base_ntl=1100.0, base_gdp_usd=180e9,
         base_gdp_pc=4600, base_mil_pct=3.4, base_gov_pct=20.0),

    dict(iso3="UKR", name="Ukraine", region="Europe & Eurasia", conflict=True, onset="2022-02-01",
         lon=31.0, lat=49.0, area_km2=603550, base_ntl=2600.0, base_gdp_usd=155e9,
         base_gdp_pc=3700, base_mil_pct=3.2, base_gov_pct=19.0),
    dict(iso3="POL", name="Poland", region="Europe & Eurasia", conflict=False, onset=None,
         lon=19.0, lat=52.0, area_km2=312679, base_ntl=3400.0, base_gdp_usd=590e9,
         base_gdp_pc=15600, base_mil_pct=2.1, base_gov_pct=17.5),
    dict(iso3="ROU", name="Romania", region="Europe & Eurasia", conflict=False, onset=None,
         lon=25.0, lat=46.0, area_km2=238397, base_ntl=1500.0, base_gdp_usd=250e9,
         base_gdp_pc=13000, base_mil_pct=2.0, base_gov_pct=16.0),
    dict(iso3="MDA", name="Moldova", region="Europe & Eurasia", conflict=False, onset=None,
         lon=28.5, lat=47.0, area_km2=33846, base_ntl=180.0, base_gdp_usd=12e9,
         base_gdp_pc=4600, base_mil_pct=0.4, base_gov_pct=20.0),
    dict(iso3="BLR", name="Belarus", region="Europe & Eurasia", conflict=False, onset=None,
         lon=28.0, lat=53.0, area_km2=207600, base_ntl=900.0, base_gdp_usd=68e9,
         base_gdp_pc=7200, base_mil_pct=1.3, base_gov_pct=16.5),

    dict(iso3="ETH", name="Ethiopia", region="Africa", conflict=True, onset="2020-11-01",
         lon=39.0, lat=9.0, area_km2=1104300, base_ntl=650.0, base_gdp_usd=100e9,
         base_gdp_pc=950, base_mil_pct=0.6, base_gov_pct=11.0),
    dict(iso3="KEN", name="Kenya", region="Africa", conflict=False, onset=None,
         lon=37.9, lat=0.0, area_km2=580367, base_ntl=520.0, base_gdp_usd=110e9,
         base_gdp_pc=2000, base_mil_pct=1.3, base_gov_pct=14.5),
    dict(iso3="SDN", name="Sudan", region="Africa", conflict=True, onset="2023-04-01",
         lon=30.0, lat=15.0, area_km2=1886068, base_ntl=430.0, base_gdp_usd=35e9,
         base_gdp_pc=750, base_mil_pct=2.9, base_gov_pct=8.0),
    dict(iso3="DJI", name="Djibouti", region="Africa", conflict=False, onset=None,
         lon=42.5, lat=11.5, area_km2=23200, base_ntl=90.0, base_gdp_usd=3.4e9,
         base_gdp_pc=3400, base_mil_pct=3.3, base_gov_pct=20.0),
    dict(iso3="ERI", name="Eritrea", region="Africa", conflict=False, onset=None,
         lon=39.0, lat=15.0, area_km2=117600, base_ntl=60.0, base_gdp_usd=2.6e9,
         base_gdp_pc=650, base_mil_pct=10.0, base_gov_pct=9.0),

    # Donor-pool-only countries (unrelated regions, no adjacency), widening the
    # synthetic-control donor pool with peacetime trajectories of varied shape.
    dict(iso3="MAR", name="Morocco", region="Africa", conflict=False, onset=None,
         lon=-7.0, lat=32.0, area_km2=446550, base_ntl=980.0, base_gdp_usd=130e9,
         base_gdp_pc=3600, base_mil_pct=4.1, base_gov_pct=17.0),
    dict(iso3="VNM", name="Vietnam", region="Asia-Pacific", conflict=False, onset=None,
         lon=108.0, lat=14.0, area_km2=331212, base_ntl=1800.0, base_gdp_usd=360e9,
         base_gdp_pc=3700, base_mil_pct=2.3, base_gov_pct=15.0),
    dict(iso3="PER", name="Peru", region="Americas", conflict=False, onset=None,
         lon=-76.0, lat=-10.0, area_km2=1285216, base_ntl=760.0, base_gdp_usd=220e9,
         base_gdp_pc=6600, base_mil_pct=1.2, base_gov_pct=13.0),
]

FIXTURE_ADJACENCY = {
    "SYR": ["JOR", "LBN", "TUR", "IRQ"], "JOR": ["SYR", "IRQ"], "LBN": ["SYR"],
    "TUR": ["SYR", "IRQ"], "IRQ": ["SYR", "TUR", "JOR"],
    "UKR": ["POL", "ROU", "MDA", "BLR"], "POL": ["UKR", "BLR"], "ROU": ["UKR", "MDA"],
    "MDA": ["UKR", "ROU"], "BLR": ["UKR", "POL"],
    "ETH": ["KEN", "SDN", "DJI", "ERI"], "KEN": ["ETH"], "SDN": ["ETH"],
    "DJI": ["ETH", "ERI"], "ERI": ["ETH", "DJI"],
    "MAR": [], "VNM": [], "PER": [],
}
