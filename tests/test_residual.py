import numpy as np
import pandas as pd

from analysis.residual import fit_gdp_ntl_residual


def test_fit_recovers_known_log_log_relationship():
    # log_ntl = 2.0 + 0.5 * log_gdp, no noise -> the robust fit should recover it
    # and residuals should be ~0. (GDP-only: pop_col=None.)
    rng = np.random.default_rng(0)
    gdp = rng.uniform(1e8, 1e11, size=30)
    ntl = np.exp(2.0 + 0.5 * np.log(gdp))

    df = pd.DataFrame({
        "iso3": [f"C{i:02d}" for i in range(30)],
        "year": 2020,
        "gdp_constant_usd": gdp,
        "sum_of_lights": ntl,
    })

    result = fit_gdp_ntl_residual(df, pop_col=None)
    assert np.allclose(result["beta_gdp"].iloc[0], 0.5, atol=1e-3)
    assert np.allclose(result["alpha"].iloc[0], 2.0, atol=1e-2)
    assert np.allclose(result["residual"], 0.0, atol=1e-3)


def test_population_control_recovers_two_slopes():
    # log_ntl = 1.0 + 0.6*log_gdp + 0.3*log_pop -> recover both slopes.
    rng = np.random.default_rng(3)
    gdp = rng.uniform(1e8, 1e12, size=40)
    pop = rng.uniform(1e5, 1e9, size=40)
    ntl = np.exp(1.0 + 0.6 * np.log(gdp) + 0.3 * np.log(pop))

    df = pd.DataFrame({
        "iso3": [f"C{i:02d}" for i in range(40)],
        "year": 2019,
        "gdp_constant_usd": gdp,
        "population": pop,
        "sum_of_lights": ntl,
    })

    result = fit_gdp_ntl_residual(df)
    assert np.allclose(result["beta_gdp"].iloc[0], 0.6, atol=1e-2)
    assert np.allclose(result["beta_pop"].iloc[0], 0.3, atol=1e-2)
    assert np.allclose(result["residual"], 0.0, atol=1e-2)


def test_robust_fit_ignores_a_wild_outlier():
    # 29 points on a clean line + 1 wild outlier; robust fit should stay close
    # to the true slope (a plain OLS would be pulled off it).
    rng = np.random.default_rng(7)
    gdp = rng.uniform(1e8, 1e11, size=30)
    ntl = np.exp(2.0 + 0.5 * np.log(gdp))
    ntl[0] = np.exp(2.0 + 0.5 * np.log(gdp[0]) - 6.0)  # one country wildly dim

    df = pd.DataFrame({
        "iso3": [f"C{i:02d}" for i in range(30)],
        "year": 2020,
        "gdp_constant_usd": gdp,
        "sum_of_lights": ntl,
    })
    result = fit_gdp_ntl_residual(df, pop_col=None)
    assert abs(result["beta_gdp"].iloc[0] - 0.5) < 0.05
    # The outlier itself should carry a large negative residual.
    assert result.sort_values("residual")["residual"].iloc[0] < -3


def test_fit_floors_nonpositive_sum_of_lights():
    df = pd.DataFrame({
        "iso3": ["A", "B", "C", "D", "E", "F"],
        "year": 2021,
        "gdp_constant_usd": [1e9, 2e9, 3e9, 4e9, 5e9, 6e9],
        "sum_of_lights": [100.0, -5.0, 0.0, 200.0, 150.0, 175.0],
    })
    result = fit_gdp_ntl_residual(df, pop_col=None)
    assert result["log_ntl"].notna().all()
    assert np.isfinite(result["log_ntl"]).all()


def test_fit_is_independent_per_year():
    rng = np.random.default_rng(1)
    rows = []
    for year, beta in [(2020, 0.3), (2021, 0.8)]:
        gdp = rng.uniform(1e8, 1e11, size=20)
        ntl = np.exp(1.0 + beta * np.log(gdp))
        for i in range(20):
            rows.append({"iso3": f"C{i:02d}", "year": year, "gdp_constant_usd": gdp[i], "sum_of_lights": ntl[i]})
    df = pd.DataFrame(rows)

    result = fit_gdp_ntl_residual(df, pop_col=None)
    beta_2020 = result.loc[result["year"] == 2020, "beta_gdp"].iloc[0]
    beta_2021 = result.loc[result["year"] == 2021, "beta_gdp"].iloc[0]
    assert np.allclose(beta_2020, 0.3, atol=1e-2)
    assert np.allclose(beta_2021, 0.8, atol=1e-2)


def test_fit_returns_nan_coefficients_when_too_few_rows():
    df = pd.DataFrame({
        "iso3": ["A", "B"],
        "year": 2022,
        "gdp_constant_usd": [1e9, 2e9],
        "sum_of_lights": [100.0, 200.0],
    })
    result = fit_gdp_ntl_residual(df, pop_col=None)
    assert result["beta_gdp"].isna().all()
    assert result["fit_n"].iloc[0] == 2
