"""``ForkSpec`` / ``ProtocolForkRequest`` value-object contract (PRD US-003 line 468).

The state-fork ``ForkSpec`` is a declarative request consumed by ``ForkLoader``
(PRD line 483). It must coexist in ``defi_sim.engine.fork`` alongside the
``ChainReorgForkSpec`` introduced for US-014; both are dataclasses but model
unrelated concepts. These tests pin the field surface and the disambiguation.
"""

from __future__ import annotations

from dataclasses import is_dataclass

from defi_sim.engine.fork import ChainReorgForkSpec, ForkSpec, ProtocolForkRequest


def test_fork_spec_is_a_dataclass_with_required_slot() -> None:
    spec = ForkSpec(slot=420_196_842)
    assert is_dataclass(spec)
    assert spec.slot == 420_196_842
    assert spec.protocols == []
    assert spec.include_wallet_accounts is None


def test_fork_spec_accepts_protocols_and_wallet_overlay() -> None:
    spec = ForkSpec(
        slot=420_196_842,
        protocols=[
            ProtocolForkRequest(protocol_model="whirlpool"),
            ProtocolForkRequest(
                protocol_model="marginfi",
                account_pubkey_allowlist=["AcCt1", "AcCt2"],
            ),
        ],
        include_wallet_accounts=["WaLLet1"],
    )
    assert [r.protocol_model for r in spec.protocols] == ["whirlpool", "marginfi"]
    assert spec.protocols[0].account_pubkey_allowlist is None
    assert spec.protocols[1].account_pubkey_allowlist == ["AcCt1", "AcCt2"]
    assert spec.include_wallet_accounts == ["WaLLet1"]


def test_protocol_fork_request_is_a_dataclass_with_optional_allowlist() -> None:
    req = ProtocolForkRequest(protocol_model="whirlpool")
    assert is_dataclass(req)
    assert req.protocol_model == "whirlpool"
    assert req.account_pubkey_allowlist is None


def test_state_fork_and_chain_reorg_specs_are_distinct_types() -> None:
    state_fork = ForkSpec(slot=420_196_842)
    chain_reorg = ChainReorgForkSpec(fork_probability_per_slot=0.0)
    assert not isinstance(state_fork, ChainReorgForkSpec)
    assert not isinstance(chain_reorg, ForkSpec)
