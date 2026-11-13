"""Monotone calibration learns a CDF distortion on an independent sample."""

import numpy as np
import pytest

from better.model import calibrate, dist


def test_calibration_recovers_known_probability_transform():
    rng = np.random.default_rng(83)
    train = rng.beta(2, 1, 40_000)
    mapping = calibrate.fit_pit(train)
    grid = np.linspace(0, 1, 41)
    assert mapping(grid) == pytest.approx(grid**2, abs=0.008)
    held_out = mapping(rng.beta(2, 1, 40_000))
    assert np.histogram(held_out, bins=10, range=(0, 1))[0] / len(held_out) == pytest.approx(
        np.full(10, 0.1), abs=0.008
    )
    assert np.all(np.diff(mapping.x) > 0)
    assert np.all(np.diff(mapping.y) >= 0)
    assert mapping(0) == 0 and mapping(1) == 1


def test_a_correct_distribution_learns_near_identity():
    rng = np.random.default_rng(3)
    train = rng.negative_binomial(5, 0.25, 50_000)
    mapping = calibrate.fit(dist.NEGBIN, train, 15, 0.2)
    grid = np.linspace(0, 1, 101)
    assert mapping(grid) == pytest.approx(grid, abs=0.008)
    repeated = calibrate.fit(dist.NEGBIN, train, 15, 0.2)
    assert np.array_equal(mapping.x, repeated.x)
    assert np.array_equal(mapping.y, repeated.y)


def test_calibration_corrects_the_spread_on_unseen_scores():
    rng = np.random.default_rng(8)
    train = np.round(rng.normal(0, 3, 40_000), 1)
    mapping = calibrate.fit(dist.NORMAL, train, 0, 4)
    held_out = np.round(rng.normal(0, 3, 50_000), 1)
    corrected = mapping(dist.pit(dist.NORMAL, held_out, 0, 4, rng))
    assert np.histogram(corrected, bins=10, range=(0, 1))[0] / len(corrected) == pytest.approx(
        np.full(10, 0.1), abs=0.008
    )


def test_small_samples_leave_calibration_alone():
    assert calibrate.fit_pit([0.8, 0.7]).is_identity
    assert calibrate.fit(dist.NORMAL, [], [], []).is_identity


def test_duplicate_pits_and_endpoint_values_do_not_break_knots():
    mapping = calibrate.fit_pit(np.repeat([0, 0.3, 0.8, 1], 100))
    assert np.all(np.diff(mapping.x) > 0)
    assert np.all(np.diff(mapping.y) >= 0)
    assert mapping(0) == 0 and mapping(1) == 1
