"""Multi-token price aggregators exposed as :class:`OracleSource` views.

PRD US-006 step 1.8b sweep: the legacy ``PriceFeed`` ABC and the
``LegacyFeedAsOracle`` shim have been deleted. Multi-token feed
generators (historical replays, stochastic processes, composites) live
on as concrete aggregators that produce per-token
:class:`~defi_sim.engine.oracles.source.OracleSource` views via
``oracle_for(token)``. Consumers always call ``price_at(slot)`` on the
view — there is no chain-neutral ``get_price(token, round)`` interface
anymore.
"""

from __future__ import annotations

from typing import Any, Literal

import numpy as np

from defi_sim.core.types import Numeric, TokenId
from defi_sim.engine.oracles.source import OracleSource


class _FeedTokenView(OracleSource):
    """Per-token :class:`OracleSource` view backed by a multi-token feed.

    Multi-token feeds keep their internal storage (a dict-of-arrays /
    cached stochastic path / composite registry) but project a per-token
    Solana-shaped oracle view through this adapter. Confidence is zero
    by default: the feed generators don't model an aggregator's
    uncertainty band — calibration in 2.4 lifts that when needed.
    """

    update_mode: Literal["push", "pull"] = "push"
    confidence_interval: float = 0.0

    def __init__(self, feed: "_MultiTokenFeed", token: TokenId) -> None:
        self._feed = feed
        self._token = token

    def price_at(self, slot: int) -> tuple[Numeric, float]:
        return self._feed._price_for(self._token, slot), self.confidence_interval


class _MultiTokenFeed:
    """Marker base for the concrete feed aggregators in this module.

    Subclasses implement ``_price_for(token, slot)`` and inherit
    :meth:`oracle_for`. Not exported — callers reach into the concrete
    types (``HistoricalFeed`` / ``StochasticFeed`` / ``CompositeFeed``)
    directly.
    """

    def _price_for(self, token: TokenId, slot: int) -> Numeric:
        raise NotImplementedError

    def oracle_for(self, token: TokenId) -> OracleSource:
        return _FeedTokenView(self, token)


class HistoricalFeed(_MultiTokenFeed):
    """Replays a price series from arrays."""

    def __init__(self, prices: dict[TokenId, np.ndarray]):
        self._prices = prices

    def _price_for(self, token: TokenId, slot: int) -> Numeric:
        arr = self._prices.get(token)
        if arr is None:
            return 0
        value = arr[-1] if slot >= len(arr) else arr[slot]
        if isinstance(value, np.generic):
            return value.item()
        return value

    @classmethod
    def from_csv(cls, path: str, token_columns: dict[TokenId, str],
                 scale: int) -> "HistoricalFeed":
        """Load from CSV."""
        import pandas as pd
        df = pd.read_csv(path)
        prices: dict[TokenId, np.ndarray] = {}
        for token_id, col_name in token_columns.items():
            prices[token_id] = (df[col_name].values * scale).astype(np.int64)
        return cls(prices)

    @classmethod
    def from_parquet(cls, path: str, token_columns: dict[TokenId, str],
                     scale: int) -> "HistoricalFeed":
        """Load from Parquet file."""
        import pandas as pd
        df = pd.read_parquet(path)
        prices: dict[TokenId, np.ndarray] = {}
        for token_id, col_name in token_columns.items():
            prices[token_id] = (df[col_name].values * scale).astype(np.int64)
        return cls(prices)


class StochasticFeed(_MultiTokenFeed):
    """Generates prices via configurable stochastic process."""

    def __init__(
        self,
        process: str,
        params: dict,
        rng: np.random.Generator | None = None,
        seed: int | None = None,
    ):
        self._process = process
        self._params = params
        self._rng = rng if rng is not None else np.random.default_rng(seed)
        self._cache: dict[TokenId, dict[int, Numeric]] = {}

    def set_rng(self, rng: np.random.Generator) -> None:
        """Attach an engine-managed RNG and clear cached paths."""
        self._rng = rng
        self._cache.clear()

    def _price_for(self, token: TokenId, slot: int) -> Numeric:
        if token not in self._cache:
            self._cache[token] = {}
        if slot not in self._cache[token]:
            self._cache[token][slot] = self._generate(token, slot)
        return self._cache[token][slot]

    def _generate(self, token: TokenId, slot: int) -> Numeric:
        scale = self._params.get("scale", 10**9)
        initial = self._params.get("initial", 1.0)
        dt = self._params.get("dt", 1.0)

        if slot == 0:
            price = float(initial)
        else:
            prev = self._price_for(token, slot - 1)
            price = prev / scale if isinstance(prev, int) else float(prev)

        if self._process == "gbm":
            mu = self._params.get("mu", 0.0)
            sigma = self._params.get("sigma", 0.01)
            z = self._rng.standard_normal()
            price = price * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z)
        elif self._process == "mean_reversion":
            theta = self._params.get("theta", float(initial))
            kappa = self._params.get("kappa", 0.1)
            sigma = self._params.get("sigma", 0.01)
            z = self._rng.standard_normal()
            price = price + kappa * (theta - price) * dt + sigma * np.sqrt(dt) * z
        elif self._process == "jump_diffusion":
            mu = self._params.get("mu", 0.0)
            sigma = self._params.get("sigma", 0.01)
            jump_intensity = self._params.get("jump_intensity", 0.1)
            jump_mean = self._params.get("jump_mean", 0.0)
            jump_std = self._params.get("jump_std", 0.02)
            z = self._rng.standard_normal()
            jumps = self._rng.poisson(jump_intensity * dt)
            jump_component = 0.0
            if jumps > 0:
                jump_component = float(
                    self._rng.normal(jump_mean, jump_std, size=jumps).sum()
                )
            price = price * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * z + jump_component)
        else:
            raise ValueError(f"Unsupported stochastic process: {self._process}")

        if scale == 1:
            return float(price)
        return int(price * scale)


class CompositeFeed(_MultiTokenFeed):
    """Combines multiple feeds for different tokens."""

    def __init__(self, feeds: dict[TokenId, _MultiTokenFeed]):
        self.feeds = feeds

    def _price_for(self, token: TokenId, slot: int) -> Numeric:
        feed = self.feeds.get(token)
        if feed is None:
            return 0
        return feed._price_for(token, slot)


def serialize_feed(feed: _MultiTokenFeed) -> dict[str, Any]:
    if isinstance(feed, HistoricalFeed):
        return {
            "type": "historical",
            "prices": {
                token_id: prices.tolist()
                for token_id, prices in feed._prices.items()
            },
        }
    if isinstance(feed, StochasticFeed):
        return {
            "type": "stochastic",
            "process": feed._process,
            "params": dict(feed._params),
            "rng_state": feed._rng.bit_generator.state,
            "cache": {
                token_id: dict(round_prices)
                for token_id, round_prices in feed._cache.items()
            },
        }
    if isinstance(feed, CompositeFeed):
        return {
            "type": "composite",
            "feeds": {
                token_id: serialize_feed(inner_feed)
                for token_id, inner_feed in feed.feeds.items()
            },
        }
    raise TypeError(f"feed {type(feed).__name__} is not snapshot-serializable")


def deserialize_feed(data: dict[str, Any]) -> _MultiTokenFeed:
    feed_type = data["type"]
    if feed_type == "historical":
        return HistoricalFeed({
            token_id: np.asarray(prices)
            for token_id, prices in data.get("prices", {}).items()
        })
    if feed_type == "stochastic":
        feed = StochasticFeed(
            process=data["process"],
            params=dict(data.get("params", {})),
        )
        feed._rng.bit_generator.state = data["rng_state"]
        feed._cache = {
            token_id: dict(round_prices)
            for token_id, round_prices in data.get("cache", {}).items()
        }
        return feed
    if feed_type == "composite":
        return CompositeFeed({
            token_id: deserialize_feed(inner_feed)
            for token_id, inner_feed in data.get("feeds", {}).items()
        })
    raise ValueError(f"unknown feed type: {feed_type}")
