import numpy as np
import pandas as pd

from analysis.severity import severity_index


def test_components_are_scaled_zero_to_one():
    episodes = pd.DataFrame({
        "ntl_pct_drop": [0.1, 0.5, 0.9],
        "total_fatalities": [10, 500, 50000],
        "blackout_duration_months": [2, 12, 36],
    })
    scored = severity_index(episodes)
    for col in ("component_ntl_pct_drop", "component_total_fatalities", "component_blackout_duration_months"):
        assert scored[col].between(0, 1).all()
        assert np.isclose(scored[col].min(), 0.0)
        assert np.isclose(scored[col].max(), 1.0)
    assert scored["severity_score"].between(0, 1).all()


def test_worst_episode_on_every_signal_ranks_first():
    episodes = pd.DataFrame({
        "ntl_pct_drop": [0.1, 0.9],
        "total_fatalities": [10, 50000],
        "blackout_duration_months": [2, 36],
    })
    scored = severity_index(episodes)
    assert scored.iloc[0]["ntl_pct_drop"] == 0.9  # sorted descending by severity_score


def test_censored_episode_gets_worst_observed_duration():
    episodes = pd.DataFrame({
        "ntl_pct_drop": [0.5, 0.5, 0.5],
        "total_fatalities": [100, 100, 100],
        "blackout_duration_months": [6.0, 12.0, np.nan],  # third episode never recovered
    })
    scored = severity_index(episodes)
    # The censored row is filled with the worst *observed* duration (12),
    # tying it with the second episode for the top duration component.
    censored_row = scored[scored["blackout_duration_months"].isna()]
    longest_observed_row = scored[scored["blackout_duration_months"] == 12.0]
    assert np.isclose(censored_row["component_blackout_duration_months"].iloc[0], 1.0)
    assert np.isclose(
        censored_row["component_blackout_duration_months"].iloc[0],
        longest_observed_row["component_blackout_duration_months"].iloc[0],
    )


def test_fatalities_are_log_scaled_not_linear():
    # Without log-scaling, 100x more fatalities would completely dominate the
    # min-max scale, squashing the middle episode's score near 0.
    episodes = pd.DataFrame({
        "ntl_pct_drop": [0.5, 0.5, 0.5],
        "total_fatalities": [10, 1000, 100000],
        "blackout_duration_months": [10, 10, 10],
    })
    scored = severity_index(episodes)
    middle = scored[scored["total_fatalities"] == 1000]["component_total_fatalities"].iloc[0]
    # log1p-scaled midpoint should sit well above what a raw-linear scale
    # would give it (linear would put it at ~0.0099, near the floor).
    assert middle > 0.3


def test_constant_signal_scores_zero_not_error():
    episodes = pd.DataFrame({
        "ntl_pct_drop": [0.4, 0.4],
        "total_fatalities": [100, 100],
        "blackout_duration_months": [6, 6],
    })
    scored = severity_index(episodes)
    assert (scored["component_ntl_pct_drop"] == 0.0).all()
