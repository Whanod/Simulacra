"""Tests for the ``StateHydrator`` ABC framework (PRD US-003 line 398)."""

from __future__ import annotations

import pytest

from defi_sim.engine.state_hydrator import AccountFilter, StateHydrator


class _FakeHydrator(StateHydrator):
    program_id = "FakeProgram1111111111111111111111111111111"
    schema_version = 1

    def account_filters(self) -> list[AccountFilter]:
        return [AccountFilter(discriminator=b"\x00" * 8)]

    def parse_account(self, pubkey, data):  # type: ignore[override]
        return {"kind": "pool", "pubkey": pubkey, "len": len(data)}


def test_state_hydrator_cannot_be_instantiated_without_abstract_methods() -> None:
    with pytest.raises(TypeError):
        StateHydrator()  # type: ignore[abstract]


def test_state_hydrator_subclass_satisfies_contract() -> None:
    h = _FakeHydrator()
    assert h.program_id == "FakeProgram1111111111111111111111111111111"
    assert h.schema_version == 1
    filters = h.account_filters()
    assert len(filters) == 1
    assert filters[0].discriminator == b"\x00" * 8
    assert filters[0].pubkey_allowlist is None
    fragment = h.parse_account("PoolA", b"abcd")
    assert fragment == {"kind": "pool", "pubkey": "PoolA", "len": 4}


def test_state_hydrator_oracle_dependencies_default_is_empty() -> None:
    assert _FakeHydrator().oracle_dependencies() == []


def test_subclass_missing_parse_account_cannot_instantiate() -> None:
    class _Partial(StateHydrator):
        program_id = "X"
        schema_version = 1

        def account_filters(self):
            return []

    with pytest.raises(TypeError):
        _Partial()  # type: ignore[abstract]
