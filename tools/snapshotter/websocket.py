"""``slotSubscribe`` websocket loop (FIX-019).

Thin wrapper around :func:`solana.rpc.websocket_api.connect` that yields
each new slot number to the synchronous :class:`SnapshotterRunner`. Used
only by the ``--watch`` mode of the snapshotter CLI.

The default endpoint is derived from ``SOLANA_RPC_URL`` by swapping the
HTTP scheme for ``wss``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterator
from typing import AsyncIterator

__all__ = ["derive_websocket_endpoint", "iter_subscribed_slots", "subscribed_slots"]


_LOG = logging.getLogger(__name__)


def derive_websocket_endpoint(http_endpoint: str | None = None) -> str:
    """Map a Helius RPC URL to its websocket counterpart.

    ``http_endpoint`` defaults to ``$SOLANA_RPC_URL``. ``http://`` becomes
    ``ws://`` and ``https://`` becomes ``wss://``; other schemes pass through
    unchanged so callers can supply an explicit ``wss://`` URL.
    """
    endpoint = http_endpoint or os.environ.get("SOLANA_RPC_URL")
    if not endpoint:
        raise RuntimeError(
            "derive_websocket_endpoint() needs SOLANA_RPC_URL or an explicit "
            "endpoint argument."
        )
    if endpoint.startswith("https://"):
        return "wss://" + endpoint[len("https://") :]
    if endpoint.startswith("http://"):
        return "ws://" + endpoint[len("http://") :]
    return endpoint


async def _slot_async_iter(
    endpoint: str, *, max_slots: int | None = None
) -> AsyncIterator[int]:
    """Async generator over slot numbers from ``slotSubscribe``."""
    from solana.rpc.websocket_api import connect

    async with connect(endpoint) as ws:
        await ws.slot_subscribe()
        await ws.recv()  # drain the subscription confirmation

        emitted = 0
        while True:
            messages = await ws.recv()
            for msg in messages if isinstance(messages, list) else [messages]:
                slot_value = _extract_slot_number(msg)
                if slot_value is None:
                    continue
                yield slot_value
                emitted += 1
                if max_slots is not None and emitted >= max_slots:
                    return


def _extract_slot_number(msg: object) -> int | None:
    """Pluck the slot number out of a slotSubscribe notification.

    The :mod:`solana` library wraps notifications in a typed object whose
    ``.result`` is a ``SlotInfo`` exposing ``.slot``. Some test fakes pass
    plain dicts shaped like ``{"slot": <int>}`` — both shapes work here.
    """
    result = getattr(msg, "result", msg)
    slot_attr = getattr(result, "slot", None)
    if isinstance(slot_attr, int):
        return slot_attr
    if isinstance(result, dict):
        slot = result.get("slot")
        if isinstance(slot, int):
            return slot
    return None


def subscribed_slots(
    endpoint: str | None = None, *, max_slots: int | None = None
) -> AsyncIterator[int]:
    """Public async iterator over slot numbers from the live websocket."""
    return _slot_async_iter(
        derive_websocket_endpoint(endpoint), max_slots=max_slots
    )


def iter_subscribed_slots(
    endpoint: str | None = None, *, max_slots: int | None = None
) -> Iterator[int]:
    """Synchronous wrapper that drains :func:`subscribed_slots` via ``asyncio.run``.

    Lets ``__main__.py`` stay synchronous and lets tests inject a fake
    iterator without touching ``asyncio``. Each delivered slot crosses the
    sync/async boundary one at a time so the websocket connection stays
    open between yields.
    """
    queue: asyncio.Queue[int | _Sentinel] = asyncio.Queue(maxsize=1)
    sentinel = _Sentinel()

    async def _producer() -> None:
        try:
            async for slot in subscribed_slots(endpoint, max_slots=max_slots):
                await queue.put(slot)
        finally:
            await queue.put(sentinel)

    loop = asyncio.new_event_loop()
    try:
        producer = loop.create_task(_producer())
        try:
            while True:
                item = loop.run_until_complete(queue.get())
                if item is sentinel:
                    return
                yield item  # type: ignore[misc]
        finally:
            producer.cancel()
            try:
                loop.run_until_complete(producer)
            except (asyncio.CancelledError, Exception):
                _LOG.debug("snapshotter websocket producer task cancelled")
    finally:
        loop.close()


class _Sentinel:
    """Marker object placed on the bridge queue when the producer finishes."""
