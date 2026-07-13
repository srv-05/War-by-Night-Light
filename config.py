"""
Configuration module containing paths, constants, variables, and schema definitions.
Loads environment variables and sets up global paths for data directories.
"""
from __future__ import annotations

import os
from pathlib import Path

# Attempt to load environment variables from a .env file, if present
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


# Base directory for the project
BASE_DIR = Path(__file__).resolve().parent

# Define the directories for storing raw, interim, processed, and geographical data
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR / "data"))
DATA_RAW_DIR = DATA_DIR / "raw"
DATA_INTERIM_DIR = DATA_DIR / "interim"
DATA_PROCESSED_DIR = DATA_DIR / "processed"
DATA_GEO_DIR = DATA_DIR / "geo"


def ensure_dirs() -> None:
    """
    Creates necessary directories if they do not already exist.
    """
    for d in (DATA_RAW_DIR, DATA_INTERIM_DIR, DATA_PROCESSED_DIR, DATA_GEO_DIR):
        d.mkdir(parents=True, exist_ok=True)


ensure_dirs()

# Paths to raw data CSV and geojson files
RAW_VIIRS_CSV = DATA_RAW_DIR / "ntl_country_month_raw.csv"
RAW_ACLED_EVENTS_CSV = DATA_RAW_DIR / "acled_events_raw.csv"
RAW_WORLDBANK_CSV = DATA_RAW_DIR / "worldbank_raw.csv"
RAW_GEOJSON = DATA_RAW_DIR / "countries_raw.geojson"

# Path to local dataset downloads, configurable via environment variable
DATASET_DIR = Path(os.getenv("DATASET_DIR", DATA_RAW_DIR / "DataSet"))

# Paths for ACLED and World Bank subdirectories
DATASET_ACLED_DIR = DATASET_DIR / "ACLED Dataset_2024"

_WB = DATASET_DIR / "World Bank_2024"
DATASET_WB_GDP_CSV = _WB / "GDP_2024.csv"
DATASET_WB_GDP_PER_CAPITA_CSV = _WB / "GDP_PER_CAPITA_2024.csv"
DATASET_WB_POPULATION_CSV = _WB / "POPULATION_2024.csv"
DATASET_WB_WDI_CSV = _WB / "WORLD_DEVELPMENT_INDEX_2024.csv"

# Paths to the night-time lights datasets
DATASET_HARMONIZED_NTL_DIR = DATASET_DIR / "Harmonized_NTL_2023"
DATASET_VIIRS_NTL_DIR = DATASET_DIR / "VIIRS_NTL_2024"

# Path to the trade dataset
DATASET_TRADE_DIR = DATASET_DIR / "Trade_2024"

# Mapping from World Bank indicator codes to standard column names used in the pipeline
WDI_INDICATORS = {
    "WB_WDI_NY_GDP_MKTP_KD": "gdp_constant_usd",
    "WB_WDI_MS_MIL_XPND_GD_ZS": "military_exp_pct_gdp",
    "WB_WDI_NE_CON_GOVT_ZS": "gov_expenditure_pct_gdp",
    "WB_WDI_SH_XPD_CHEX_GD_ZS": "health_exp_pct_gdp",
    "WB_WDI_SE_XPD_TOTL_GD_ZS": "education_exp_pct_gdp",
    "WB_WDI_GOV_WGI_GE_EST": "govt_effectiveness",
    "WB_WDI_EG_ELC_ACCS_ZS": "electricity_access_pct",
    "WB_WDI_NE_GDI_FTOT_ZS": "capital_formation_pct_gdp",
}

# Labels for various development factors to display on the frontend
DEVELOPMENT_FACTORS = {
    "gdp_per_capita": "GDP per capita (US$)",
    "health_exp_pct_gdp": "Health investment (% GDP)",
    "education_exp_pct_gdp": "Education investment (% GDP)",
    "govt_effectiveness": "Government effectiveness (WGI)",
    "electricity_access_pct": "Electricity access (% pop)",
    "capital_formation_pct_gdp": "Capital formation (% GDP)",
    "military_exp_pct_gdp": "Military spending (% GDP)",
}

# Intermediate Parquet files generated during the pipeline run
NTL_PANEL_PARQUET = DATA_INTERIM_DIR / "ntl_panel.parquet"
NTL_ANNUAL_PARQUET = DATA_INTERIM_DIR / "ntl_annual.parquet"
BILATERAL_TRADE_PARQUET = DATA_INTERIM_DIR / "bilateral_trade.parquet"
ACLED_EVENTS_PARQUET = DATA_INTERIM_DIR / "acled_events.parquet"
ACLED_PANEL_PARQUET = DATA_INTERIM_DIR / "acled_panel.parquet"
WORLDBANK_PANEL_PARQUET = DATA_INTERIM_DIR / "worldbank_panel.parquet"
MASTER_PANEL_PARQUET = DATA_INTERIM_DIR / "master_panel.parquet"

# Final processed Parquet files that are directly served by the backend API
PLOT1_RESIDUAL_PARQUET = DATA_PROCESSED_DIR / "plot1_gdp_ntl_residual.parquet"
PLOT2_CONFLICT_LIGHT_PARQUET = DATA_PROCESSED_DIR / "plot2_conflict_light.parquet"
PLOT2_CONFLICT_TYPE_PARQUET = DATA_PROCESSED_DIR / "plot2_conflict_type.parquet"
PLOT3_SHOCK_RECOVERY_PARQUET = DATA_PROCESSED_DIR / "plot3_shock_recovery.parquet"
PLOT4_SPILLOVER_PARQUET = DATA_PROCESSED_DIR / "plot4_spillover.parquet"
PLOT5_TRADE_PARQUET = DATA_PROCESSED_DIR / "plot5_trade.parquet"
PLOT5_TRADE_SANKEY_PARQUET = DATA_PROCESSED_DIR / "plot5_trade_sankey.parquet"

# Geographical data file paths, including geometries and adjacency information
COUNTRIES_JSON = DATA_GEO_DIR / "countries.json"
ADJACENCY_JSON = DATA_GEO_DIR / "adjacency.json"
BORDER_LENGTHS_JSON = DATA_GEO_DIR / "border_lengths.json"
GEOJSON_PATH = DATA_GEO_DIR / "countries.geojson"


# Analysis timeline range defining the start and end years
START_YEAR = int(os.getenv("START_YEAR", "2000"))
END_YEAR = int(os.getenv("END_YEAR", "2024"))

# Settings used to analyze trade shocks against baseline values
TRADE_BASELINE_YEARS = 3
TRADE_POST_WINDOW = 3
TRADE_TOP_PARTNERS = 8

# A cutoff multiplier to identify anomalies in annual night-time light variations
NTL_ANNUAL_MAX_YOY = 3.0

# Minimum number of data years required for a country's residual baseline analysis
RESIDUAL_BASELINE_MIN_YEARS = 5

# Cutoff value for identifying physically implausible monthly spikes in light
ANOMALY_MAX_GAIN = 1.0

# Countries considered to be heavily dependent on commodities for trade
COMMODITY_EXPORTERS = {
    "DZA", "AGO", "AZE", "BHR", "BRN", "TCD", "COG", "COD", "ECU", "GNQ", "GAB",
    "GIN", "IRN", "IRQ", "KAZ", "KWT", "LBY", "MNG", "MRT", "NER", "NGA", "OMN",
    "PNG", "QAT", "RUS", "SAU", "SSD", "TTO", "TKM", "ARE", "VEN", "YEM", "ZMB",
}

# Major international sanctions events affecting countries in the analysis
SANCTION_EVENTS = {
    "RUS": [{"year": 2014, "label": "Crimea sanctions"}, {"year": 2022, "label": "Full-invasion sanctions"}],
    "IRN": [{"year": 2012, "label": "Oil embargo"}, {"year": 2018, "label": "Sanctions re-imposed"}],
    "VEN": [{"year": 2019, "label": "PDVSA / oil sanctions"}],
    "SYR": [{"year": 2011, "label": "EU/US sanctions"}],
    "PRK": [{"year": 2017, "label": "UNSC sanctions tightened"}],
    "LBY": [{"year": 2011, "label": "Civil-war sanctions"}],
}

# Google Earth Engine configuration for VIIRS
VIIRS_COLLECTION = "NOAA/VIIRS/DNB/MONTHLY_V1/VCMSLCFG"
VIIRS_RAD_BAND = "avg_rad"
VIIRS_CVG_BAND = "cf_cvg"
VIIRS_SCALE_M = 500

# Mapping of alternative World Bank indicators for broader data points
WORLDBANK_INDICATORS = {
    "NY.GDP.MKTP.CD": "gdp_current_usd",
    "NY.GDP.MKTP.KD": "gdp_constant_usd",
    "NY.GDP.PCAP.CD": "gdp_per_capita",
    "MS.MIL.XPND.GD.ZS": "military_exp_pct_gdp",
    "NE.CON.GOVT.ZS": "gov_expenditure_pct_gdp",
}

# Threshold values to define conflicts based on fatalities
CONFLICT_FATALITY_THRESHOLD = int(os.getenv("CONFLICT_FATALITY_THRESHOLD", "25"))
MAJOR_CONFLICT_FATALITIES = int(os.getenv("MAJOR_CONFLICT_FATALITIES", "1000"))

# Timeframe for establishing a baseline for light measurements prior to conflict
BASELINE_WINDOW_MONTHS = int(os.getenv("BASELINE_WINDOW_MONTHS", "12"))

# Minimum limit for valid VIIRS observations to exclude noise before log transformations
NTL_LOG_FLOOR = 1e-3

# The proportion of the baseline that a country must reach to be considered recovered
RECOVERY_THRESHOLD_PCT = float(os.getenv("RECOVERY_THRESHOLD_PCT", "0.05"))

# The maximum timeframe in months allowed for recovery analysis
MAX_RECOVERY_HORIZON_MONTHS = int(os.getenv("MAX_RECOVERY_HORIZON_MONTHS", "60"))

# The minimum count of valid observations in a month to accept the data point
VALID_OBS_MIN_COUNT = int(os.getenv("VALID_OBS_MIN_COUNT", "3"))

# Proportional weights used to calculate the conflict severity score
SEVERITY_WEIGHTS = {
    "ntl_pct_drop": 1 / 3,
    "total_fatalities": 1 / 3,
    "blackout_duration_months": 1 / 3,
}

# Timeframes configured to identify correlations and spillover effects
SPILLOVER_MIN_OVERLAP_MONTHS = 6
SPILLOVER_POST_BUFFER_MONTHS = 6
SPILLOVER_MAX_LAG_MONTHS = 3

# A country is treated as "in conflict" in a calendar year when its ACLED
# fatalities that year reach this threshold. Used to compute the per-year
# spillover network (Plot 4) and per-year commodity trade sankey (Plot 5) for
# every conflict country, every year it is active.
ANNUAL_CONFLICT_FATALITIES = 100

# PostgreSQL connection string
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://lights_out:lights_out@localhost:5432/lights_out"
)

# Backend switch determining the primary data source, can be 'parquet' or 'db'
DATA_BACKEND = os.getenv("DATA_BACKEND", "parquet")

# Server binding variables configured through environment variables
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# List of valid origins capable of hitting the API
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
