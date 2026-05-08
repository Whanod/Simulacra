"""Generic weight-generation helpers.

Extracted from quant-simulation models/weights.py. Only general-purpose
math ships here — prediction-market-specific scenario families stay out.
"""

from __future__ import annotations

import numpy as np
from scipy import stats


def gaussian_weights(
    num_assets: int,
    mu: float,
    sigma: float,
    normalize_to: int = 10**9,
) -> np.ndarray:
    """Compute Gaussian-distributed weights across asset indices.
    Returns int array summing to normalize_to.
    Uses exact scipy.stats.norm (not Taylor-4 approximation).

    mu and sigma are in index space (0 to num_assets-1).
    """
    if num_assets <= 0:
        return np.array([], dtype=np.int64)
    if sigma <= 0:
        # Degenerate: all weight on nearest bin to mu
        weights = np.zeros(num_assets, dtype=np.int64)
        idx = max(0, min(num_assets - 1, int(round(mu))))
        weights[idx] = normalize_to
        return weights

    # Compute PDF at bin centers
    centers = np.arange(num_assets) + 0.5
    raw = stats.norm.pdf(centers, loc=mu, scale=sigma)

    return normalize_weights(raw, normalize_to)


def uniform_weights(num_assets: int, normalize_to: int = 10**9) -> np.ndarray:
    """Equal weights across all assets. Handles remainder distribution
    so the array sums to exactly normalize_to."""
    if num_assets <= 0:
        return np.array([], dtype=np.int64)

    base = normalize_to // num_assets
    remainder = normalize_to - base * num_assets
    weights = np.full(num_assets, base, dtype=np.int64)
    # Distribute remainder across first bins
    weights[:remainder] += 1
    return weights


def dirichlet_weights(
    num_assets: int,
    alpha: float,
    rng: np.random.Generator,
    normalize_to: int = 10**9,
) -> np.ndarray:
    """Random weights from symmetric Dirichlet distribution.
    Useful for generating random agent beliefs."""
    if num_assets <= 0:
        return np.array([], dtype=np.int64)

    raw = rng.dirichlet(np.full(num_assets, alpha))
    return normalize_weights(raw, normalize_to)


def normalize_weights(raw: np.ndarray, normalize_to: int = 10**9) -> np.ndarray:
    """Normalize any non-negative float/int array to int array
    summing to exactly normalize_to. Uses largest-remainder method for rounding."""
    if len(raw) == 0:
        return np.array([], dtype=np.int64)

    total = raw.sum()
    if total <= 0:
        # Fallback: uniform
        return uniform_weights(len(raw), normalize_to)

    # Scale to target
    scaled = raw * (normalize_to / total)

    # Floor values
    floored = np.floor(scaled).astype(np.int64)
    remainders = scaled - floored

    # Distribute deficit using largest-remainder method
    deficit = normalize_to - floored.sum()
    if deficit > 0:
        indices = np.argsort(-remainders)[:deficit]
        floored[indices] += 1

    return floored
