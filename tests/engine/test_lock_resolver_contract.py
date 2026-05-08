"""PRD US-003 step 3: per-market LockResolver contract.

These tests pin down the lock-resolution contract introduced ahead of
the parallel scheduler. They verify:
- ``LockResolver`` is a runtime-checkable Protocol.
- Built-in markets (``CfammMarket``, ``ClobMarket``) implement it.
- Lock content matches the PRD US-003 step 6 mapping (pool / LP /
  orderbook accounts).
- ``DropReason.MISSING_LOCK_RESOLVER`` is in the canonical vocabulary so
  the engine-side admission rejection path can use it once wired.
"""

from __future__ import annotations

from defi_sim.core.types import (
    LPAction,
    LPActionType,
    OrderAction,
    OrderSide,
    SingleAssetAction,
    Side,
    SwapAction,
    Token,
)
from defi_sim.engine.execution import KNOWN_DROP_REASONS, DropReason
from defi_sim.engine.scheduler import LockedAction, LockResolver
from defi_sim.markets.cfamm import CfammMarket
from defi_sim.markets.clob import ClobMarket


def _tokens() -> list[Token]:
    return [
        Token(id="SOL", symbol="SOL", decimals=9),
        Token(id="USDC", symbol="USDC", decimals=6),
    ]


def test_cfamm_market_implements_lock_resolver_protocol():
    market = CfammMarket(tokens=_tokens(), initial_liquidity=1_000_000)
    assert isinstance(market, LockResolver)


def test_clob_market_implements_lock_resolver_protocol():
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    assert isinstance(market, LockResolver)


def test_cfamm_swap_action_resolves_to_pool_write_lock():
    market = CfammMarket(tokens=_tokens(), initial_liquidity=1_000_000)
    locked = market.resolve_locks(
        SwapAction(agent_id="alice", token_in="SOL", token_out="USDC", amount_in=10)
    )
    assert isinstance(locked, LockedAction)
    assert locked.read_locks == frozenset()
    assert len(locked.write_locks) == 1
    pool_id = next(iter(locked.write_locks))
    assert pool_id.startswith("cfamm:") and pool_id.endswith(":pool")


def test_cfamm_lp_action_resolves_to_pool_and_position_write_locks():
    market = CfammMarket(tokens=_tokens(), initial_liquidity=1_000_000)
    action = LPAction(
        agent_id="alice",
        collateral="USDC",
        amount=1000,
        lp_type=LPActionType.DEPOSIT,
        position_id="pos-1",
    )
    locked = market.resolve_locks(action)
    assert locked.read_locks == frozenset()
    assert len(locked.write_locks) == 2
    assert any(a.endswith(":pool") for a in locked.write_locks)
    assert any(":lp:alice:pos-1" in a for a in locked.write_locks)


def test_cfamm_distinct_instances_have_distinct_pool_account_ids():
    a = CfammMarket(tokens=_tokens(), initial_liquidity=1_000_000)
    b = CfammMarket(tokens=_tokens(), initial_liquidity=1_000_000)
    pool_a = a._pool_account_id()
    pool_b = b._pool_account_id()
    assert pool_a != pool_b


def test_clob_order_action_resolves_to_orderbook_write_lock():
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    locked = market.resolve_locks(
        OrderAction(
            agent_id="alice",
            base="SOL",
            quote="USDC",
            side=OrderSide.BUY,
            price=10,
            quantity=1,
        )
    )
    assert locked.read_locks == frozenset()
    assert len(locked.write_locks) == 1
    book_id = next(iter(locked.write_locks))
    assert book_id.startswith("clob:") and ":book:SOL:USDC" in book_id


def test_clob_market_order_via_single_asset_resolves_to_book_lock():
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    locked = market.resolve_locks(
        SingleAssetAction(
            agent_id="alice", asset="SOL", collateral="USDC", amount=5, side=Side.BUY
        )
    )
    assert len(locked.write_locks) == 1


def test_missing_lock_resolver_drop_reason_in_known_vocabulary():
    assert DropReason.MISSING_LOCK_RESOLVER == "missing_lock_resolver"
    assert DropReason.MISSING_LOCK_RESOLVER in KNOWN_DROP_REASONS


def test_clob_order_with_oracle_account_ids_surfaces_to_read_locks():
    """PRD US-006 line 491: clob orders that consult an oracle add read_locks."""
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    locked = market.resolve_locks(
        OrderAction(
            agent_id="alice",
            base="SOL",
            quote="USDC",
            side=OrderSide.BUY,
            price=10,
            quantity=1,
            oracle_account_ids=frozenset({"pyth_pull_sol_usdc"}),
        )
    )
    assert locked.read_locks == frozenset({"pyth_pull_sol_usdc"})
    assert len(locked.write_locks) == 1


def test_clob_swap_with_oracle_account_ids_surfaces_to_read_locks():
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    locked = market.resolve_locks(
        SwapAction(
            agent_id="alice",
            token_in="SOL",
            token_out="USDC",
            amount_in=10,
            oracle_account_ids=frozenset({"pyth_lazer_sol_usdc"}),
        )
    )
    assert locked.read_locks == frozenset({"pyth_lazer_sol_usdc"})
    assert len(locked.write_locks) == 1


def test_clob_single_asset_with_multiple_oracle_account_ids_surfaces_to_read_locks():
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    oracles = frozenset({"pyth_pull_sol_usdc", "switchboard_on_demand_sol_usdc"})
    locked = market.resolve_locks(
        SingleAssetAction(
            agent_id="alice",
            asset="SOL",
            collateral="USDC",
            amount=5,
            side=Side.BUY,
            oracle_account_ids=oracles,
        )
    )
    assert locked.read_locks == oracles


def test_clob_oracle_read_lock_creates_scheduler_conflict_with_oracle_writer():
    """Two clob orders sharing an oracle read-lock do NOT conflict (read-read);
    a clob order's oracle read-lock DOES conflict with another action's
    write-lock on the same oracle account (e.g. an OracleUpdateAction).
    """
    from defi_sim.engine.scheduler import LockedAction, conflicts

    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    oracle_id = "pyth_pull_sol_usdc"

    reader_a = market.resolve_locks(
        OrderAction(
            agent_id="alice",
            base="SOL",
            quote="USDC",
            side=OrderSide.BUY,
            price=10,
            quantity=1,
            oracle_account_ids=frozenset({oracle_id}),
        )
    )
    reader_b = market.resolve_locks(
        OrderAction(
            agent_id="bob",
            base="SOL",
            quote="USDC",
            side=OrderSide.SELL,
            price=11,
            quantity=1,
            oracle_account_ids=frozenset({oracle_id}),
        )
    )
    writer = LockedAction(
        action=OrderAction(agent_id="updater", base="SOL", quote="USDC"),
        write_locks=frozenset({oracle_id}),
    )

    # Read-read on the same oracle does not conflict (the orderbook write
    # locks differ since both readers target different write_locks? Actually
    # both target the same book — they conflict on the book write, not the
    # oracle read. So this asserts the oracle read alone is not the cause.)
    # Strip the book write to isolate the oracle-read interaction:
    reader_a_oracle_only = LockedAction(
        action=reader_a.action, read_locks=reader_a.read_locks, write_locks=frozenset()
    )
    reader_b_oracle_only = LockedAction(
        action=reader_b.action, read_locks=reader_b.read_locks, write_locks=frozenset()
    )
    assert not conflicts(reader_a_oracle_only, reader_b_oracle_only)
    # A writer to the same oracle account conflicts with a clob reader.
    assert conflicts(reader_a, writer)


def test_clob_action_without_oracle_account_ids_keeps_empty_read_locks():
    """Default-empty oracle_account_ids preserves prior behavior (read_locks empty)."""
    tokens = _tokens()
    market = ClobMarket(pairs=[(tokens[0], tokens[1])])
    locked = market.resolve_locks(
        OrderAction(
            agent_id="alice",
            base="SOL",
            quote="USDC",
            side=OrderSide.BUY,
            price=10,
            quantity=1,
        )
    )
    assert locked.read_locks == frozenset()
