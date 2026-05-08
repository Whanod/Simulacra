"""Price-time priority central limit order book (CLOB).

Ported from quant-simulation models/orderbook.py with TokenId/AgentId types.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass
from enum import Enum
from typing import Any

from defi_sim.core.types import AgentId, Numeric, TokenId


class OBSide(Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class Order:
    agent_id: AgentId
    base: TokenId
    quote: TokenId
    side: OBSide
    price: Numeric
    quantity: Numeric
    timestamp: int = 0
    order_id: int = 0

    @property
    def remaining(self) -> Numeric:
        return self.quantity


@dataclass
class Fill:
    buyer_id: AgentId
    seller_id: AgentId
    price: Numeric
    quantity: Numeric


class Orderbook:
    """Per-pair price-time priority order book."""

    def __init__(self, base: TokenId, quote: TokenId):
        self.base = base
        self.quote = quote
        # Bids: max-heap (negate price for heapq min-heap)
        self._bids: list[tuple[Numeric, int, Order]] = []
        # Asks: min-heap
        self._asks: list[tuple[Numeric, int, Order]] = []
        self._order_counter = 0
        self._fills: list[Fill] = []

    def place_order(self, order: Order, rest_unfilled: bool = True) -> list[Fill]:
        """Place a limit order. Matches against resting orders first,
        then rests any unfilled remainder."""
        self._order_counter += 1
        order.order_id = self._order_counter
        fills: list[Fill] = []

        if order.side == OBSide.BUY:
            fills = self._match_buy(order)
            if rest_unfilled and order.quantity > 0:
                heapq.heappush(self._bids, (-order.price, order.timestamp, order))
        else:
            fills = self._match_sell(order)
            if rest_unfilled and order.quantity > 0:
                heapq.heappush(self._asks, (order.price, order.timestamp, order))

        self._fills.extend(fills)
        return fills

    def _match_buy(self, buy_order: Order) -> list[Fill]:
        """Match buy against resting asks (price-time priority)."""
        fills: list[Fill] = []
        while self._asks and buy_order.quantity > 0:
            ask_price, ask_ts, ask_order = self._asks[0]
            if ask_price > buy_order.price:
                break

            fill_qty: Numeric
            if isinstance(buy_order.quantity, float):
                fill_qty = min(buy_order.quantity, ask_order.quantity)
            else:
                fill_qty = min(buy_order.quantity, ask_order.quantity)

            fills.append(Fill(
                buyer_id=buy_order.agent_id,
                seller_id=ask_order.agent_id,
                price=ask_order.price,
                quantity=fill_qty,
            ))

            buy_order.quantity = buy_order.quantity - fill_qty
            ask_order.quantity = ask_order.quantity - fill_qty

            if ask_order.quantity <= 0:
                heapq.heappop(self._asks)

        return fills

    def _match_sell(self, sell_order: Order) -> list[Fill]:
        """Match sell against resting bids (price-time priority)."""
        fills: list[Fill] = []
        while self._bids and sell_order.quantity > 0:
            neg_bid_price, bid_ts, bid_order = self._bids[0]
            bid_price = -neg_bid_price
            if bid_price < sell_order.price:
                break

            fill_qty: Numeric
            if isinstance(sell_order.quantity, float):
                fill_qty = min(sell_order.quantity, bid_order.quantity)
            else:
                fill_qty = min(sell_order.quantity, bid_order.quantity)

            fills.append(Fill(
                buyer_id=bid_order.agent_id,
                seller_id=sell_order.agent_id,
                price=bid_order.price,
                quantity=fill_qty,
            ))

            sell_order.quantity = sell_order.quantity - fill_qty
            bid_order.quantity = bid_order.quantity - fill_qty

            if bid_order.quantity <= 0:
                heapq.heappop(self._bids)

        return fills

    def market_buy_by_quote(
        self,
        buyer_id: AgentId,
        quote_budget: Numeric,
        timestamp: int = 0,
    ) -> tuple[list[Fill], Numeric]:
        """Spend up to quote_budget against resting asks without resting leftovers."""
        fills: list[Fill] = []
        remaining_budget = quote_budget

        while self._asks and remaining_budget > 0:
            ask_price, _, ask_order = self._asks[0]
            if ask_price <= 0:
                break

            if isinstance(remaining_budget, float):
                affordable_qty = remaining_budget / ask_price
            else:
                affordable_qty = remaining_budget // ask_price

            if affordable_qty <= 0:
                break

            fill_qty = min(ask_order.quantity, affordable_qty)
            if fill_qty <= 0:
                break

            fills.append(Fill(
                buyer_id=buyer_id,
                seller_id=ask_order.agent_id,
                price=ask_order.price,
                quantity=fill_qty,
            ))

            spent = fill_qty * ask_order.price
            remaining_budget = remaining_budget - spent
            ask_order.quantity = ask_order.quantity - fill_qty

            if ask_order.quantity <= 0:
                heapq.heappop(self._asks)

        self._fills.extend(fills)
        return fills, quote_budget - remaining_budget

    def best_bid(self) -> Numeric | None:
        """Return the best (highest) bid price, or None if no bids."""
        while self._bids:
            neg_price, _, order = self._bids[0]
            if order.quantity > 0:
                return -neg_price
            heapq.heappop(self._bids)
        return None

    def best_ask(self) -> Numeric | None:
        """Return the best (lowest) ask price, or None if no asks."""
        while self._asks:
            price, _, order = self._asks[0]
            if order.quantity > 0:
                return price
            heapq.heappop(self._asks)
        return None

    def spread(self) -> Numeric | None:
        """Return bid-ask spread, or None if either side is empty."""
        bid = self.best_bid()
        ask = self.best_ask()
        if bid is not None and ask is not None:
            return ask - bid
        return None

    def total_depth(self, side: OBSide) -> Numeric:
        """Total resting quantity on one side of the book."""
        if side == OBSide.BUY:
            return sum(o.quantity for _, _, o in self._bids if o.quantity > 0)
        return sum(o.quantity for _, _, o in self._asks if o.quantity > 0)

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an order by setting its quantity to 0. Lazy removal."""
        for _, _, order in self._bids:
            if order.order_id == order_id:
                order.quantity = 0
                return True
        for _, _, order in self._asks:
            if order.order_id == order_id:
                order.quantity = 0
                return True
        return False

    @property
    def fills(self) -> list[Fill]:
        return list(self._fills)

    def to_dict(self) -> dict[str, Any]:
        def serialize_heap_entry(entry: tuple[Numeric, int, Order], side: OBSide) -> dict[str, Any]:
            price_key, timestamp, order = entry
            price = -price_key if side == OBSide.BUY else price_key
            return {
                "price_key": price_key,
                "timestamp": timestamp,
                "order": {
                    "agent_id": order.agent_id,
                    "base": order.base,
                    "quote": order.quote,
                    "side": order.side.value,
                    "price": price,
                    "quantity": order.quantity,
                    "timestamp": order.timestamp,
                    "order_id": order.order_id,
                },
            }

        return {
            "base": self.base,
            "quote": self.quote,
            "order_counter": self._order_counter,
            "bids": [serialize_heap_entry(entry, OBSide.BUY) for entry in self._bids if entry[2].quantity > 0],
            "asks": [serialize_heap_entry(entry, OBSide.SELL) for entry in self._asks if entry[2].quantity > 0],
            "fills": [
                {
                    "buyer_id": fill.buyer_id,
                    "seller_id": fill.seller_id,
                    "price": fill.price,
                    "quantity": fill.quantity,
                }
                for fill in self._fills
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Orderbook":
        book = cls(base=data["base"], quote=data["quote"])
        book._order_counter = data.get("order_counter", 0)

        def deserialize_heap_entry(entry: dict[str, Any]) -> tuple[Numeric, int, Order]:
            raw_order = entry["order"]
            order = Order(
                agent_id=raw_order["agent_id"],
                base=raw_order["base"],
                quote=raw_order["quote"],
                side=OBSide(raw_order["side"]),
                price=raw_order["price"],
                quantity=raw_order["quantity"],
                timestamp=raw_order["timestamp"],
                order_id=raw_order["order_id"],
            )
            return (entry["price_key"], entry["timestamp"], order)

        book._bids = [deserialize_heap_entry(entry) for entry in data.get("bids", [])]
        book._asks = [deserialize_heap_entry(entry) for entry in data.get("asks", [])]
        heapq.heapify(book._bids)
        heapq.heapify(book._asks)
        book._fills = [
            Fill(
                buyer_id=fill["buyer_id"],
                seller_id=fill["seller_id"],
                price=fill["price"],
                quantity=fill["quantity"],
            )
            for fill in data.get("fills", [])
        ]
        return book
