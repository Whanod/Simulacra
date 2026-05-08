"""CLOB Market — Central Limit Order Book.

Wraps the Orderbook module. Implements Market + PricedMarket.
Does NOT implement LiquidityPool.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any, ClassVar

from defi_sim._compat import msgpack
from defi_sim.core.market import (
    Market,
    PricedMarket,
    deserialize_callable_ref,
    register_market_type,
    serialize_callable_ref,
)
from defi_sim.core.types import (
    Action,
    ClobSnapshot,
    decode_msgpack_value,
    encode_msgpack_value,
    ExecutionContext,
    ExecutionResult,
    Numeric,
    OrderAction,
    OrderSide,
    Side,
    SingleAssetAction,
    SwapAction,
    Token,
    TokenId,
)
from defi_sim.engine.scheduler import LockedAction
from defi_sim.orderbook.orderbook import OBSide, Order, Orderbook


@register_market_type
class ClobMarket(Market, PricedMarket):
    """Central limit order book market wrapping the Orderbook module."""

    market_type: ClassVar[str] = "clob"

    def __init__(
        self,
        pairs: list[tuple[Token, Token]],
        fee_model: Any = None,
    ):
        self.fee_model = fee_model
        self._pairs = pairs
        self._books: dict[tuple[TokenId, TokenId], Orderbook] = {}
        self._all_tokens: set[TokenId] = set()

        for base, quote in pairs:
            key = (base.id, quote.id)
            self._books[key] = Orderbook(base.id, quote.id)
            self._all_tokens.add(base.id)
            self._all_tokens.add(quote.id)

    def get_state(self) -> ClobSnapshot:
        best_bid: dict[TokenId, Numeric | None] = {}
        best_ask: dict[TokenId, Numeric | None] = {}
        spread: dict[TokenId, Numeric] = {}
        total_depth: dict[TokenId, Numeric] = {}
        base_counts = Counter(base for base, _quote in self._books.keys())

        for (base, quote), book in self._books.items():
            key = self._book_state_key(base, quote, base_counts)
            bid = book.best_bid()
            ask = book.best_ask()
            best_bid[key] = bid
            best_ask[key] = ask
            s = book.spread()
            spread[key] = s if s is not None else 0
            total_depth[key] = book.total_depth(OBSide.BUY) + book.total_depth(OBSide.SELL)

        return ClobSnapshot(
            num_assets=len(self._all_tokens),
            tokens=sorted(self._all_tokens),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            total_depth=total_depth,
        )

    def execute(self, action: Action, ctx: ExecutionContext) -> ExecutionResult:
        if isinstance(action, OrderAction):
            return self._execute_order(action, ctx)
        elif isinstance(action, SingleAssetAction):
            return self._execute_market_order(action, ctx)
        elif isinstance(action, SwapAction):
            return self._execute_swap(action, ctx)
        return ExecutionResult(success=False, error=f"Unsupported action type: {type(action).__name__}")

    def _orderbook_account_id(self, base: TokenId, quote: TokenId) -> str:
        """Synthetic Solana-style orderbook account id keyed on the pair."""
        return f"clob:{id(self):x}:book:{base}:{quote}"

    def resolve_locks(self, action: Action, state: Any = None) -> LockedAction:
        """Map a CLOB-routed action to its read/write account locks.

        - ``OrderAction`` (limit): write the orderbook account for the
          ``(base, quote)`` pair.
        - ``SingleAssetAction`` / ``SwapAction`` (market orders against
          the book): write the orderbook account for the relevant pair.
        Oracle accounts the action consults (``Action.oracle_account_ids``)
        are surfaced as ``read_locks`` per PRD US-006 line 491 so the
        parallel scheduler models oracle-account contention correctly.
        """
        oracle_reads = action.oracle_account_ids
        if isinstance(action, OrderAction):
            book = self._orderbook_account_id(action.base, action.quote)
            return LockedAction(
                action=action,
                read_locks=oracle_reads,
                write_locks=frozenset({book}),
            )
        if isinstance(action, SingleAssetAction):
            book = self._orderbook_account_id(action.asset, action.collateral)
            return LockedAction(
                action=action,
                read_locks=oracle_reads,
                write_locks=frozenset({book}),
            )
        if isinstance(action, SwapAction):
            book = self._orderbook_account_id(action.token_in, action.token_out)
            return LockedAction(
                action=action,
                read_locks=oracle_reads,
                write_locks=frozenset({book}),
            )
        return LockedAction(action=action)

    def get_prices(self) -> dict[TokenId, Numeric]:
        """Mid prices from best bid/ask per pair."""
        prices: dict[TokenId, Numeric] = {}
        base_counts = Counter(base for base, _quote in self._books.keys())
        for (base, _quote), book in self._books.items():
            key = self._book_state_key(base, _quote, base_counts)
            bid = book.best_bid()
            ask = book.best_ask()
            if bid is not None and ask is not None:
                if isinstance(bid, float):
                    prices[key] = (bid + ask) / 2
                else:
                    prices[key] = (bid + ask) // 2
            elif bid is not None:
                prices[key] = bid
            elif ask is not None:
                prices[key] = ask
            else:
                prices[key] = 0
        return prices

    def get_depth(self, token: TokenId) -> Numeric:
        if "/" in token:
            base, quote = token.split("/", 1)
            book = self._books.get((base, quote))
            if book is not None:
                return book.total_depth(OBSide.BUY) + book.total_depth(OBSide.SELL)

        matching_depths: list[Numeric] = []
        for (base, _quote), book in self._books.items():
            if base == token:
                matching_depths.append(book.total_depth(OBSide.BUY) + book.total_depth(OBSide.SELL))
        if not matching_depths:
            return 0
        return sum(matching_depths)

    def copy(self) -> "ClobMarket":
        c = ClobMarket.__new__(ClobMarket)
        c.fee_model = self.fee_model
        c._pairs = list(self._pairs)
        c._books = {k: copy.deepcopy(v) for k, v in self._books.items()}
        c._all_tokens = set(self._all_tokens)
        return c

    def to_bytes(self) -> bytes:
        data = {
            "pairs": [(b.id, b.symbol, b.decimals, q.id, q.symbol, q.decimals)
                      for b, q in self._pairs],
            "fee_model_ref": serialize_callable_ref(self.fee_model),
            "books": {
                f"{base}::{quote}": book.to_dict()
                for (base, quote), book in self._books.items()
            },
        }
        return msgpack.packb(encode_msgpack_value(data), use_bin_type=True)

    @classmethod
    def from_bytes(cls, data: bytes) -> "ClobMarket":
        d = decode_msgpack_value(msgpack.unpackb(data, raw=False, strict_map_key=False))
        pairs = [
            (Token(id=p[0], symbol=p[1], decimals=p[2]),
             Token(id=p[3], symbol=p[4], decimals=p[5]))
            for p in d["pairs"]
        ]
        market = cls(pairs=pairs)
        market.fee_model = deserialize_callable_ref(d.get("fee_model_ref"))
        books = d.get("books", {})
        if books:
            market._books = {}
            for key, value in books.items():
                base, quote = key.split("::", 1)
                market._books[(base, quote)] = Orderbook.from_dict(value)
        return market

    # --- Internal ---

    def _find_book(self, base: TokenId, quote: TokenId) -> Orderbook | None:
        return self._books.get((base, quote))

    @staticmethod
    def _book_state_key(
        base: TokenId,
        quote: TokenId,
        base_counts: Counter[TokenId],
    ) -> TokenId:
        if base_counts[base] == 1:
            return base
        return f"{base}/{quote}"

    def _notional(self, fills: list) -> Numeric:
        total: Numeric = 0
        for fill in fills:
            total = total + (fill.price * fill.quantity)
        return total

    def _compute_fee(self, gross: Numeric, ctx: ExecutionContext) -> tuple[Numeric, dict[str, Numeric]]:
        fee_model = self.get_fee_model(ctx.default_fee_model)
        if fee_model is None or gross <= 0:
            return 0, {}
        fee_result = fee_model(gross, ctx)
        return fee_result.total_fee, dict(fee_result.splits)

    def _counterparty_updates(
        self,
        fills: list,
        acting_agent_id: str | int,
        base: TokenId,
        quote: TokenId,
    ) -> tuple[dict[str | int, dict[TokenId, Numeric]], dict[str | int, Numeric]]:
        deltas: dict[str | int, dict[TokenId, Numeric]] = {}
        volumes: dict[str | int, Numeric] = {}

        for fill in fills:
            notional = fill.price * fill.quantity
            if acting_agent_id == fill.buyer_id:
                counterparty = fill.seller_id
                counterparty_deltas = deltas.setdefault(counterparty, {})
                counterparty_deltas[quote] = counterparty_deltas.get(quote, 0) + notional
            elif acting_agent_id == fill.seller_id:
                counterparty = fill.buyer_id
                counterparty_deltas = deltas.setdefault(counterparty, {})
                counterparty_deltas[base] = counterparty_deltas.get(base, 0) + fill.quantity
            else:
                continue

            volumes[counterparty] = volumes.get(counterparty, 0) + notional

        return deltas, volumes

    def _execute_order(self, action: OrderAction, ctx: ExecutionContext) -> ExecutionResult:
        """Place a limit order."""
        book = self._find_book(action.base, action.quote)
        if book is None:
            return ExecutionResult(success=False, error=f"No orderbook for pair {action.base}/{action.quote}")

        if action.side == OrderSide.BUY:
            required_quote = action.price * action.quantity
            if ctx.agent_state.balance(action.quote) < required_quote:
                return ExecutionResult(success=False, error="insufficient quote balance")
        else:
            if ctx.agent_state.balance(action.base) < action.quantity:
                return ExecutionResult(success=False, error="insufficient base balance")

        ob_side = OBSide.BUY if action.side == OrderSide.BUY else OBSide.SELL
        order = Order(
            agent_id=action.agent_id,
            base=action.base,
            quote=action.quote,
            side=ob_side,
            price=action.price,
            quantity=action.quantity,
            timestamp=ctx.timestamp,
        )

        fills = book.place_order(order)
        filled_notional = self._notional(fills)
        remaining_quantity = order.quantity
        filled_quantity = action.quantity - remaining_quantity
        other_agent_deltas, other_agent_volumes = self._counterparty_updates(
            fills, action.agent_id, action.base, action.quote
        )
        deltas: dict[TokenId, Numeric] = {}

        if action.side == OrderSide.BUY:
            locked_quote = action.price * remaining_quantity
            if filled_quantity > 0:
                deltas[action.base] = deltas.get(action.base, 0) + filled_quantity
            total_quote_delta = filled_notional + locked_quote
            if total_quote_delta > 0:
                deltas[action.quote] = deltas.get(action.quote, 0) - total_quote_delta
        else:
            deltas[action.base] = deltas.get(action.base, 0) - action.quantity
            if filled_notional > 0:
                deltas[action.quote] = deltas.get(action.quote, 0) + filled_notional

        total_fee, fee_splits = self._compute_fee(filled_notional, ctx)
        if total_fee > 0:
            deltas[action.quote] = deltas.get(action.quote, 0) - total_fee

        return ExecutionResult(
            success=True,
            token_deltas=deltas,
            other_agent_deltas=other_agent_deltas,
            fees_paid=total_fee,
            fee_splits=fee_splits,
            fee_token=action.quote,
            volume=filled_notional,
            other_agent_volumes=other_agent_volumes,
        )

    def _execute_market_order(self, action: SingleAssetAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute as a market order by walking the book."""
        pair = (action.asset, action.collateral)
        book = self._books.get(pair)
        if book is None:
            return ExecutionResult(
                success=False,
                error=f"No orderbook for pair {action.asset}/{action.collateral}",
            )

        base, quote = pair
        deltas: dict[TokenId, Numeric] = {}
        if action.side == Side.BUY:
            if ctx.agent_state.balance(action.collateral) < action.amount:
                return ExecutionResult(success=False, error="insufficient collateral balance")
            fills, spent_quote = book.market_buy_by_quote(
                buyer_id=action.agent_id,
                quote_budget=action.amount,
                timestamp=ctx.timestamp,
            )
            other_agent_deltas, other_agent_volumes = self._counterparty_updates(
                fills, action.agent_id, base, quote
            )
            base_bought = sum(fill.quantity for fill in fills)
            deltas[base] = deltas.get(base, 0) + base_bought
            deltas[quote] = deltas.get(quote, 0) - spent_quote
            total_fee, fee_splits = self._compute_fee(spent_quote, ctx)
            if total_fee > 0:
                deltas[quote] = deltas.get(quote, 0) - total_fee
            return ExecutionResult(
                success=True,
                token_deltas=deltas,
                other_agent_deltas=other_agent_deltas,
                fees_paid=total_fee,
                fee_splits=fee_splits,
                fee_token=quote,
                volume=spent_quote,
                other_agent_volumes=other_agent_volumes,
            )

        if ctx.agent_state.balance(base) < action.amount:
            return ExecutionResult(success=False, error="insufficient asset balance")
        if isinstance(action.amount, float):
            price = 0.0
        else:
            price = 0
        order = Order(
            agent_id=action.agent_id,
            base=base,
            quote=quote,
            side=OBSide.SELL,
            price=price,
            quantity=action.amount,
            timestamp=ctx.timestamp,
        )
        fills = book.place_order(order, rest_unfilled=False)
        other_agent_deltas, other_agent_volumes = self._counterparty_updates(
            fills, action.agent_id, base, quote
        )

        for fill in fills:
            cost = fill.price * fill.quantity
            deltas[base] = deltas.get(base, 0) - fill.quantity
            deltas[quote] = deltas.get(quote, 0) + cost

        total_fee, fee_splits = self._compute_fee(self._notional(fills), ctx)
        if total_fee > 0:
            deltas[quote] = deltas.get(quote, 0) - total_fee

        return ExecutionResult(
            success=True,
            token_deltas=deltas,
            other_agent_deltas=other_agent_deltas,
            fees_paid=total_fee,
            fee_splits=fee_splits,
            fee_token=quote,
            volume=self._notional(fills),
            other_agent_volumes=other_agent_volumes,
        )

    def _execute_swap(self, action: SwapAction, ctx: ExecutionContext) -> ExecutionResult:
        """Execute a swap as a market order on the matching pair."""
        book = self._find_book(action.token_in, action.token_out) or self._find_book(action.token_out, action.token_in)
        if book is None:
            return ExecutionResult(success=False, error=f"No orderbook for swap {action.token_in}/{action.token_out}")

        # Determine direction
        deltas: dict[TokenId, Numeric] = {}
        if (action.token_in, action.token_out) in self._books:
            if ctx.agent_state.balance(action.token_in) < action.amount_in:
                return ExecutionResult(success=False, error="insufficient token_in balance")
            price = 0.0 if isinstance(action.amount_in, float) else 0
            order = Order(
                agent_id=action.agent_id,
                base=book.base,
                quote=book.quote,
                side=OBSide.SELL,
                price=price,
                quantity=action.amount_in,
                timestamp=ctx.timestamp,
            )
            fills = book.place_order(order, rest_unfilled=False)
            other_agent_deltas, other_agent_volumes = self._counterparty_updates(
                fills, action.agent_id, book.base, book.quote
            )
            for fill in fills:
                cost = fill.price * fill.quantity
                deltas[book.base] = deltas.get(book.base, 0) - fill.quantity
                deltas[book.quote] = deltas.get(book.quote, 0) + cost
            total_fee, fee_splits = self._compute_fee(self._notional(fills), ctx)
            if total_fee > 0:
                deltas[book.quote] = deltas.get(book.quote, 0) - total_fee
            return ExecutionResult(
                success=True,
                token_deltas=deltas,
                other_agent_deltas=other_agent_deltas,
                fees_paid=total_fee,
                fee_splits=fee_splits,
                fee_token=book.quote,
                volume=self._notional(fills),
                other_agent_volumes=other_agent_volumes,
            )

        if ctx.agent_state.balance(action.token_in) < action.amount_in:
            return ExecutionResult(success=False, error="insufficient token_in balance")

        fills, spent_quote = book.market_buy_by_quote(
            buyer_id=action.agent_id,
            quote_budget=action.amount_in,
            timestamp=ctx.timestamp,
        )
        other_agent_deltas, other_agent_volumes = self._counterparty_updates(
            fills, action.agent_id, book.base, book.quote
        )
        base_bought = sum(fill.quantity for fill in fills)
        deltas[book.base] = deltas.get(book.base, 0) + base_bought
        deltas[book.quote] = deltas.get(book.quote, 0) - spent_quote
        total_fee, fee_splits = self._compute_fee(spent_quote, ctx)
        if total_fee > 0:
            deltas[book.quote] = deltas.get(book.quote, 0) - total_fee

        return ExecutionResult(
            success=True,
            token_deltas=deltas,
            other_agent_deltas=other_agent_deltas,
            fees_paid=total_fee,
            fee_splits=fee_splits,
            fee_token=book.quote,
            volume=spent_quote,
            other_agent_volumes=other_agent_volumes,
        )
