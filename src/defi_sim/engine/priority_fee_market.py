"""Per-account priority fee market (PRD US-010).

Maintains rolling per-``AccountId`` distributions of priority fees on
admitted write-locking actions so EV calculations and tip-sizing reflect
demand for hot accounts instead of constant-fee mocks.
"""

from __future__ import annotations

from collections import deque

from defi_sim.engine.scheduler import AccountId


class PriorityFeeMarket:
    """Per-account rolling distribution of compute-unit prices.

    Storage (PRD line 737): per-account ring-buffer of ``(slot, price)``
    tuples capped at ``window_slots`` (oldest observations drop out as the
    window slides). Aggregation combines rank-based percentile over the
    buffer (robust to single-point outliers) with an EWMA-smoothed baseline
    per account (uses ``ewma_half_life_slots``) for stability against
    transient fluctuations — the baseline is exposed to consumers that need
    a single scalar reference (e.g., change-detection in
    ``PriorityFeeMarketUpdatedEvent`` per PRD line 745).

    The floor protects against degenerate quotes for never-observed
    accounts (a brand-new pool would otherwise quote zero). The floor is
    an engine-level guard, not a Solana-runtime concept.
    """

    def __init__(
        self,
        window_slots: int = 150,
        ewma_half_life_slots: int = 30,
        floor_micro_lamports: int = 1,
        update_event_threshold: float = 0.05,
    ) -> None:
        self._window_slots = window_slots
        self._ewma_half_life_slots = ewma_half_life_slots
        self._floor_micro_lamports = floor_micro_lamports
        self._update_event_threshold = update_event_threshold
        self._observations: dict[AccountId, deque[tuple[int, int]]] = {}
        self._ewma_baseline: dict[AccountId, float] = {}
        self._ewma_alpha: float = 1.0 - 0.5 ** (1.0 / max(ewma_half_life_slots, 1))

    @property
    def update_event_threshold(self) -> float:
        """Relative-change threshold used by the engine when deciding whether
        to emit a ``PriorityFeeMarketUpdatedEvent`` for an account whose
        distribution has shifted (PRD US-010 line 745)."""
        return self._update_event_threshold

    def observe(
        self, account_id: AccountId, slot: int, price_micro_lamports: int
    ) -> None:
        obs = self._observations.get(account_id)
        if obs is None:
            obs = deque(maxlen=self._window_slots)
            self._observations[account_id] = obs
        obs.append((slot, price_micro_lamports))

        prior = self._ewma_baseline.get(account_id)
        if prior is None:
            self._ewma_baseline[account_id] = float(price_micro_lamports)
        else:
            self._ewma_baseline[account_id] = (
                self._ewma_alpha * float(price_micro_lamports)
                + (1.0 - self._ewma_alpha) * prior
            )

    def quote(self, account_id: AccountId, percentile: int) -> int:
        observations = self._observations.get(account_id)
        if not observations:
            return self._floor_micro_lamports
        prices = sorted(price for _slot, price in observations)
        return self._percentile_of_sorted(prices, percentile)

    def percentiles(self, account_id: AccountId) -> dict[int, int]:
        observations = self._observations.get(account_id)
        if not observations:
            return {p: self._floor_micro_lamports for p in (25, 50, 75, 90, 99)}
        prices = sorted(price for _slot, price in observations)
        return {p: self._percentile_of_sorted(prices, p) for p in (25, 50, 75, 90, 99)}

    def previous_percentiles(self, account_id: AccountId) -> dict[int, int] | None:
        """Return the current percentile distribution for ``account_id`` if any
        observations exist, else ``None`` — used by the engine's
        change-detection step (PRD US-010 line 745) to distinguish a
        first-time observation from a delta against a known prior."""
        if not self._observations.get(account_id):
            return None
        return self.percentiles(account_id)

    def smoothed_baseline(self, account_id: AccountId) -> int:
        """EWMA-smoothed baseline price for ``account_id`` clamped to floor.

        The baseline is updated by ``observe()`` using the half-life
        configured at construction; consumers that need a single stable
        reference (e.g., the change-detection step that decides whether
        to emit ``PriorityFeeMarketUpdatedEvent`` per PRD line 745) should
        prefer this over a single rank-percentile sample.
        """
        prior = self._ewma_baseline.get(account_id)
        if prior is None:
            return self._floor_micro_lamports
        return max(int(prior), self._floor_micro_lamports)

    def _percentile_of_sorted(self, prices: list[int], percentile: int) -> int:
        idx = max(0, min(len(prices) - 1, (percentile * (len(prices) - 1)) // 100))
        return max(prices[idx], self._floor_micro_lamports)
