"""Yellowstone-style gRPC compatibility handlers.

The JSON-RPC shim is the primary Solana compatibility surface. This module
keeps the optional gRPC surface dependency-light by registering generic
``grpcio`` handlers that stream protobuf-compatible messages sourced from the
same committed corpus fixtures. Importing the module does not require grpcio;
creating a server does.
"""

from __future__ import annotations

import json
from concurrent import futures
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from defi_sim_solana.replay.account_client import AccountRecord, AccountSnapshot
from defi_sim_solana.replay.corpus import corpus_root, load_corpus_fixture
from defi_sim_solana.replay.slot_client import SlotSnapshot

try:  # pragma: no cover - exercised when optional dependency is installed.
    import grpc as _grpc
except ModuleNotFoundError:  # pragma: no cover - optional dependency path.
    _grpc = None

SERVICE_NAME = "defi_sim.solana.yellowstone.v1.YellowstoneCompat"
PROTO_PATH = "solana-plans/api-specs/proto/yellowstone_compat.proto"


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    """Decoded subset of the protobuf subscription requests."""

    from_slot: int = 0
    limit: int = 100
    program_ids: tuple[str, ...] = ()
    pubkeys: tuple[str, ...] = ()
    signatures: tuple[str, ...] = ()
    account_keys: tuple[str, ...] = ()


def create_yellowstone_compat_server(*, max_workers: int = 2) -> Any:
    """Create a grpcio server with the Yellowstone compatibility service.

    The caller owns binding and lifecycle:

    ``server.add_insecure_port("127.0.0.1:50051"); server.start()``.
    """

    grpc = _require_grpc()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    add_yellowstone_compat_to_server(server)
    return server


def add_yellowstone_compat_to_server(server: Any) -> None:
    """Register the compatibility service on an existing grpcio server."""

    grpc = _require_grpc()
    handler = grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {
            "SubscribeSlotUpdates": grpc.unary_stream_rpc_method_handler(
                _subscribe_slot_updates,
                request_deserializer=_identity,
                response_serializer=_identity,
            ),
            "SubscribeAccountUpdates": grpc.unary_stream_rpc_method_handler(
                _subscribe_account_updates,
                request_deserializer=_identity,
                response_serializer=_identity,
            ),
            "SubscribeTransactionUpdates": grpc.unary_stream_rpc_method_handler(
                _subscribe_transaction_updates,
                request_deserializer=_identity,
                response_serializer=_identity,
            ),
        },
    )
    server.add_generic_rpc_handlers((handler,))


def iter_slot_update_messages(request: bytes = b"") -> Iterator[bytes]:
    """Yield protobuf-encoded ``SlotUpdate`` messages from corpus slots."""

    params = _parse_slot_request(request)
    yielded = 0
    for snapshot in _iter_slot_snapshots():
        if snapshot.slot < params.from_slot:
            continue
        yield _encode_slot_update(snapshot)
        yielded += 1
        if yielded >= params.limit:
            break


def iter_account_update_messages(request: bytes = b"") -> Iterator[bytes]:
    """Yield protobuf-encoded ``AccountUpdate`` messages from account fixtures."""

    params = _parse_account_request(request)
    yielded = 0
    program_filter = set(params.program_ids)
    pubkey_filter = set(params.pubkeys)
    for slot, snapshot in _iter_account_snapshots():
        if slot < params.from_slot:
            continue
        if program_filter and snapshot.program_id not in program_filter:
            continue
        for record in snapshot.accounts:
            if pubkey_filter and record.pubkey not in pubkey_filter:
                continue
            yield _encode_account_update(record)
            yielded += 1
            if yielded >= params.limit:
                return


def iter_transaction_update_messages(request: bytes = b"") -> Iterator[bytes]:
    """Yield protobuf-encoded ``TransactionUpdate`` messages from block fixtures."""

    params = _parse_transaction_request(request)
    yielded = 0
    signature_filter = set(params.signatures)
    account_filter = set(params.account_keys)
    for snapshot in _iter_slot_snapshots():
        if snapshot.slot < params.from_slot:
            continue
        for tx in snapshot.transactions:
            if not isinstance(tx, dict):
                continue
            signatures = _extract_signatures(tx)
            if signature_filter and not signature_filter.intersection(signatures):
                continue
            account_keys = _extract_account_keys(tx)
            if account_filter and not account_filter.intersection(account_keys):
                continue
            for signature in signatures or ("",):
                yield _encode_transaction_update(snapshot.slot, signature, tx)
                yielded += 1
                if yielded >= params.limit:
                    return


def _subscribe_slot_updates(request: bytes, _context: Any) -> Iterator[bytes]:
    yield from iter_slot_update_messages(request)


def _subscribe_account_updates(request: bytes, _context: Any) -> Iterator[bytes]:
    yield from iter_account_update_messages(request)


def _subscribe_transaction_updates(request: bytes, _context: Any) -> Iterator[bytes]:
    yield from iter_transaction_update_messages(request)


def _require_grpc() -> Any:
    if _grpc is None:
        raise RuntimeError(
            "Yellowstone gRPC compatibility requires the `solana-rpc` extra "
            "or an environment with grpcio installed."
        )
    return _grpc


def _identity(value: bytes) -> bytes:
    return value


def _corpus_slots() -> list[int]:
    root = corpus_root()
    if not root.is_dir():
        return []
    slots: list[int] = []
    for child in root.iterdir():
        if child.is_dir() and child.name.replace("_", "").isdigit():
            slots.append(int(child.name.replace("_", "")))
    return sorted(slots)


def _iter_slot_snapshots() -> Iterator[SlotSnapshot]:
    for slot in _corpus_slots():
        raw = load_corpus_fixture(slot, "block")
        if raw is not None:
            yield SlotSnapshot.from_raw(raw)


def _program_fixture_ids(slot: int) -> list[str]:
    slot_dir = corpus_root() / str(slot)
    if not slot_dir.is_dir():
        return []
    ids: set[str] = set()
    suffixes = (".sample.json.gz", ".json.gz", ".sample.json", ".json")
    for path in slot_dir.glob("program_accounts-*.json*"):
        name = path.name.removeprefix("program_accounts-")
        for suffix in suffixes:
            if name.endswith(suffix):
                ids.add(name.removesuffix(suffix))
                break
    return sorted(ids)


def _iter_account_snapshots() -> Iterator[tuple[int, AccountSnapshot]]:
    for slot in _corpus_slots():
        for program_id in _program_fixture_ids(slot):
            raw = load_corpus_fixture(slot, "program_accounts", program_id=program_id)
            if raw is not None:
                yield slot, AccountSnapshot.from_raw(raw)


def _extract_signatures(tx: dict[str, Any]) -> tuple[str, ...]:
    inner = tx.get("transaction")
    if isinstance(inner, dict):
        signatures = inner.get("signatures")
    else:
        signatures = tx.get("signatures")
    return tuple(sig for sig in signatures or () if isinstance(sig, str))


def _extract_account_keys(tx: dict[str, Any]) -> tuple[str, ...]:
    inner = tx.get("transaction")
    if not isinstance(inner, dict):
        inner = tx
    message = inner.get("message")
    if not isinstance(message, dict):
        return ()
    keys = message.get("accountKeys") or message.get("staticAccountKeys") or ()
    parsed: list[str] = []
    for key in keys:
        if isinstance(key, str):
            parsed.append(key)
        elif isinstance(key, dict) and isinstance(key.get("pubkey"), str):
            parsed.append(key["pubkey"])
    return tuple(parsed)


def _encode_slot_update(snapshot: SlotSnapshot) -> bytes:
    return b"".join(
        (
            _field_varint(1, snapshot.slot),
            _field_string(2, snapshot.blockhash or ""),
            _field_varint(3, snapshot.block_time or 0),
        )
    )


def _encode_account_update(record: AccountRecord) -> bytes:
    return b"".join(
        (
            _field_varint(1, record.slot),
            _field_string(2, record.pubkey),
            _field_string(3, record.owner),
            _field_varint(4, record.lamports),
            _field_bytes(5, record.account_data),
        )
    )


def _encode_transaction_update(
    slot: int,
    signature: str,
    transaction: dict[str, Any],
) -> bytes:
    raw = json.dumps(transaction, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return b"".join(
        (
            _field_varint(1, slot),
            _field_string(2, signature),
            _field_bytes(3, raw),
        )
    )


def _field_varint(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(max(0, int(value)))


def _field_string(field_number: int, value: str) -> bytes:
    return _field_bytes(field_number, value.encode("utf-8"))


def _field_bytes(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _key(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _varint(value: int) -> bytes:
    pieces: list[int] = []
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            pieces.append(to_write | 0x80)
        else:
            pieces.append(to_write)
            return bytes(pieces)


def _parse_slot_request(data: bytes) -> SubscribeRequest:
    values = _parse_fields(data)
    return SubscribeRequest(
        from_slot=int(values.varints.get(1, 0)),
        limit=_bounded_limit(values.varints.get(2)),
    )


def _parse_account_request(data: bytes) -> SubscribeRequest:
    values = _parse_fields(data)
    return SubscribeRequest(
        from_slot=int(values.varints.get(1, 0)),
        program_ids=tuple(values.strings.get(2, ())),
        pubkeys=tuple(values.strings.get(3, ())),
        limit=_bounded_limit(values.varints.get(4)),
    )


def _parse_transaction_request(data: bytes) -> SubscribeRequest:
    values = _parse_fields(data)
    return SubscribeRequest(
        from_slot=int(values.varints.get(1, 0)),
        signatures=tuple(values.strings.get(2, ())),
        account_keys=tuple(values.strings.get(3, ())),
        limit=_bounded_limit(values.varints.get(4)),
    )


def _bounded_limit(raw: int | None) -> int:
    if raw is None or raw <= 0:
        return 100
    return min(int(raw), 1_000)


@dataclass(slots=True)
class _ParsedFields:
    varints: dict[int, int] = field(default_factory=dict)
    strings: dict[int, list[str]] = field(default_factory=dict)


def _parse_fields(data: bytes) -> _ParsedFields:
    parsed = _ParsedFields()
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x7
        if wire_type == 0:
            value, pos = _read_varint(data, pos)
            parsed.varints[field_number] = value
            continue
        if wire_type == 2:
            length, pos = _read_varint(data, pos)
            raw = data[pos : pos + length]
            pos += length
            try:
                parsed.strings.setdefault(field_number, []).append(raw.decode("utf-8"))
            except UnicodeDecodeError:
                pass
            continue
        break
    return parsed


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    shift = 0
    value = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
    return value, pos
