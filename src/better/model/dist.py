"""Predictive distributions for a stat, and probabilities against a line.

Every stat gets one of three families:

``negbin``     a count (points, rebounds, their sums). Negative binomial with
               variance ``mean + dispersion * mean**2``. Dispersion 0 is a Poisson.
``gamma``      a weighted score that is not a whole number (fantasy score). Gamma
               with variance ``dispersion * mean**2``, read on a grid of tenths.
``bernoulli``  something that happens or does not (double-double). ``mean`` is the
               probability; there is no dispersion.

A ``Calibration`` is a monotone map from the model's cumulative probability to a
calibrated one. It is fitted on past out-of-sample predictions (see ``calibrate``)
and applied here, so every probability the project reports goes through one place.

All functions accept arrays and broadcast.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import special
from scipy import stats as sps

from better.stats import COUNT, FLAG, SCORE, Stat

NEGBIN = "negbin"
GAMMA = "gamma"
BERNOULLI = "bernoulli"
FAMILY_OF_KIND = {COUNT: NEGBIN, SCORE: GAMMA, FLAG: BERNOULLI}

# PrizePicks prints a fantasy score to one decimal, so that is the grid it lives on.
SCORE_STEP = 0.1
_POISSON_BELOW = 1e-8   # a dispersion this small is treated as exactly Poisson
_MIN_MEAN = 1e-6


def family_of(stat: Stat) -> str:
    return FAMILY_OF_KIND[stat.kind]


@dataclass(frozen=True)
class Calibration:
    """A monotone map on [0, 1], stored as the knots of a piecewise-linear curve."""

    x: np.ndarray
    y: np.ndarray

    def __call__(self, u: np.ndarray) -> np.ndarray:
        return np.interp(u, self.x, self.y)

    @property
    def is_identity(self) -> bool:
        return bool(np.allclose(self.x, self.y))


IDENTITY = Calibration(np.array([0.0, 1.0]), np.array([0.0, 1.0]))


def cdf(family: str, x, mean, dispersion=0.0) -> np.ndarray:
    """P(X <= x) under the family, before any calibration."""
    x = np.asarray(x, dtype=float)
    mean = np.maximum(np.asarray(mean, dtype=float), _MIN_MEAN)
    dispersion = np.asarray(dispersion, dtype=float)
    if family == NEGBIN:
        return _negbin_cdf(np.floor(x), mean, dispersion)
    if family == GAMMA:
        shape = 1.0 / np.maximum(dispersion, 1e-6)
        return np.where(x <= 0, 0.0, special.gammainc(shape, np.maximum(x, 0.0) * shape / mean))
    if family == BERNOULLI:
        p = np.clip(mean, 0.0, 1.0)
        return np.where(x < 0, 0.0, np.where(x < 1, 1.0 - p, 1.0))
    raise ValueError(f"unknown family {family!r}")


def probabilities(
    family: str, line, mean, dispersion=0.0, calibration: Calibration = IDENTITY
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """``(p_over, p_under, p_push)`` for a line. The three always sum to 1.

    A push needs the stat to land exactly on the line, so it is zero for a count
    against a half-point line.
    """
    line = np.asarray(line, dtype=float)
    if family == GAMMA:
        # Values sit on a grid of tenths: "over 31.5" means 31.6 or more.
        below = calibration(cdf(family, line - SCORE_STEP / 2, mean, dispersion))
        at_or_below = calibration(cdf(family, line + SCORE_STEP / 2, mean, dispersion))
    else:
        on_grid = np.floor(line) == line
        at_or_below = calibration(cdf(family, np.floor(line), mean, dispersion))
        below = np.where(
            on_grid, calibration(cdf(family, line - 1.0, mean, dispersion)), at_or_below
        )
    p_over = np.asarray(1.0 - at_or_below, dtype=float)
    p_under = np.asarray(below, dtype=float)
    p_push = np.asarray(np.clip(at_or_below - below, 0.0, 1.0), dtype=float)
    return p_over, p_under, p_push


def pit(family: str, y, mean, dispersion=0.0, rng: np.random.Generator | None = None):
    """The probability integral transform of observed values.

    For a discrete family the transform is spread uniformly over the jump at ``y``
    (using ``rng``, or the midpoint when none is given). If the predictive
    distribution is right, the result is uniform on [0, 1], which is what makes it
    the basis for calibration.
    """
    y = np.asarray(y, dtype=float)
    if family == GAMMA:
        upper = cdf(family, y + SCORE_STEP / 2, mean, dispersion)
        lower = cdf(family, y - SCORE_STEP / 2, mean, dispersion)
    else:
        upper = cdf(family, y, mean, dispersion)
        lower = cdf(family, y - 1.0, mean, dispersion)
    spread = 0.5 if rng is None else rng.random(np.broadcast(upper, lower).shape)
    return lower + spread * (upper - lower)


def log_likelihood(family: str, y, mean, dispersion=0.0) -> np.ndarray:
    """Log probability (or density) of observed values."""
    y = np.asarray(y, dtype=float)
    mean = np.maximum(np.asarray(mean, dtype=float), _MIN_MEAN)
    dispersion = np.asarray(dispersion, dtype=float)
    if family == NEGBIN:
        poisson = sps.poisson.logpmf(y, mean)
        size = 1.0 / np.maximum(dispersion, _POISSON_BELOW)
        negbin = sps.nbinom.logpmf(y, size, size / (size + mean))
        return np.where(dispersion < _POISSON_BELOW, poisson, negbin)
    if family == GAMMA:
        shape = 1.0 / np.maximum(dispersion, 1e-6)
        return sps.gamma.logpdf(np.maximum(y, SCORE_STEP / 2), shape, scale=mean / shape)
    if family == BERNOULLI:
        p = np.clip(mean, 1e-9, 1 - 1e-9)
        return np.where(y >= 0.5, np.log(p), np.log1p(-p))
    raise ValueError(f"unknown family {family!r}")


def variance(family: str, mean, dispersion=0.0) -> np.ndarray:
    mean = np.asarray(mean, dtype=float)
    dispersion = np.asarray(dispersion, dtype=float)
    if family == NEGBIN:
        return mean + dispersion * mean**2
    if family == GAMMA:
        return dispersion * mean**2
    if family == BERNOULLI:
        return mean * (1.0 - mean)
    raise ValueError(f"unknown family {family!r}")


def median(family: str, mean, dispersion=0.0) -> np.ndarray:
    """The value the stat is as likely to fall at or below as above."""
    mean = np.maximum(np.asarray(mean, dtype=float), _MIN_MEAN)
    dispersion = np.asarray(dispersion, dtype=float)
    if family == NEGBIN:
        size = 1.0 / np.maximum(dispersion, _POISSON_BELOW)
        negbin = sps.nbinom.ppf(0.5, size, size / (size + mean))
        return np.where(dispersion < _POISSON_BELOW, sps.poisson.ppf(0.5, mean), negbin)
    if family == GAMMA:
        shape = 1.0 / np.maximum(dispersion, 1e-6)
        return sps.gamma.ppf(0.5, shape, scale=mean / shape)
    if family == BERNOULLI:
        return (mean >= 0.5).astype(float)
    raise ValueError(f"unknown family {family!r}")


def _negbin_cdf(k: np.ndarray, mean: np.ndarray, dispersion: np.ndarray) -> np.ndarray:
    """P(X <= k) for whole ``k``; 0 below zero. Poisson when dispersion is 0."""
    k, mean, dispersion = np.broadcast_arrays(k, mean, dispersion)
    safe_k = np.maximum(k, 0.0)
    size = 1.0 / np.maximum(dispersion, _POISSON_BELOW)
    # nbinom.cdf(k; n, p) is the regularised incomplete beta I_p(n, k + 1).
    negbin = special.betainc(size, safe_k + 1.0, size / (size + mean))
    poisson = special.gammaincc(safe_k + 1.0, mean)
    result = np.where(dispersion < _POISSON_BELOW, poisson, negbin)
    return np.where(k < 0, 0.0, result)
