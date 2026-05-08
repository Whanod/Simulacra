"""Invariant checks, conservation, solvency validation."""

from __future__ import annotations

import inspect
from typing import Any, Callable, Iterable

from defi_sim.core.agent import Agent
from defi_sim.core.types import (
    AgentId,
    AmmSnapshot,
    ExecutionResult,
    MarketSnapshot,
)
from defi_sim.core.market import Market
from defi_sim.engine.events import Event, EventBus, EventType


def check_conservation(
    pre: MarketSnapshot,
    post: MarketSnapshot,
    result: ExecutionResult,
) -> bool:
    """Verify token conservation across a single trade execution."""
    pre_reserves = getattr(pre, "reserves", None)
    post_reserves = getattr(post, "reserves", None)
    if not isinstance(pre_reserves, dict) or not isinstance(post_reserves, dict):
        return True

    tokens = set(getattr(pre, "tokens", []))
    tokens.update(getattr(post, "tokens", []))
    tokens.update(pre_reserves)
    tokens.update(post_reserves)
    tokens.update(result.token_deltas)

    for token in tokens:
        pre_reserve = pre_reserves.get(token, 0)
        post_reserve = post_reserves.get(token, 0)
        delta = result.token_deltas.get(token, 0)
        reserve_change = post_reserve - pre_reserve

        # For tokens explicitly moved to or from the trader, reserve changes
        # should mirror the trader delta even if the market has additional
        # internal mint/burn mechanics for other reserves.
        if token in result.token_deltas and reserve_change != -delta:
            return False

        if post_reserve < 0:
            return False

    return True


def check_agent_solvency(agents: Iterable[Agent]) -> list[AgentId]:
    """Return agent_ids with any negative balance."""
    insolvent: list[AgentId] = []
    for agent in agents:
        for token, balance in agent.state.balances.items():
            if balance < 0:
                insolvent.append(agent.agent_id)
                break
    return insolvent


def check_no_negative_reserves(market: Market) -> bool:
    """Verify all reserves / pool values are non-negative."""
    state = market.get_state()
    if isinstance(state, AmmSnapshot):
        for token, reserve in state.reserves.items():
            if reserve < 0:
                return False
    return True


class ValidationHook:
    """Subscribes to EventBus and runs configurable checks after each round."""

    def __init__(
        self,
        bus: EventBus,
        checks: list[Callable] | None = None,
        fail_fast: bool = True,
        agents: list[Agent] | None = None,
        market: Market | None = None,
    ):
        self._checks = checks or []
        self._fail_fast = fail_fast
        self._agents = agents or []
        self._market = market
        self._violations: list[tuple[int, str]] = []
        bus.on(EventType.ROUND_END, self._on_round_end)

    def _on_round_end(self, event: Event) -> None:
        round_num = event.round

        for check in self._checks:
            try:
                result = self._run_check(check, event)
                if result in (None, True) or result == [] or result == {} or result == ():
                    continue

                if check is check_agent_solvency:
                    msg = f"Round {round_num}: Insolvent agents: {result}"
                elif check is check_no_negative_reserves:
                    msg = f"Round {round_num}: Negative reserves detected"
                else:
                    msg = f"Round {round_num}: {check.__name__} failed: {result}"

                self._violations.append((round_num, msg))
                if self._fail_fast:
                    raise AssertionError(msg)
            except AssertionError:
                raise
            except Exception as e:
                self._violations.append((round_num, str(e)))

    def _run_check(self, check: Callable, event: Event) -> Any:
        signature = inspect.signature(check)
        kwargs: dict[str, Any] = {}

        for name in signature.parameters:
            if name == "agents":
                kwargs[name] = self._agents
            elif name == "market":
                kwargs[name] = self._market
            elif name == "event":
                kwargs[name] = event
            elif name == "hook":
                kwargs[name] = self

        if kwargs:
            return check(**kwargs)

        if check is check_agent_solvency:
            return check(self._agents)
        if check is check_no_negative_reserves and self._market is not None:
            return check(self._market)
        return check()

    @property
    def violations(self) -> list[tuple[int, str]]:
        return list(self._violations)
