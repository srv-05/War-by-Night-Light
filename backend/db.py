"""
This module handles database interactions and caching for data loading.
It supports both an optional PostgreSQL backend for panel data and a default Parquet-based backend.
"""
from __future__ import annotations

import json
from functools import lru_cache

import pandas as pd
from sqlalchemy import Boolean, Column, Date, Float, Integer, String, create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

import config


# Set up the SQLAlchemy engine. Caches the engine instance to ensure we only create it once.
@lru_cache(maxsize=1)
def get_engine():
    return create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True)


# Create a session factory bound to our SQLAlchemy engine
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, future=True)


# Declarative base class for defining SQLAlchemy models
Base = declarative_base()


class MasterPanel(Base):
    """
    Represents the main panel data containing country-level observations over time,
    including metrics like night-time lights, fatalities, and GDP.
    """
    __tablename__ = "master_panel"

    id = Column(Integer, primary_key=True, autoincrement=True)
    iso3 = Column(String(3), index=True, nullable=False)
    year = Column(Integer, index=True)
    month = Column(Integer)
    date = Column(Date)
    sum_of_lights = Column(Float)
    mean_radiance = Column(Float)
    valid_obs_count = Column(Integer)
    area_km2 = Column(Float)
    event_count = Column(Integer)
    total_fatalities = Column(Float)
    gdp_current_usd = Column(Float)
    gdp_constant_usd = Column(Float)
    gdp_per_capita = Column(Float)
    military_exp_pct_gdp = Column(Float)
    gov_expenditure_pct_gdp = Column(Float)


class RecoveryEpisode(Base):
    """
    Represents conflict episodes and their associated recovery data.
    """
    __tablename__ = "recovery_episodes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    iso3 = Column(String(3), index=True)
    country = Column(String)
    region = Column(String)
    onset_date = Column(Date)
    severity_score = Column(Float)
    recovery_time_months = Column(Float)
    recovery_capacity_score = Column(Float)
    censored = Column(Boolean)


def get_session():
    """
    Provides a database session for use in requests. Ensures the session is closed after use.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def load_panel_to_postgres(master: pd.DataFrame, episodes: pd.DataFrame) -> None:
    """
    Utility function to push the Pandas DataFrames containing master and episode data
    directly into the PostgreSQL database.
    """
    engine = get_engine()
    Base.metadata.create_all(engine)
    master.to_sql("master_panel", engine, if_exists="replace", index=False)
    episodes.to_sql("recovery_episodes", engine, if_exists="replace", index=False)


# Cache function for reading parquet files from disk, to avoid repeated disk I/O.
@lru_cache(maxsize=None)
def _read_parquet(path_str: str) -> pd.DataFrame:
    return pd.read_parquet(path_str)


# The following functions load specific Parquet files used across different views.
def plot1_residual() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT1_RESIDUAL_PARQUET))


def plot2_conflict_light() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT2_CONFLICT_LIGHT_PARQUET))


def plot2_conflict_type() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT2_CONFLICT_TYPE_PARQUET))


def plot3_shock_recovery() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT3_SHOCK_RECOVERY_PARQUET))


def plot4_spillover() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT4_SPILLOVER_PARQUET))


def plot5_trade() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT5_TRADE_PARQUET))


def plot5_trade_sankey() -> pd.DataFrame:
    return _read_parquet(str(config.PLOT5_TRADE_SANKEY_PARQUET))


def bilateral_trade() -> pd.DataFrame:
    """
    Loads bilateral trade data on demand.
    """
    return _read_parquet(str(config.BILATERAL_TRADE_PARQUET))


# The following functions load specific JSON reference data files. They are cached in memory.
@lru_cache(maxsize=1)
def countries() -> list[dict]:
    return json.loads(config.COUNTRIES_JSON.read_text())


@lru_cache(maxsize=1)
def countries_by_iso3() -> dict:
    return {c["iso3"]: c for c in countries()}


@lru_cache(maxsize=1)
def adjacency() -> dict:
    return json.loads(config.ADJACENCY_JSON.read_text())


@lru_cache(maxsize=1)
def geojson() -> dict:
    return json.loads(config.GEOJSON_PATH.read_text())


def clear_cache() -> None:
    """
    Clears all cached data in memory, forcing a reload on the next call.
    """
    _read_parquet.cache_clear()
    countries.cache_clear()
    countries_by_iso3.cache_clear()
    adjacency.cache_clear()
    geojson.cache_clear()
