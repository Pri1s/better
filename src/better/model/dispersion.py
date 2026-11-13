"""Fit predictive spread to earlier out-of-sample means by maximum likelihood.

The caller owns the chronological split: fitting this object on in-sample means
would understate forecast uncertainty. Negative-binomial and gamma spreads are
alpha; normal spread is variance. Bernoulli has no separate spread parameter.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize, special

from better.model import dist

_MIN_ROWS = 30
_MIN_LOG_SPREAD = -12.0
_MAX_LOG_SPREAD = 12.0


def _design(family: str, mean, minutes_cv, started_share) -> np.ndarray:
    mean, cv, starts = np.broadcast_arrays(
        np.asarray(mean, dtype=float), np.asarray(minutes_cv, dtype=float),
        np.asarray(started_share, dtype=float),
    )
    scale = np.maximum(np.abs(mean), 1) if family == dist.NORMAL else np.maximum(mean, 1e-6)
    return np.stack([
        np.ones(mean.shape), np.log(scale),
        np.clip(np.nan_to_num(cv, nan=0), 0, 5),
        np.clip(np.nan_to_num(starts, nan=0), 0, 1),
    ], axis=-1)


@dataclass(frozen=True)
class DispersionFit:
    """A four-coefficient log-spread regression, serializable with fitted models."""

    family: str
    coefficients: np.ndarray
    n_observations: int = 0
    converged: bool = False

    def predict(self, mean, minutes_cv=0.0, started_share=0.0) -> np.ndarray:
        design = _design(self.family, mean, minutes_cv, started_share)
        if self.family == dist.BERNOULLI:
            return np.zeros(design.shape[:-1])
        return np.exp(np.clip(
            design @ self.coefficients, _MIN_LOG_SPREAD, _MAX_LOG_SPREAD,
        ))


def default(family: str) -> DispersionFit:
    """A deliberately broad starting spread before out-of-sample rows exist."""
    if family not in (dist.NEGBIN, dist.GAMMA, dist.NORMAL, dist.BERNOULLI):
        raise ValueError(f"unknown family {family!r}")
    initial = 100.0 if family == dist.NORMAL else 0.2
    return DispersionFit(family, np.array([np.log(initial), 0.0, 0.0, 0.0]))


def fit(family: str, y, mean, minutes_cv=0.0, started_share=0.0) -> DispersionFit:
    """Fit log-spread to residuals; missing outcomes are excluded explicitly.

    Sparse samples use a constant moment estimate. Normal fits use the density
    likelihood (rounding changes variance by at most 1/1200); forecasts and their
    evaluation use exact probability mass on the fantasy-score tenths grid.
    """
    fallback = default(family)
    y, mean, cv, starts = np.broadcast_arrays(
        np.asarray(y, dtype=float), np.asarray(mean, dtype=float),
        np.asarray(minutes_cv, dtype=float), np.asarray(started_share, dtype=float),
    )
    valid = np.isfinite(y) & np.isfinite(mean)
    y, mean, cv, starts = (a[valid] for a in (y, mean, cv, starts))
    n = len(y)
    if family == dist.BERNOULLI:
        return DispersionFit(family, np.zeros(4), n, True)
    if not n:
        return fallback
    if family == dist.NEGBIN and (np.any(y < 0) or np.any(y != np.floor(y))):
        raise ValueError("count observations must be nonnegative whole numbers")
    if family in (dist.NEGBIN, dist.GAMMA) and np.any(mean < 0):
        raise ValueError("count and gamma means must be nonnegative")
    if family == dist.GAMMA and np.any(y < 0):
        raise ValueError("gamma observations must be nonnegative")
    if family != dist.NORMAL:
        mean = np.maximum(mean, 1e-6)
    squared_error = (y - mean)**2
    if family == dist.NORMAL:
        initial = max(float(np.mean(squared_error)), 1e-3)
    else:
        numerator = squared_error - mean if family == dist.NEGBIN else squared_error
        initial = max(float(np.sum(numerator) / np.sum(mean**2)), 1e-4)
    coefficients = np.array([np.log(initial), 0.0, 0.0, 0.0])
    if n < _MIN_ROWS:
        return DispersionFit(family, coefficients, n, False)
    design = _design(family, mean, cv, starts)
    if family == dist.GAMMA:
        y = np.maximum(y, dist.SCORE_STEP / 2)

    def objective(coefs):
        raw = design @ coefs
        eta = np.clip(raw, _MIN_LOG_SPREAD, _MAX_LOG_SPREAD)
        inverse = np.exp(-eta)
        if family == dist.NEGBIN:
            size = inverse
            logp = np.log(size) - np.log(size + mean)
            likelihood = (
                special.gammaln(y + size) - special.gammaln(size)
                - special.gammaln(y + 1) + size * logp
                + y * (np.log(mean) - np.log(size + mean))
            )
            score = -size * (
                special.digamma(y + size) - special.digamma(size)
                + logp + 1 - (size + y) / (size + mean)
            )
        elif family == dist.GAMMA:
            shape = inverse
            log_ratio = np.log(y / mean)
            likelihood = (
                shape * np.log(shape) - np.log(y) - special.gammaln(shape)
                + shape * log_ratio - shape * y / mean
            )
            score = -shape * (
                np.log(shape) + 1 - special.digamma(shape) + log_ratio - y / mean
            )
        else:
            likelihood = -0.5 * (np.log(2 * np.pi) + eta + squared_error * inverse)
            score = 0.5 * (squared_error * inverse - 1)
        score *= (raw >= _MIN_LOG_SPREAD) & (raw <= _MAX_LOG_SPREAD)
        return -float(np.mean(likelihood)), -(design.T @ score) / n

    result = optimize.minimize(
        objective, coefficients, method="L-BFGS-B", jac=True,
        bounds=[(-12, 12), (-4, 4), (-4, 4), (-4, 4)],
        options={"maxiter": 150, "ftol": 1e-10},
    )
    if not np.isfinite(result.fun) or not np.all(np.isfinite(result.x)):
        return DispersionFit(family, coefficients, n, False)
    return DispersionFit(family, result.x, n, bool(result.success))
