"""Route materialized Solana replay actions into engine world markets."""

from __future__ import annotations

from dataclasses import dataclass

from defi_sim.core.types import Action, MultiMarketAction
from defi_sim_solana.replay.materialize import (
    ActionDecodeStatus,
    MaterializedSwapAction,
    TipAction,
    TokenTransferAction,
    action_decode_status,
)

REPLAY_ACCOUNTING_MARKET = "__replay_accounting__"


@dataclass(frozen=True)
class ReplayActionRouting:
    executable: list[MultiMarketAction]
    diagnostics: list[Action]


def route_replay_actions_to_markets(
    actions: list[Action],
    *,
    accounting_market_name: str = REPLAY_ACCOUNTING_MARKET,
) -> ReplayActionRouting:
    """Wrap executable decoded replay actions in ``MultiMarketAction``.

    Partial and opaque actions remain diagnostics. They contribute coverage
    metadata but are deliberately not sent through ``Market.execute``.
    """

    executable: list[MultiMarketAction] = []
    diagnostics: list[Action] = []
    for action in actions:
        if action_decode_status(action) is not ActionDecodeStatus.DECODED:
            diagnostics.append(action)
            continue
        market_name = replay_market_name_for_action(
            action,
            accounting_market_name=accounting_market_name,
        )
        if not market_name:
            diagnostics.append(action)
            continue
        executable.append(wrap_replay_action(action, market_name=market_name))
    return ReplayActionRouting(executable=executable, diagnostics=diagnostics)


def replay_market_name_for_action(
    action: Action,
    *,
    accounting_market_name: str = REPLAY_ACCOUNTING_MARKET,
) -> str | None:
    if isinstance(action, (TipAction, TokenTransferAction)):
        return accounting_market_name
    if isinstance(action, MaterializedSwapAction):
        return action.pool_id or action.protocol_program_id
    for attr in (
        "pool_id",
        "reserve_id",
        "lending_market",
        "market_id",
        "protocol_program_id",
    ):
        value = getattr(action, attr, None)
        if value:
            return str(value)
    return None


def wrap_replay_action(action: Action, *, market_name: str) -> MultiMarketAction:
    return MultiMarketAction(
        agent_id=action.agent_id,
        num_required_signatures=action.num_required_signatures,
        compute_unit_limit=action.compute_unit_limit,
        compute_unit_price_micro_lamports=action.compute_unit_price_micro_lamports,
        submission_path=action.submission_path,
        oracle_account_ids=action.oracle_account_ids,
        recent_blockhash=action.recent_blockhash,
        expiry_slot=action.expiry_slot,
        market_name=market_name,
        inner=action,
    )


def unwrap_replay_action(action: Action) -> Action:
    if isinstance(action, MultiMarketAction):
        return action.inner
    return action
