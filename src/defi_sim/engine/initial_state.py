"""``InitialState`` / ``InitialStateFragment`` value objects (PRD US-003 line 419).

Typed parser output produced by :class:`StateHydrator` implementations. Each
fragment carries enough metadata to route itself to the right runtime owner
(a ``Market``, the price-feed registry, or an agent) at materialization time.

``InitialState`` is **a value object** — the deterministic, cacheable parser
output keyed by ``(slot, fork_spec_hash, hydrator_versions)`` per PRD line 526.
It is *not* the engine's runtime state: ``Market`` instances and ``AgentState``
are constructed separately by ``materialize_fork`` (PRD line 543).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Union

from defi_sim.engine.state_hydrator import Pubkey

__all__ = ["FragmentKind", "InitialState", "InitialStateFragment"]


FragmentKind = Literal[
    "pool",
    "lending_reserve",
    "lending_position",
    "perp_market",
    "perp_position",
    "oracle_price",
    "wallet_balance",
    "wallet_position",
]


@dataclass(frozen=True)
class InitialStateFragment:
    """One parsed account, typed for materializer routing.

    ``kind`` selects the runtime destination: ``"pool"`` /
    ``"lending_reserve"`` / ``"perp_market"`` go to a ``Market``,
    ``"oracle_price"`` to the price-feed registry, ``"wallet_*"`` and
    per-user position fragments to the wallet identified by ``owner``.

    ``payload`` is numeric-mode-agnostic — the hydrator decides whether to
    emit ``Decimal``, ``int``, or other primitives. The materializer is
    responsible for any final coercion under the active ``NumericMode``.
    """

    kind: FragmentKind
    protocol_model: str
    pubkey: Pubkey
    owner: Pubkey | None
    payload: Mapping[str, Any]


@dataclass
class InitialState:
    """Deterministic, cacheable bundle of fragments produced for one ``ForkSpec``.

    Mutable so that :meth:`merge` can incorporate per-protocol parser output,
    oracle accounts, and wallet overlays in the order ``ForkLoader`` walks
    them. Once a load completes, callers should treat the instance as frozen
    in spirit (the cache key includes hydrator schema versions).
    """

    slot: int
    fragments: list[InitialStateFragment] = field(default_factory=list)

    def merge(
        self,
        other: Union[
            "InitialState", InitialStateFragment, Iterable[InitialStateFragment]
        ],
    ) -> None:
        """Append fragments from another ``InitialState``, a single fragment, or an iterable."""
        if isinstance(other, InitialStateFragment):
            self.fragments.append(other)
            return
        if isinstance(other, InitialState):
            self.fragments.extend(other.fragments)
            return
        self.fragments.extend(other)

    def by_protocol(self, protocol_model: str) -> list[InitialStateFragment]:
        """Return fragments whose ``protocol_model`` matches, preserving insertion order."""
        return [f for f in self.fragments if f.protocol_model == protocol_model]

    def by_kind(self, kind: FragmentKind) -> list[InitialStateFragment]:
        """Return fragments whose ``kind`` matches, preserving insertion order."""
        return [f for f in self.fragments if f.kind == kind]

    def protocols(self) -> list[str]:
        """Distinct ``protocol_model`` values, in first-seen (deterministic) order."""
        seen: dict[str, None] = {}
        for f in self.fragments:
            if f.protocol_model not in seen:
                seen[f.protocol_model] = None
        return list(seen)

    def to_json(self) -> str:
        """Serialize to a JSON string suitable for the parsed-state cache.

        ``payload`` mappings are passed through to ``json.dumps`` as-is, so
        hydrators that emit non-JSON-native types (``Decimal``, etc.) must
        coerce before producing fragments.
        """
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "InitialState":
        """Inverse of :meth:`to_json`. Reconstructs typed fragments."""
        data = json.loads(payload)
        fragments = [
            InitialStateFragment(
                kind=f["kind"],
                protocol_model=f["protocol_model"],
                pubkey=f["pubkey"],
                owner=f["owner"],
                payload=f["payload"],
            )
            for f in data["fragments"]
        ]
        return cls(slot=data["slot"], fragments=fragments)
