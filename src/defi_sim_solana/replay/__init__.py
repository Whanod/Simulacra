"""Solana slot/account ingestion (PRD US-001).

Public surface (per PRD line 91-96):
    from .slot_client import get_slot, SlotSnapshot
    from .account_client import get_program_accounts_at_slot, AccountSnapshot
    from .corpus import load_corpus_fixture, corpus_root

Imports are resolved lazily via PEP 562 ``__getattr__`` so that each backing
module (slot_client, account_client, corpus) can land in its own follow-up
iteration without breaking ``import defi_sim_solana.replay``.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

__all__ = [
    "get_slot",
    "SlotSnapshot",
    "get_program_accounts_at_slot",
    "AccountSnapshot",
    "load_corpus_fixture",
    "corpus_root",
    "PythPriceUpdateHydrator",
    "PythPriceUpdate",
    "KaminoLendHydrator",
    "KaminoReserve",
    "KaminoObligation",
    "JupiterPerpsHydrator",
    "JupiterPerpsCustody",
    "JupiterPerpsPosition",
]

_NAME_TO_SUBMODULE: dict[str, str] = {
    "get_slot": "slot_client",
    "SlotSnapshot": "slot_client",
    "get_program_accounts_at_slot": "account_client",
    "AccountSnapshot": "account_client",
    "load_corpus_fixture": "corpus",
    "corpus_root": "corpus",
    "PythPriceUpdateHydrator": "pyth_hydrator",
    "PythPriceUpdate": "pyth_hydrator",
    "KaminoLendHydrator": "kamino_lend",
    "KaminoReserve": "kamino_lend",
    "KaminoObligation": "kamino_lend",
    "JupiterPerpsHydrator": "jupiter_perps",
    "JupiterPerpsCustody": "jupiter_perps",
    "JupiterPerpsPosition": "jupiter_perps",
}


def __getattr__(name: str) -> Any:
    submodule = _NAME_TO_SUBMODULE.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(f"{__name__}.{submodule}")
    value = getattr(module, name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))


if TYPE_CHECKING:
    from .account_client import (  # noqa: F401
        AccountSnapshot,
        get_program_accounts_at_slot,
    )
    from .corpus import (  # noqa: F401
        corpus_root,
        load_corpus_fixture,
    )
    from .slot_client import (  # noqa: F401
        SlotSnapshot,
        get_slot,
    )
    from .pyth_hydrator import (  # noqa: F401
        PythPriceUpdate,
        PythPriceUpdateHydrator,
    )
    from .kamino_lend import (  # noqa: F401
        KaminoLendHydrator,
        KaminoObligation,
        KaminoReserve,
    )
    from .jupiter_perps import (  # noqa: F401
        JupiterPerpsCustody,
        JupiterPerpsHydrator,
        JupiterPerpsPosition,
    )
