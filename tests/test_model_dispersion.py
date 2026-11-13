"""Spread fits recover a simulated law from out-of-sample means."""

import numpy as np
import pytest

from better.model import dispersion, dist


@pytest.mark.parametrize("family", [dist.NEGBIN, dist.GAMMA, dist.NORMAL])
def test_spread_regression_recovers_known_parameters(family):
    rng = np.random.default_rng(204)
    n = 35_000
    mean = rng.uniform(3, 35, n)
    cv = rng.uniform(0.05, 0.8, n)
    starts = rng.integers(0, 2, n).astype(float)
    coefficients = np.array([-2.4, 0.2, 0.6, -0.3])
    if family == dist.NORMAL:
        coefficients[0] = 1.1
    spread = np.exp(
        coefficients[0] + coefficients[1] * np.log(mean)
        + coefficients[2] * cv + coefficients[3] * starts
    )
    if family == dist.NEGBIN:
        y = rng.negative_binomial(1 / spread, 1 / (1 + spread * mean))
    elif family == dist.GAMMA:
        y = rng.gamma(1 / spread, mean * spread)
    else:
        mean = -mean  # Negative fantasy means must not be clipped.
        y = np.round(rng.normal(mean, np.sqrt(spread)), 1)
    model = dispersion.fit(family, y, mean, cv, starts)
    assert model.converged
    assert model.n_observations == n
    assert model.coefficients == pytest.approx(coefficients, abs=0.09)
    fitted = model.predict(mean, cv, starts)
    assert np.mean(np.abs(fitted / spread - 1)) < 0.025


def test_zero_and_missing_history_have_finite_fallbacks():
    for family in (dist.NEGBIN, dist.GAMMA, dist.NORMAL, dist.BERNOULLI):
        model = dispersion.fit(family, [], [], [], [])
        assert model.n_observations == 0
        result = model.predict([0.01, 3], [np.nan, 0.2], [np.nan, 1])
        assert np.all(np.isfinite(result)) and np.all(result >= 0)


def test_a_bernoulli_has_no_free_dispersion():
    model = dispersion.fit(dist.BERNOULLI, [1, 0, 0], [0.5, 0.2, 0.1], 0, 0)
    assert model.predict([0.5, 0.2, 0.1], 0, 0) == pytest.approx([0, 0, 0])


def test_spread_fit_rejects_invalid_count_observations():
    with pytest.raises(ValueError, match="count"):
        dispersion.fit(dist.NEGBIN, [1, -1], [1, 1], 0, 0)
