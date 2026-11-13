"""Spec 04, requirement R4.6: probabilities against a line are exact and sum to one."""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats as sps

from better import stats
from better.model import dist


def test_every_stat_has_a_family():
    families = {stat.key: dist.family_of(stat) for stat in stats.STATS}
    assert families["points"] == families["pts_rebs_asts"] == dist.NEGBIN
    assert families["fantasy_score"] == dist.GAMMA
    assert families["double_double"] == dist.BERNOULLI


@pytest.mark.parametrize(("mean", "dispersion"), [(0.6, 0.0), (4.2, 0.15), (24.0, 0.08)])
def test_the_count_cdf_matches_scipy(mean, dispersion):
    k = np.arange(-2, 80)
    if dispersion == 0:
        expected = sps.poisson.cdf(k, mean)
    else:
        size = 1 / dispersion
        expected = sps.nbinom.cdf(k, size, size / (size + mean))
    assert dist.cdf(dist.NEGBIN, k, mean, dispersion) == pytest.approx(expected, abs=1e-12)
    # A count cannot fall between whole numbers: the CDF is flat across each step.
    assert dist.cdf(dist.NEGBIN, 7.9, mean, dispersion) == pytest.approx(
        dist.cdf(dist.NEGBIN, 7, mean, dispersion))


def test_the_count_variance_is_what_the_dispersion_says():
    rng = np.random.default_rng(1)
    mean, dispersion = 12.0, 0.2
    size = 1 / dispersion
    draws = rng.negative_binomial(size, size / (size + mean), 400_000)
    assert draws.var() == pytest.approx(dist.variance(dist.NEGBIN, mean, dispersion), rel=0.02)
    assert dist.variance(dist.NEGBIN, mean, dispersion) == mean + dispersion * mean**2


@pytest.mark.parametrize(
    ("line", "mean", "dispersion"),
    [(24.5, 23.0, 0.1), (24.0, 23.0, 0.1), (0.5, 0.7, 0.0), (1.0, 0.7, 0.3), (60.5, 23.0, 0.1)],
)
def test_count_probabilities_against_brute_force(line, mean, dispersion):
    k = np.arange(0, 400)
    if dispersion == 0:
        pmf = sps.poisson.pmf(k, mean)
    else:
        size = 1 / dispersion
        pmf = sps.nbinom.pmf(k, size, size / (size + mean))
    over, under, push = dist.probabilities(dist.NEGBIN, line, mean, dispersion)
    assert over == pytest.approx(pmf[k > line].sum(), abs=1e-10)
    assert under == pytest.approx(pmf[k < line].sum(), abs=1e-10)
    assert push == pytest.approx(pmf[k == line].sum(), abs=1e-10)
    assert over + under + push == pytest.approx(1.0)


def test_a_half_point_line_cannot_push():
    _, _, push = dist.probabilities(dist.NEGBIN, np.array([0.5, 7.5, 24.5]), 8.0, 0.1)
    assert np.all(push == 0)


def test_probabilities_broadcast_over_arrays():
    lines = np.array([9.5, 10.0, 10.5])
    means = np.array([10.0, 10.0, 12.0])
    over, under, push = dist.probabilities(dist.NEGBIN, lines, means, 0.05)
    assert over.shape == (3,)
    assert np.allclose(over + under + push, 1.0)
    assert over[0] > over[1]      # a lower line is easier to beat
    assert over[2] > over[0]      # ... and a higher mean beats a similar line more often
    assert push[1] > 0 and push[0] == push[2] == 0


def test_the_score_family_lives_on_a_grid_of_tenths():
    mean, dispersion = 30.0, 0.09
    shape = 1 / dispersion
    over, under, push = dist.probabilities(dist.GAMMA, 31.5, mean, dispersion)
    law = sps.gamma(shape, scale=mean / shape)
    assert under == pytest.approx(law.cdf(31.45))
    assert over == pytest.approx(law.sf(31.55))
    assert push == pytest.approx(law.cdf(31.55) - law.cdf(31.45))
    assert 0 < push < 0.01
    assert over + under + push == pytest.approx(1.0)
    assert dist.variance(dist.GAMMA, mean, dispersion) == pytest.approx(law.var())


def test_a_flag_is_over_with_its_probability():
    over, under, push = dist.probabilities(dist.BERNOULLI, 0.5, np.array([0.2, 0.9]))
    assert over == pytest.approx([0.2, 0.9])
    assert under == pytest.approx([0.8, 0.1])
    assert np.all(push == 0)


@pytest.mark.parametrize(
    ("family", "mean", "dispersion"),
    [(dist.NEGBIN, 2.3, 0.4), (dist.NEGBIN, 18.0, 0.07), (dist.GAMMA, 28.0, 0.12)],
)
def test_the_pit_of_a_correct_distribution_is_uniform(family, mean, dispersion):
    rng = np.random.default_rng(7)
    if family == dist.NEGBIN:
        size = 1 / dispersion
        y = rng.negative_binomial(size, size / (size + mean), 200_000)
    else:
        shape = 1 / dispersion
        y = np.round(rng.gamma(shape, mean / shape, 200_000), 1)
    u = dist.pit(family, y, mean, dispersion, rng)
    assert u.min() >= 0 and u.max() <= 1
    deciles = np.histogram(u, bins=10, range=(0, 1))[0] / len(u)
    assert deciles == pytest.approx(np.full(10, 0.1), abs=0.004)


def test_the_pit_of_a_wrong_distribution_is_not_uniform():
    rng = np.random.default_rng(7)
    y = rng.poisson(10.0, 100_000)
    too_narrow = dist.pit(dist.NEGBIN, y, 10.0, 0.0, rng)   # right
    too_low = dist.pit(dist.NEGBIN, y, 8.0, 0.0, rng)       # mean understated
    assert abs(too_narrow.mean() - 0.5) < 0.005
    assert too_low.mean() > 0.6
    # Without a generator the midpoint of each jump is used: deterministic.
    assert np.array_equal(dist.pit(dist.NEGBIN, y, 10.0), dist.pit(dist.NEGBIN, y, 10.0))


def test_a_calibration_map_reshapes_the_probabilities_and_keeps_them_summing_to_one():
    # A map that says the model's CDF is too low everywhere: stats come in lower.
    lowered = dist.Calibration(np.array([0.0, 0.5, 1.0]), np.array([0.0, 0.6, 1.0]))
    plain = dist.probabilities(dist.NEGBIN, 10.0, 10.0, 0.1)
    moved = dist.probabilities(dist.NEGBIN, 10.0, 10.0, 0.1, lowered)
    assert sum(moved) == pytest.approx(1.0)
    assert moved[0] < plain[0] and moved[1] > plain[1]
    assert dist.IDENTITY.is_identity and not lowered.is_identity
    again = dist.probabilities(dist.NEGBIN, 10.0, 10.0, 0.1, dist.IDENTITY)
    assert np.allclose(np.array(again), np.array(plain))


@pytest.mark.parametrize(
    ("family", "y", "mean", "dispersion", "expected"),
    [
        (dist.NEGBIN, 3, 4.0, 0.0, sps.poisson.logpmf(3, 4.0)),
        (dist.NEGBIN, 3, 4.0, 0.5, sps.nbinom.logpmf(3, 2.0, 2.0 / 6.0)),
        (dist.GAMMA, 20.0, 25.0, 0.25, sps.gamma.logpdf(20.0, 4.0, scale=6.25)),
        (dist.BERNOULLI, 1, 0.3, 0.0, np.log(0.3)),
        (dist.BERNOULLI, 0, 0.3, 0.0, np.log(0.7)),
    ],
)
def test_log_likelihood(family, y, mean, dispersion, expected):
    assert dist.log_likelihood(family, y, mean, dispersion) == pytest.approx(expected)


def test_the_median_splits_the_distribution():
    for mean, dispersion in [(1.2, 0.3), (9.0, 0.1), (26.0, 0.05)]:
        m = dist.median(dist.NEGBIN, mean, dispersion)
        assert dist.cdf(dist.NEGBIN, m, mean, dispersion) >= 0.5
        assert dist.cdf(dist.NEGBIN, m - 1, mean, dispersion) < 0.5
    assert dist.cdf(dist.GAMMA, dist.median(dist.GAMMA, 30.0, 0.1), 30.0, 0.1) == pytest.approx(0.5)


def test_degenerate_inputs_do_not_blow_up():
    over, under, push = dist.probabilities(dist.NEGBIN, 0.5, 0.0, 0.2)  # a mean of zero
    assert (over, under, push) == pytest.approx((0.0, 1.0, 0.0), abs=1e-5)
    assert dist.cdf(dist.NEGBIN, -1, 5.0, 0.1) == 0
    assert dist.cdf(dist.GAMMA, -3.0, 5.0, 0.1) == 0
    with pytest.raises(ValueError):
        dist.cdf("cauchy", 1, 1)
