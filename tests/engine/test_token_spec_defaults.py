"""Token-default compatibility tests for the Solana pivot (PRD 0.1.6).

These tests pin behaviour the pivot must NOT change: legacy / chain-neutral
artifacts that omit ``decimals`` still deserialize as 18-decimal tokens, and
the runtime ``COLLATERAL`` token stays at 9 decimals. Solana-specific
defaults are made explicit at creation time via
:func:`default_tokens_for_execution`.
"""

from __future__ import annotations

from defi_sim.core.types import COLLATERAL, Token
from defi_sim.engine.specs import TokenSpec, default_tokens_for_execution


def test_neutral_token_spec_default_decimals_remains_18() -> None:
    spec = TokenSpec(id="X", symbol="X")
    assert spec.decimals == 18


def test_token_spec_explicit_18_round_trips() -> None:
    spec = TokenSpec(id="X", symbol="X", decimals=18)
    payload = {"id": spec.id, "symbol": spec.symbol, "decimals": spec.decimals}
    restored = TokenSpec.from_dict(payload)
    assert restored == spec
    assert restored.decimals == 18


def test_token_spec_legacy_payload_without_decimals_loads_as_18() -> None:
    restored = TokenSpec.from_dict({"id": "X", "symbol": "X"})
    assert restored.decimals == 18


def test_collateral_token_unchanged() -> None:
    assert COLLATERAL.decimals == 9


def test_runtime_token_default_decimals_remains_18() -> None:
    assert Token(id="X", symbol="X").decimals == 18


def test_default_tokens_for_solana_like_returns_sol_and_usdc() -> None:
    tokens = default_tokens_for_execution("solana_like")
    by_symbol = {t.symbol: t for t in tokens}
    assert "SOL" in by_symbol and by_symbol["SOL"].decimals == 9
    assert "USDC" in by_symbol and by_symbol["USDC"].decimals == 6


def test_default_tokens_for_unknown_execution_is_empty() -> None:
    assert default_tokens_for_execution(None) == []
    assert default_tokens_for_execution("ethereum_like") == []
    assert default_tokens_for_execution("") == []


def test_token_spec_default_decimals_remains_18_after_phase_1_9() -> None:
    """PRD US-007 line 553: locks the decimals default at 18.

    The Phase 1.9 ``TokenSpec`` extension adds ``standard`` /
    ``exchange_rate_to_sol`` / ``exchange_rate_drift`` / ``transfer_hook``
    fields but must NOT flip the global ``decimals`` default. Solana-shaped
    callers (``default_tokens_for_execution("solana_like")``) write
    ``SOL=9``/``USDC=6`` explicitly; legacy / chain-neutral artifacts that
    omit ``decimals`` still load as 18-decimal tokens.
    """

    spec = TokenSpec(id="X", symbol="X")
    assert spec.decimals == 18, (
        "Phase 1.9 must NOT change TokenSpec.decimals default; "
        "see phase-0.md:62 for the artifact-migration constraint."
    )
    assert spec.standard == "spl"
    assert spec.exchange_rate_to_sol is None
    assert spec.exchange_rate_drift is None
    assert spec.transfer_hook is None

    legacy = TokenSpec.from_dict({"id": "X", "symbol": "X"})
    assert legacy.decimals == 18
    assert legacy.standard == "spl"
    assert legacy.exchange_rate_to_sol is None
