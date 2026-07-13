import numpy as np
import pandas as pd

from analysis.baseline import (
    compute_country_anomaly,
    deseasonalize,
    detect_conflict_episodes,
    ntl_anomaly,
    ntl_baseline,
    seasonal_factors,
)


def test_seasonal_factors_and_deseasonalize_remove_the_calendar():
    # 4 years of a pure seasonal pattern (summer bright, winter dim), no trend.
    rng = np.random.default_rng(0)
    months = list(range(1, 13)) * 4
    pattern = {m: 1.0 + 0.4 * np.sin(2 * np.pi * (m - 1) / 12) for m in range(1, 13)}
    lights = [1000 * pattern[m] for m in months]
    df = pd.DataFrame({"month": months, "sum_of_lights": lights})

    factors = seasonal_factors(df, value_col="sum_of_lights", month_col="month")
    assert np.isclose(factors.mean(), 1.0, atol=1e-6)          # normalized
    assert factors[6] > factors[12]                             # summer factor > winter

    des = deseasonalize(df, factors, value_col="sum_of_lights", month_col="month")
    # After removing the calendar, the series should be ~flat (constant).
    assert des.std() / des.mean() < 0.02


def test_seasonal_factors_thin_history_returns_ones():
    df = pd.DataFrame({"month": [1, 2, 3], "sum_of_lights": [10.0, 20.0, 30.0]})
    factors = seasonal_factors(df, value_col="sum_of_lights", month_col="month")
    assert (factors == 1.0).all()


def _country_df(sum_of_lights, fatalities=None, start="2020-01-01"):
    n = len(sum_of_lights)
    dates = pd.period_range(start, periods=n, freq="M").to_timestamp()
    return pd.DataFrame({
        "date": dates,
        "sum_of_lights": sum_of_lights,
        "total_fatalities": fatalities if fatalities is not None else [0.0] * n,
    })


def test_ntl_baseline_is_trailing_median():
    df = _country_df([10, 20, 30, 900])  # onset at idx 3; pre-onset = [10,20,30]
    assert ntl_baseline(df, onset_idx=3) == 20.0


def test_ntl_baseline_uses_only_available_pre_onset_window():
    df = _country_df([100, 5])  # onset at idx 1, only one pre-onset month, window=12
    assert ntl_baseline(df, onset_idx=1, window_months=12) == 100.0


def test_ntl_baseline_no_pre_onset_history_is_nan():
    df = _country_df([50, 60])
    assert np.isnan(ntl_baseline(df, onset_idx=0))


def test_ntl_anomaly_formula():
    values = pd.Series([80.0, 100.0, 120.0])
    anomaly = ntl_anomaly(values, baseline=100.0)
    np.testing.assert_allclose(anomaly.to_numpy(), [-0.2, 0.0, 0.2])


def test_ntl_anomaly_zero_baseline_is_all_nan():
    values = pd.Series([1.0, 2.0])
    anomaly = ntl_anomaly(values, baseline=0.0)
    assert anomaly.isna().all()


def test_compute_country_anomaly_attaches_columns():
    df = _country_df([10, 20, 30, 15])
    out = compute_country_anomaly(df, onset_idx=3, window_months=3)
    assert out["baseline"].iloc[0] == 20.0  # median(10,20,30)
    np.testing.assert_allclose(out["anomaly"].to_numpy(), (df["sum_of_lights"] - 20.0) / 20.0)


def test_detect_conflict_episodes_basic_span():
    fatalities = [0, 0, 40, 60, 0, 0]
    df = _country_df([100] * 6, fatalities=fatalities)
    episodes = detect_conflict_episodes(df, threshold=25)
    assert len(episodes) == 1
    ep = episodes[0]
    assert ep["onset_idx"] == 2
    assert ep["end_idx"] == 3
    assert ep["duration_months"] == 2
    assert ep["total_fatalities"] == 100
    assert ep["peak_fatalities"] == 60


def test_detect_conflict_episodes_bridges_short_gap():
    # A 2-month lull (below threshold) inside an otherwise continuous war
    # should NOT split the episode when max_gap=2.
    fatalities = [0, 40, 0, 0, 50, 0]
    df = _country_df([100] * 6, fatalities=fatalities)
    episodes = detect_conflict_episodes(df, threshold=25, max_gap=2)
    assert len(episodes) == 1
    assert episodes[0]["onset_idx"] == 1
    assert episodes[0]["end_idx"] == 4


def test_detect_conflict_episodes_splits_long_gap():
    fatalities = [40, 0, 0, 0, 0, 50]
    df = _country_df([100] * 6, fatalities=fatalities)
    episodes = detect_conflict_episodes(df, threshold=25, max_gap=1)
    assert len(episodes) == 2


def test_detect_conflict_episodes_none_when_peaceful():
    df = _country_df([100] * 6, fatalities=[0, 1, 2, 0, 3, 1])
    assert detect_conflict_episodes(df, threshold=25) == []
