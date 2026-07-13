import numpy as np
import pandas as pd

from analysis.synthetic_control import (
    fit_synthetic_control,
    gap_closed_recovery,
    recovery_capacity_score,
    recovery_time,
)


def test_fit_synthetic_control_matches_a_bigger_country_via_indexing():
    # The fit is on INDEXED series, so a donor emitting far more light than the
    # target can still be a good match on trajectory shape — the whole point of
    # indexing. Here the target follows donor A's *shape* but at 1/10th A's
    # level; the counterfactual should reconstruct the target's level and hold
    # A's undamaged trend through the post-onset shock.
    rng = np.random.default_rng(42)
    n = 60
    shape = 1000 + np.cumsum(rng.normal(0, 5, n))
    donor_a = 10 * shape                      # 10x the target's level, same shape
    donor_c = 50 + np.cumsum(rng.normal(0, 1, n))  # unrelated
    donors = pd.DataFrame({"A": donor_a, "C": donor_c})

    onset_idx = 40
    target = shape.copy().astype(float)
    target[onset_idx:] *= 0.3                  # conflict shock

    pre_mask = pd.Series([i < onset_idx for i in range(n)])
    result = fit_synthetic_control(pd.Series(target), donors, pre_mask)

    assert result["weights"].get("A", 0) > 0.9       # A carries the fit
    assert np.isclose(result["weights"].sum(), 1.0)
    # Counterfactual is on the TARGET's scale (indexing rescales), tracking the
    # undamaged shape through the shock.
    cf_post = result["counterfactual"].iloc[onset_idx:].to_numpy()
    np.testing.assert_allclose(cf_post, shape[onset_idx:], rtol=0.05)


def test_gap_closed_recovery_grades_recovery():
    # Observed drops to a trough then climbs partway back toward a flat cf=100.
    cf = pd.Series([100.0] * 40)
    obs = pd.Series([100] * 10 + [40] + [50, 60, 70, 80] + [70] * 24)  # trough at idx 10 (gap 60)
    r = gap_closed_recovery(obs, cf, onset_idx=8, horizon_months=24)
    assert r["trough_idx"] == 10
    # By the horizon it's climbed to ~70 -> gap 30 of original 60 closed = 0.5.
    assert 0.4 < r["gap_closed_pct"] < 0.6
    assert r["censored"] is False


def test_gap_closed_recovery_censored_when_short():
    cf = pd.Series([100.0] * 15)
    obs = pd.Series([100] * 10 + [40, 45, 50, 55, 60])  # only 4 months past trough
    r = gap_closed_recovery(obs, cf, onset_idx=8, horizon_months=24)
    assert r["censored"] is True


def test_fit_synthetic_control_drops_donor_columns_with_missing_pre_data():
    n = 20
    donors = pd.DataFrame({
        "A": np.arange(n, dtype=float),
        "B": [np.nan] * 5 + list(np.arange(5, n, dtype=float)),
    })
    target = pd.Series(np.arange(n, dtype=float))
    pre_mask = pd.Series([i < 10 for i in range(n)])

    result = fit_synthetic_control(target, donors, pre_mask)
    assert "B" not in result["weights"].index
    assert "A" in result["weights"].index


def test_recovery_time_finds_trough_and_recrossing():
    # observed: flat at 100, drops to 20 at onset, climbs back linearly.
    observed = pd.Series([100, 100, 100, 20, 40, 60, 80, 98, 100, 100])
    counterfactual = pd.Series([100] * 10)
    result = recovery_time(observed, counterfactual, onset_idx=3, threshold_pct=0.05)

    assert result["trough_idx"] == 3
    assert result["trough_value"] == 20
    assert result["censored"] is False
    assert result["recovery_idx"] == 7  # first index within 5% of counterfactual
    assert result["recovery_time_months"] == 4  # 7 - 3


def test_recovery_time_right_censored_when_never_recovers():
    observed = pd.Series([100, 100, 30, 32, 33, 34])
    counterfactual = pd.Series([100] * 6)
    result = recovery_time(observed, counterfactual, onset_idx=2, threshold_pct=0.05, max_horizon_months=3)

    assert result["censored"] is True
    assert np.isnan(result["recovery_time_months"])
    assert result["recovery_idx"] is None


def test_recovery_capacity_score_no_hardcoded_sign():
    # The function should be a pure, symmetric min-max blend — flipping which
    # country has the higher military spend should just flip which country
    # scores higher, with no built-in directional assumption.
    df = pd.DataFrame({
        "military_exp_pct_gdp": [1.0, 5.0],
        "gov_expenditure_pct_gdp": [10.0, 10.0],
    })
    scored = recovery_capacity_score(df)
    assert scored.iloc[1] > scored.iloc[0]
    assert scored.between(0, 1).all()


def test_recovery_capacity_score_missing_dev_column_falls_back_gracefully():
    df = pd.DataFrame({"military_exp_pct_gdp": [1.0, 2.0, 3.0]})
    scored = recovery_capacity_score(df)
    assert len(scored) == 3
    assert scored.between(0, 1).all()
