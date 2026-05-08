"""Jito bundle auction execution mode (PRD US-011 line 832).

Per slot, bundles are grouped into local auctions by overlapping
write/read-lock conflict sets, ranked by ``tip_lamports / max(total_cu, 1)``
(with total tip as a deterministic tie-breaker), then selected greedily under
the slot's remaining CU budget. Selected bundles atomically reserve the union
of their write-locks; bundles whose required locks collide with an
already-selected bundle or the regular non-bundle action queue are dropped
with reason ``bundle_lock_conflict``.

This module owns the auction *mechanism* — admission, ranking, selection.
Atomic execution lives on ``SimulationEngine._execute_bundle_atomically`` (PRD
US-005 line 424) and is invoked by the bundle pre-stage in
``SolanaLikeExecution.execute_slot`` (PRD US-011 step 3, line 840). Revenue
routing is event-emission only in 1.7 (PRD line 787): the auction returns
selected bundles paired with the configured ``jito_stake_pool_share`` so the
pre-stage can emit ``BundleTipPaid`` events; no agent ledger is debited until
US-014 (validator economics) lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterable

from defi_sim.engine.bundle import (
    MAX_BUNDLE_TXS,
    MIN_BUNDLE_TIP_LAMPORTS,
    Bundle,
)
from defi_sim.engine.scheduler import AccountId

if TYPE_CHECKING:
    from defi_sim_solana.calibration import TipQuoteCurve

# Beta-prior strength for the calibrated/observed blend in :meth:`tip_quote`.
# The auction blends the calibrated tip-percentile prior with in-process
# observations using
#
#     w_calibrated = max(0, k - n_observed) / k
#     w_observed   = 1 - w_calibrated
#
# so a fresh run quotes 100% calibrated, and after ~``k`` observations the
# prior decays to zero and the in-process distribution dominates. ``k=200``
# is a deliberate choice: the lighthouse template runs ~500 slots and
# spawns 1-3 sandwich bundle attempts per active slot, so the prior fades
# cleanly across a single run rather than dominating the whole horizon.
DEFAULT_TIP_QUOTE_CALIBRATION_K: int = 200


# PRD US-011 line 890: ``BundleAuctionSpec.tip_account_set`` defaults to the
# 8 well-known Jito tip-account pubkeys. Real validators send tips to these
# accounts; calibration scenarios can override the set for testing
# (e.g. shrinking to one address to simplify ledger assertions).
DEFAULT_JITO_TIP_ACCOUNTS: tuple[str, ...] = (
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5",
    "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY",
    "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh",
    "ADuUkR4vqLUMWXxW9gh6D6L8pivKeVBBjNB4nKsYKCfk",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL",
    "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
)


# Bundle-specific drop-reason vocabulary. Strings are surfaced into
# ``SlotOutcome.dropped`` and downstream telemetry alongside the
# ``DropReason`` constants from ``execution.py``.
class BundleDropReason:
    BUNDLE_TOO_LARGE = "bundle_too_large"
    BUNDLE_TIP_BELOW_MINIMUM = "bundle_tip_below_minimum"
    BUNDLE_LOCK_CONFLICT = "bundle_lock_conflict"
    BUNDLE_SLOT_CU_EXCEEDED = "bundle_slot_cu_exceeded"


KNOWN_BUNDLE_DROP_REASONS: frozenset[str] = frozenset(
    {
        BundleDropReason.BUNDLE_TOO_LARGE,
        BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM,
        BundleDropReason.BUNDLE_LOCK_CONFLICT,
        BundleDropReason.BUNDLE_SLOT_CU_EXCEEDED,
    }
)


@dataclass(frozen=True)
class BundleCandidate:
    """A bundle paired with its resolved write/read account locks.

    The auction works on these (rather than raw ``Bundle``s) so lock
    resolution stays a callers concern — typically performed by the slot
    pre-stage via the same per-market ``LockResolver`` the regular scheduler
    uses (PRD US-003 step 3).
    """

    bundle: Bundle
    write_locks: frozenset[AccountId] = field(default_factory=frozenset)
    read_locks: frozenset[AccountId] = field(default_factory=frozenset)
    # Stable submission order, used as the final deterministic tie-breaker
    # so two bundles with identical (efficiency, total_tip) still rank
    # reproducibly across runs.
    submitted_index: int = 0
    # PRD US-013: per-candidate exemption from the non-bundle conflict check.
    # When the bundle declares ``coexisting_actions``, the lock-resolver
    # populates these with the victim's resolved locks so the auction does
    # not treat the bundle's own back-run-target as a hostile conflict.
    coexisting_write_locks: frozenset[AccountId] = field(default_factory=frozenset)
    coexisting_read_locks: frozenset[AccountId] = field(default_factory=frozenset)

    @property
    def total_cu(self) -> int:
        return self.bundle.total_cu

    @property
    def tip_lamports(self) -> int:
        return self.bundle.tip_lamports

    @property
    def tip_per_cu(self) -> float:
        cu = max(self.total_cu, 1)
        return self.tip_lamports / cu


@dataclass(frozen=True)
class BundleAuctionResult:
    """Outcome of one slot's bundle auction.

    ``selected`` is in execution order (highest-efficiency first). Each
    dropped entry records the ``Bundle`` and a string drop reason from
    ``BundleDropReason``.
    """

    selected: list[BundleCandidate] = field(default_factory=list)
    dropped: list[tuple[Bundle, str]] = field(default_factory=list)


class BundleAuction:
    """Per-slot bundle admission + selection.

    Construction parameters mirror the configurable knobs called out in the
    PRD (line 833 admission limits; line 890 ``BundleAuctionSpec``):

    * ``max_bundle_txs`` / ``min_bundle_tip_lamports`` override the
      Jito-mainnet defaults so calibration scenarios can stress
      bundle-shape limits.
    * ``max_bundles_per_slot`` caps the picked-set cardinality so the
      validator can keep the slot below its CU budget under any tip
      distribution.
    * ``jito_stake_pool_share`` is recorded on the auction (default 0.05 =
      5%) so the bundle pre-stage can emit it on ``BundleTipPaid`` for the
      ledger replay in US-014 (validator economics) without a schema
      migration.
    """

    def __init__(
        self,
        *,
        max_bundle_txs: int = MAX_BUNDLE_TXS,
        min_bundle_tip_lamports: int = MIN_BUNDLE_TIP_LAMPORTS,
        max_bundles_per_slot: int = 5,
        jito_stake_pool_share: float = 0.05,
        tip_account_set: Iterable[str] = DEFAULT_JITO_TIP_ACCOUNTS,
        tip_quote_curve: "TipQuoteCurve | None" = None,
        tip_quote_calibration_k: int = DEFAULT_TIP_QUOTE_CALIBRATION_K,
    ) -> None:
        if max_bundle_txs <= 0:
            raise ValueError("max_bundle_txs must be positive")
        if min_bundle_tip_lamports < 0:
            raise ValueError("min_bundle_tip_lamports must be non-negative")
        if max_bundles_per_slot <= 0:
            raise ValueError("max_bundles_per_slot must be positive")
        if not 0.0 <= jito_stake_pool_share <= 1.0:
            raise ValueError("jito_stake_pool_share must be in [0, 1]")
        if tip_quote_calibration_k <= 0:
            raise ValueError("tip_quote_calibration_k must be positive")
        tip_accounts = tuple(str(a) for a in tip_account_set)
        if not tip_accounts:
            raise ValueError("tip_account_set must contain at least one address")
        self.max_bundle_txs = int(max_bundle_txs)
        self.min_bundle_tip_lamports = int(min_bundle_tip_lamports)
        self.max_bundles_per_slot = int(max_bundles_per_slot)
        self.jito_stake_pool_share = float(jito_stake_pool_share)
        self.tip_account_set: tuple[str, ...] = tip_accounts
        self.tip_quote_curve: "TipQuoteCurve | None" = tip_quote_curve
        self.tip_quote_calibration_k: int = int(tip_quote_calibration_k)
        self._tip_observations: dict[tuple[AccountId, ...], list[int]] = {}

    def observe_tip(self, lock_set: Iterable[AccountId], tip_lamports: int) -> None:
        """Record an observed winning/competing tip for a local lock cohort.

        The auction already groups bundles by overlapping locks for selection;
        the tip optimizer uses the same cohort concept to quote the tip needed
        to beat a target percentile of competing bundles.
        """
        if tip_lamports < 0:
            raise ValueError("tip_lamports must be non-negative")
        self._tip_observations.setdefault(self._lock_cohort_key(lock_set), []).append(
            int(tip_lamports)
        )

    def tip_quote(self, lock_set: Iterable[AccountId], percentile: int) -> int:
        """Return the calibrated/observed tip percentile for a local lock cohort.

        When a :class:`TipQuoteCurve` prior is configured (via
        ``tip_quote_curve=`` on construction), this returns a Beta-weighted
        blend of the calibrated baseline and in-process observations:

            w_calibrated = max(0, k - n_observed) / k       # decays from 1 → 0
            w_observed   = 1 - w_calibrated
            quote        = round(w_calibrated * curve.percentile(p, cohort)
                                 + w_observed * observed_percentile)

        with ``k = self.tip_quote_calibration_k`` (default 200). This keeps a
        fresh run from quoting the floor until enough in-process tips have
        accrued, while letting the in-cohort distribution take over as
        observations accumulate.

        Without a curve configured, behavior is unchanged from the
        pre-calibration path: empty cohorts fall back to
        ``min_bundle_tip_lamports``; non-empty cohorts return the nearest-rank
        empirical percentile of in-process observations. Either way the
        result is clamped to ``min_bundle_tip_lamports`` so callers never
        recommend a below-floor tip.
        """
        if not 1 <= percentile <= 99:
            raise ValueError("percentile must be in [1, 99]")
        cohort_key = self._lock_cohort_key(lock_set)
        observations = self._tip_observations.get(cohort_key, [])
        observed_pct = self._observed_percentile(observations, percentile)

        curve = self.tip_quote_curve
        if curve is None:
            if observed_pct is None:
                return self.min_bundle_tip_lamports
            return max(observed_pct, self.min_bundle_tip_lamports)

        calibrated_pct = curve.percentile(percentile, cohort_key)
        n = len(observations)
        k = self.tip_quote_calibration_k
        w_cal = max(0.0, (k - n) / k)
        w_obs = 1.0 - w_cal
        if observed_pct is None:
            blended = calibrated_pct  # n == 0 implies w_obs == 0 anyway
        else:
            blended = int(round(w_cal * calibrated_pct + w_obs * observed_pct))
        return max(blended, self.min_bundle_tip_lamports)

    @staticmethod
    def _observed_percentile(
        observations: Iterable[int], percentile: int
    ) -> int | None:
        tips = sorted(int(t) for t in observations)
        if not tips:
            return None
        idx = max(0, min(len(tips) - 1, (percentile * (len(tips) - 1)) // 100))
        return tips[idx]

    def is_tip_quote_calibrated(self) -> bool:
        """True when a :class:`TipQuoteCurve` prior is configured."""
        return self.tip_quote_curve is not None

    def clear_tip_observations(self) -> None:
        self._tip_observations.clear()

    def _lock_cohort_key(
        self, lock_set: Iterable[AccountId]
    ) -> tuple[AccountId, ...]:
        return tuple(sorted(str(account) for account in lock_set))

    def admit(
        self, bundles: Iterable[Bundle]
    ) -> tuple[list[Bundle], list[tuple[Bundle, str]]]:
        """Apply the Jito invariants: max-tx-count and min-tip thresholds.

        Bundles already enforce these in ``Bundle.__post_init__``, but the
        auction itself rechecks against the per-instance overrides so a
        calibration scenario configured with a tighter ``max_bundle_txs``
        actually rejects a bundle that the dataclass would have accepted.
        """
        admitted: list[Bundle] = []
        dropped: list[tuple[Bundle, str]] = []
        for bundle in bundles:
            if len(bundle.txs) > self.max_bundle_txs:
                dropped.append((bundle, BundleDropReason.BUNDLE_TOO_LARGE))
                continue
            if bundle.tip_lamports < self.min_bundle_tip_lamports:
                dropped.append(
                    (bundle, BundleDropReason.BUNDLE_TIP_BELOW_MINIMUM)
                )
                continue
            admitted.append(bundle)
        return admitted, dropped

    def select_top_k(
        self,
        candidates: Iterable[BundleCandidate],
        *,
        remaining_slot_cu: int,
        non_bundle_pending_writes: frozenset[AccountId] | set[AccountId] = frozenset(),
        non_bundle_pending_reads: frozenset[AccountId] | set[AccountId] = frozenset(),
    ) -> BundleAuctionResult:
        """Greedy efficiency-first selection under slot CU + lock constraints.

        Ranking key: (``tip_per_cu`` desc, ``tip_lamports`` desc,
        ``submitted_index`` asc) so identical-efficiency bundles fall back
        to total tip and then to submission order. Conflicts:

        * A bundle whose write-set overlaps any already-selected bundle's
          write or read set, OR whose read-set overlaps an already-selected
          bundle's write set, is dropped with ``bundle_lock_conflict``.
        * A bundle whose write-set overlaps the regular action queue's
          pending writes (or whose read-set overlaps those writes) is
          likewise dropped with ``bundle_lock_conflict`` so the bundle
          doesn't race the scheduler over the same account.
        * A bundle whose ``total_cu`` exceeds remaining slot CU is dropped
          with ``bundle_slot_cu_exceeded``. Selection continues — a smaller
          bundle may still fit.
        """
        ranked = sorted(
            candidates,
            key=lambda c: (-c.tip_per_cu, -c.tip_lamports, c.submitted_index),
        )

        non_bundle_writes = frozenset(non_bundle_pending_writes)
        non_bundle_reads = frozenset(non_bundle_pending_reads)
        reserved_writes: set[AccountId] = set()
        reserved_reads: set[AccountId] = set()
        cu_used = 0
        selected: list[BundleCandidate] = []
        dropped: list[tuple[Bundle, str]] = []

        for candidate in ranked:
            # PRD US-013: a searcher's bundle may declare ``coexisting_actions``
            # (e.g., a back-run riding alongside its victim). The locks those
            # actions hold are subtracted from the non-bundle conflict set
            # for THIS candidate so the bundle isn't dropped for racing the
            # very victim it's targeting. Bundles without coexistence flags
            # see the unchanged conflict set.
            effective_nb_writes = non_bundle_writes - candidate.coexisting_write_locks
            effective_nb_reads = non_bundle_reads - candidate.coexisting_read_locks
            if candidate.write_locks & effective_nb_writes:
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
                )
                continue
            if candidate.write_locks & effective_nb_reads:
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
                )
                continue
            if candidate.read_locks & effective_nb_writes:
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
                )
                continue
            if candidate.write_locks & (reserved_writes | reserved_reads):
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
                )
                continue
            if candidate.read_locks & reserved_writes:
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_LOCK_CONFLICT)
                )
                continue
            if cu_used + candidate.total_cu > remaining_slot_cu:
                dropped.append(
                    (candidate.bundle, BundleDropReason.BUNDLE_SLOT_CU_EXCEEDED)
                )
                continue
            reserved_writes |= candidate.write_locks
            reserved_reads |= candidate.read_locks
            cu_used += candidate.total_cu
            selected.append(candidate)
            if len(selected) >= self.max_bundles_per_slot:
                break

        return BundleAuctionResult(selected=selected, dropped=dropped)
