import numpy as np
import pandas as pd

from analysis.spillover import (
    anomaly_reference_matrix,
    compute_spillover_edges,
    lagged_correlation,
    partial_out_regional_factor,
)


def test_partial_out_regional_factor_removes_shared_trend():
    rng = np.random.default_rng(7)
    n = 100
    regional_factor = np.cumsum(rng.normal(0, 1, n))  # e.g. a shared regional recession
    a = regional_factor + rng.normal(0, 0.1, n)
    b = 0.5 * regional_factor + rng.normal(0, 0.1, n)  # weaker exposure to the same factor
    wide = pd.DataFrame({"A": a, "B": b})

    residuals = partial_out_regional_factor(wide, ["A", "B"])

    # Before partialling out, A and B are almost perfectly correlated (both
    # driven by the same regional factor). After, most of that shared
    # movement should be gone.
    raw_corr = wide["A"].corr(wide["B"])
    residual_corr = residuals["A"].corr(residuals["B"])
    assert raw_corr > 0.9
    assert abs(residual_corr) < abs(raw_corr)


def test_partial_out_regional_factor_falls_back_on_low_variance_region():
    wide = pd.DataFrame({"A": [1.0, 1.0, 1.0], "B": [2.0, 2.0, 2.0]})
    residuals = partial_out_regional_factor(wide, ["A", "B"])
    # No regional variation to fit a slope against -> de-meaned only.
    assert np.allclose(residuals["A"], 0.0)
    assert np.allclose(residuals["B"], 0.0)


def test_lagged_correlation_detects_positive_lead():
    rng = np.random.default_rng(3)
    n = 60
    a = pd.Series(np.cumsum(rng.normal(0, 1, n)))
    lag = 3
    b = a.shift(lag).bfill()  # b lags behind a by 3 steps -> a leads b

    corr, found_lag = lagged_correlation(a, b, max_lag=5)
    assert found_lag == lag
    assert corr > 0.9


def test_lagged_correlation_returns_nan_when_no_overlap():
    a = pd.Series([1.0, 2.0])
    b = pd.Series([np.nan, np.nan])
    corr, lag = lagged_correlation(a, b, max_lag=1)
    assert np.isnan(corr)


def test_anomaly_reference_matrix_shared_onset():
    wide_lights = pd.DataFrame({
        "EPI": [100.0, 100.0, 100.0, 20.0],
        "NBR": [50.0, 50.0, 50.0, 10.0],
    })
    result = anomaly_reference_matrix(wide_lights, ["EPI", "NBR"], onset_idx=3)
    # Both baselines are their own trailing medians (100 and 50); anomaly at
    # onset should be the same relative drop for both (-0.8).
    np.testing.assert_allclose(result["EPI"].iloc[3], -0.8)
    np.testing.assert_allclose(result["NBR"].iloc[3], -0.8)


def test_compute_spillover_edges_finds_correlated_neighbor():
    rng = np.random.default_rng(11)
    n = 40
    shock = np.concatenate([np.zeros(20), -np.cumsum(rng.uniform(0, 0.05, 20))])
    anomaly_wide = pd.DataFrame({
        "EPI": shock + rng.normal(0, 0.01, n),
        "NBR_CORRELATED": shock + rng.normal(0, 0.01, n),
        "NBR_UNRELATED": rng.normal(0, 1, n),
    })
    window_mask = pd.Series([True] * n)

    result = compute_spillover_edges(anomaly_wide, "EPI", ["NBR_CORRELATED", "NBR_UNRELATED"], window_mask, min_overlap=5)

    edge_by_target = {e["target"]: e for e in result["edges"]}
    assert edge_by_target["NBR_CORRELATED"]["weight"] > 0.7
    assert abs(edge_by_target["NBR_UNRELATED"]["weight"]) < abs(edge_by_target["NBR_CORRELATED"]["weight"])


def test_compute_spillover_edges_skips_missing_neighbors():
    anomaly_wide = pd.DataFrame({"EPI": [0.1, 0.2, 0.3, 0.4, 0.5]})
    window_mask = pd.Series([True] * 5)
    result = compute_spillover_edges(anomaly_wide, "EPI", ["GHOST"], window_mask)
    assert result["edges"] == []
    assert result["nodes"] == [{"iso3": "EPI", "role": "epicenter"}]
