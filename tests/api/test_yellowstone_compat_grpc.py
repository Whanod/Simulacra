"""Coverage for the optional Yellowstone-style gRPC compatibility surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from defi_sim_api.grpc import yellowstone_compat

grpc = pytest.importorskip("grpc")


def _fields(message: bytes) -> Any:
    return yellowstone_compat._parse_fields(message)


def test_yellowstone_proto_declares_expected_streams() -> None:
    proto = Path(yellowstone_compat.PROTO_PATH).read_text(encoding="utf-8")

    assert "service YellowstoneCompat" in proto
    assert "rpc SubscribeSlotUpdates" in proto
    assert "rpc SubscribeAccountUpdates" in proto
    assert "rpc SubscribeTransactionUpdates" in proto


def test_slot_and_account_update_messages_stream_from_corpus() -> None:
    slots = list(yellowstone_compat.iter_slot_update_messages())
    accounts = list(yellowstone_compat.iter_account_update_messages())

    assert slots
    assert accounts
    slot_fields = _fields(slots[-1])
    account_fields = _fields(accounts[-1])
    assert slot_fields.varints[1] >= 420_196_842
    assert 2 in slot_fields.strings
    assert account_fields.varints[1] >= 420_196_842
    assert account_fields.strings[2]
    assert account_fields.strings[3]


def test_yellowstone_grpc_server_exposes_subscription_methods() -> None:
    server = yellowstone_compat.create_yellowstone_compat_server()
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    try:
        channel = grpc.insecure_channel(f"127.0.0.1:{port}")
        slot_stub = channel.unary_stream(
            f"/{yellowstone_compat.SERVICE_NAME}/SubscribeSlotUpdates",
            request_serializer=lambda payload: payload,
            response_deserializer=lambda payload: payload,
        )
        account_stub = channel.unary_stream(
            f"/{yellowstone_compat.SERVICE_NAME}/SubscribeAccountUpdates",
            request_serializer=lambda payload: payload,
            response_deserializer=lambda payload: payload,
        )
        tx_stub = channel.unary_stream(
            f"/{yellowstone_compat.SERVICE_NAME}/SubscribeTransactionUpdates",
            request_serializer=lambda payload: payload,
            response_deserializer=lambda payload: payload,
        )

        assert list(slot_stub(b"", timeout=5))
        assert list(account_stub(b"", timeout=5))
        assert list(tx_stub(b"", timeout=5)) == []
    finally:
        server.stop(grace=None)
