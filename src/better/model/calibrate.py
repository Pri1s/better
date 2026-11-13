"""Monotone CDF calibration from earlier out-of-sample forecast PIT values."""

from __future__ import annotations

import numpy as np

from better.model import dist

MIN_OBSERVATIONS = 30


def fit_pit(values, *, max_knots: int = 201) -> dist.Calibration:
    """Approximate the empirical CDF with bounded, monotone interpolation knots.

    Ranks are already monotone, so an isotonic regression leaves them unchanged.
    Quantile thinning bounds storage without losing more than one rank interval
    between knots. Endpoints are fixed to preserve total probability.
    """
    if max_knots < 3:
        raise ValueError("max_knots must be at least three")
    values = np.asarray(values, dtype=float).ravel()
    values = values[np.isfinite(values)]
    if np.any((values < 0) | (values > 1)):
        raise ValueError("PIT values must be between zero and one")
    if len(values) < MIN_OBSERVATIONS:
        return dist.IDENTITY
    values.sort()
    unique, first, counts = np.unique(values, return_index=True, return_counts=True)
    ranks = (first + counts / 2) / len(values)
    interior = (unique > 0) & (unique < 1)
    unique, ranks = unique[interior], ranks[interior]
    if len(unique) > max_knots - 2:
        selected = np.unique(np.linspace(0, len(unique) - 1, max_knots - 2).astype(int))
        unique, ranks = unique[selected], ranks[selected]
    return dist.Calibration(np.r_[0, unique, 1], np.r_[0, ranks, 1])


def fit(
    family: str, y, mean, dispersion=0.0, *, seed: int = 0, max_knots: int = 201,
) -> dist.Calibration:
    """Fit using randomised PIT; repeatable with an explicit random seed.

    Only the caller can establish that the supplied forecasts precede outcomes
    and the fitted map's target date. The walk-forward pipeline enforces that.
    """
    values = dist.pit(family, y, mean, dispersion, np.random.default_rng(seed))
    return fit_pit(values, max_knots=max_knots)
