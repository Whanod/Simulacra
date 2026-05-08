"""``ForkLoader`` — declarative on-chain state hydration (PRD US-003 line 483).

Walks a :class:`ForkSpec` against the historical-account ingestion path from
PRD US-001 (``defi_sim_solana.replay.get_program_accounts_at_slot``) and the
per-protocol :class:`StateHydrator` parsers from PRD line 398, producing a
deterministic :class:`InitialState` value object that
``materialize_fork`` (PRD line 543) later turns into a runtime ``World``.

Provider selection is *explicit*: the loader takes ``historical_backend`` and
``corpus_loader`` kwargs and threads them into the wrapper. Tests inject fakes;
the entry-gate harness picks Triton/Helius/etc. by binding the appropriate
backend at construction time. **No global module state.**

The wrapper itself is LRU-cached, so calling ``load(spec)`` twice in one
session for the same slot reuses the raw RPC payloads without a heavyweight
cache class. The *parsed* ``InitialState`` cache (PRD line 526) sits on top
of this and is owned by ``materialize_fork``.

``ProtocolModelRegistry`` lives here as a tiny stub for now: it carries the
``protocol_model`` -> ``ForkableMarket``-class lookup used by the loader. The
PRD doesn't yet specify a registration surface beyond ``.lookup(name)``; when
Phase 3 protocols start landing, the registry will gain a ``.register()``
method and likely move to its own module.

Explicit non-goals (PRD US-003 line 650)
----------------------------------------
The fork loader and the materializer are deliberately scoped narrowly. The
following are **not** in scope and any future change that drifts toward them
should be pushed back:

* **No sysvar replication.** ``Clock``, ``EpochSchedule``, ``Rent`` and the
  rest of the sysvars are not hydrated — Phase 3 protocol models do not
  consume them and replicating them would force a runtime emulator.
* **No unrelated programs.** Only programs named in the
  :class:`ForkSpec.protocols` list (and their declared oracle dependencies)
  are pulled. The loader never speculatively touches other programs.
* **No full-account-index walk.** Hydration is pinned to per-protocol
  ``account_filter`` + optional ``pubkey_allowlist`` lookups. A "give me every
  account at slot N" affordance is explicitly out of scope.
* **No ledger replay.** Forks materialize a *state* at slot N; they do not
  re-execute the ledger forward to that slot. Replay-from-actions is the
  separate ``ReplayExecution`` mode (PRD US-002), not this loader.

The shared rule: hydrate exactly enough state to make the modeled protocols'
math correct, and nothing more.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from defi_sim.engine.fork import ForkSpec
from defi_sim.engine.fork_cache import InitialStateCache, cache_key
from defi_sim.engine.initial_state import InitialState, InitialStateFragment
from defi_sim.engine.state_hydrator import OracleId, Pubkey

if TYPE_CHECKING:
    from defi_sim.engine.forkable import ForkableMarket
    from defi_sim_solana.replay.account_client import (
        AccountSnapshot,
        CorpusLoader,
        HistoricalAccountBackend,
    )

__all__ = ["ForkLoader", "ProtocolModelRegistry"]


@dataclass
class ProtocolModelRegistry:
    """Lookup table from ``protocol_model`` string to ``ForkableMarket`` class.

    Minimal surface: ``register(name, model_cls)`` and ``lookup(name)``.
    The registry is intentionally a simple value object — no entry-point
    discovery, no module-level singleton. Tests construct one and pass it to
    the loader; the production wiring point will be the bootstrap layer that
    instantiates a single registry from a config file when Phase 3 protocols
    land.
    """

    models: dict[str, type["ForkableMarket"]]

    def __init__(
        self, models: dict[str, type["ForkableMarket"]] | None = None
    ) -> None:
        self.models = dict(models or {})

    def register(self, name: str, model_cls: type["ForkableMarket"]) -> None:
        self.models[name] = model_cls

    def lookup(self, name: str) -> type["ForkableMarket"]:
        try:
            return self.models[name]
        except KeyError as exc:
            raise LookupError(
                f"no ForkableMarket registered for protocol_model {name!r}; "
                f"known models: {sorted(self.models)}"
            ) from exc


class ForkLoader:
    """Hydrate an :class:`InitialState` from on-chain accounts at ``ForkSpec.slot``.

    Provider selection is explicit and per-instance. The default behavior is
    to consult the corpus first (via the wrapper's ``corpus_loader``) and fall
    back to the injected ``historical_backend``; both knobs are forwarded
    untouched into ``get_program_accounts_at_slot``.
    """

    def __init__(
        self,
        registry: ProtocolModelRegistry,
        *,
        historical_backend: "HistoricalAccountBackend | None" = None,
        corpus_loader: "CorpusLoader | None" = None,
        initial_state_cache: InitialStateCache | None = None,
    ) -> None:
        self.registry = registry
        self.historical_backend = historical_backend
        self.corpus_loader = corpus_loader
        self.initial_state_cache = (
            initial_state_cache
            if initial_state_cache is not None
            else InitialStateCache()
        )

    def load(self, fork_spec: ForkSpec) -> InitialState:
        """Walk ``fork_spec`` and return a cached parsed :class:`InitialState`."""
        key = cache_key(fork_spec, self.registry)
        cached = self.initial_state_cache.get(key)
        if cached is not None:
            return cached
        initial = self._load_uncached(fork_spec)
        self.initial_state_cache.put(key, initial)
        return initial

    def _load_uncached(self, fork_spec: ForkSpec) -> InitialState:
        """Walk ``fork_spec`` and return a freshly merged :class:`InitialState`."""
        from defi_sim_solana.replay.account_client import (
            get_program_accounts_at_slot,
        )

        initial = InitialState(slot=fork_spec.slot)
        for req in fork_spec.protocols:
            model_cls = self.registry.lookup(req.protocol_model)
            hydrator = model_cls.state_hydrator
            filters = hydrator.account_filters()
            for account_filter in filters or [None]:
                discriminator = (
                    account_filter.discriminator
                    if account_filter is not None
                    else None
                )
                snap = get_program_accounts_at_slot(
                    hydrator.program_id,
                    fork_spec.slot,
                    backend=self.historical_backend,
                    corpus_loader=self.corpus_loader,
                    discriminator=discriminator,
                )
                allowlist = self._merged_allowlist(
                    req.account_pubkey_allowlist,
                    account_filter.pubkey_allowlist
                    if account_filter is not None
                    else None,
                )
                for record in self._select_accounts(snap, allowlist):
                    initial.merge(
                        hydrator.parse_account(record.pubkey, record.account_data)
                    )
            for oracle_id in hydrator.oracle_dependencies():
                initial.merge(self._load_oracle(oracle_id, fork_spec.slot))
        if fork_spec.include_wallet_accounts:
            initial.merge(
                self._load_wallet_accounts(
                    fork_spec.include_wallet_accounts, fork_spec.slot
                )
            )
        return initial

    @staticmethod
    def _select_accounts(
        snap: "AccountSnapshot",
        allowlist: list[Pubkey] | None,
    ):
        if allowlist is None:
            return snap.accounts
        wanted = set(allowlist)
        return tuple(r for r in snap.accounts if r.pubkey in wanted)

    @staticmethod
    def _merged_allowlist(
        request_allowlist: list[Pubkey] | None,
        filter_allowlist: tuple[Pubkey, ...] | None,
    ) -> list[Pubkey] | None:
        if request_allowlist is None:
            return list(filter_allowlist) if filter_allowlist is not None else None
        if filter_allowlist is None:
            return request_allowlist
        filtered = set(filter_allowlist)
        return [pubkey for pubkey in request_allowlist if pubkey in filtered]

    def _load_oracle(
        self, oracle_id: OracleId, slot: int
    ) -> list[InitialStateFragment]:
        """Load one oracle account into ``oracle_price`` fragments.

        Fixture parsers can decode committed oracle account bytes, but
        production old-slot oracle hydration still needs a configured exact
        as-of-slot account-state source. Until that source is wired here,
        declaring an ``oracle_dependencies()`` non-empty list raises a clear
        error instead of silently returning empty fragments — masking missing
        oracle state would corrupt downstream calibration.
        """
        raise NotImplementedError(
            f"oracle hydration requires exact as-of-slot account state "
            f"(requested {oracle_id!r} at slot {slot}); parse committed oracle "
            f"fixtures with their hydrator until a historical oracle account "
            f"source is configured."
        )

    def _load_wallet_accounts(
        self, pubkeys: list[Pubkey], slot: int
    ) -> list[InitialStateFragment]:
        """Load wallet-overlay accounts into ``wallet_*`` fragments.

        Phase 2.3a ships the dispatch surface only — wallet decoding (SPL
        token accounts, ATA balances, per-protocol position accounts) lands
        with the first ``SeedableAgent`` adopter. Asking for a wallet
        overlay before the decoder exists raises a clear error.
        """
        raise NotImplementedError(
            f"wallet-account hydration not yet implemented (requested "
            f"{len(pubkeys)} accounts at slot {slot}); land the SPL/position "
            f"decoders before passing include_wallet_accounts."
        )
