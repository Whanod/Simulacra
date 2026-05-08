"""Pyth Pull ``PriceUpdateV2`` fixture parser.

Parses committed Pyth Solana Receiver ``PriceUpdateV2`` account fixtures into
``oracle_price`` :class:`~defi_sim.engine.initial_state.InitialStateFragment`
objects. This is intentionally fixture-scoped: production old-slot oracle
hydration still requires an exact as-of-slot account-state source before
``ForkLoader._load_oracle`` can call this parser.

Supported account format
------------------------
``PriceUpdateV2`` from ``pyth-solana-receiver-sdk``:

* Anchor discriminator ``sha256("account:PriceUpdateV2")[:8]``.
* ``write_authority`` pubkey.
* ``VerificationLevel`` encoded as Borsh enum. The parser supports
  ``Partial { num_signatures }`` and ``Full``.
* ``PriceFeedMessage`` fields ``feed_id``, ``price``, ``conf``, ``exponent``,
  ``publish_time``, ``prev_publish_time``, ``ema_price``, and ``ema_conf``.
* ``posted_slot``.

Legacy Pyth price-feed-program accounts and TWAP accounts are not parsed by
this fixture hydrator yet; they should get their own parser and tests when a
story needs them.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from defi_sim.engine.initial_state import InitialStateFragment
from defi_sim.engine.state_hydrator import (
    AccountFilter,
    OracleId,
    StateHydrator,
)
from defi_sim_solana.program_ids import PYTH_SOLANA_RECEIVER_PROGRAM

__all__ = [
    "PYTH_PRICE_UPDATE_V2_DISCRIMINATOR",
    "PYTH_SOLANA_RECEIVER_PROGRAM",
    "PythPriceUpdate",
    "PythPriceUpdateHydrator",
]


PYTH_PRICE_UPDATE_V2_DISCRIMINATOR = hashlib.sha256(
    b"account:PriceUpdateV2"
).digest()[:8]

_PRICE_MESSAGE_LEN = 32 + 8 + 8 + 4 + 8 + 8 + 8 + 8
_POSTED_SLOT_LEN = 8
_PARTIAL_MESSAGE_OFFSET = 8 + 32 + 2
_FULL_MESSAGE_OFFSET = 8 + 32 + 1
_MIN_PARTIAL_LEN = _PARTIAL_MESSAGE_OFFSET + _PRICE_MESSAGE_LEN + _POSTED_SLOT_LEN
_MIN_FULL_LEN = _FULL_MESSAGE_OFFSET + _PRICE_MESSAGE_LEN + _POSTED_SLOT_LEN
_DEFAULT_MAX_CONFIDENCE_BPS = 1_000
_DEFAULT_MIN_PARTIAL_SIGNATURES = 1


@dataclass(frozen=True, slots=True)
class PythPriceUpdate:
    """Typed view of a parsed Pyth ``PriceUpdateV2`` fixture account."""

    pubkey: str
    feed_id: str
    price: int
    confidence: int
    exponent: int
    publish_time: int
    prev_publish_time: int
    ema_price: int
    ema_confidence: int
    posted_slot: int
    verification_level: str
    num_signatures: int | None
    confidence_bps: int | None
    is_stale: bool
    is_confidence_heavy: bool
    is_valid: bool
    status: str
    warnings: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "source": "pyth_pull",
            "feed_id": self.feed_id,
            "price": self.price,
            "confidence": self.confidence,
            "exponent": self.exponent,
            "publish_time": self.publish_time,
            "prev_publish_time": self.prev_publish_time,
            "ema_price": self.ema_price,
            "ema_confidence": self.ema_confidence,
            "posted_slot": self.posted_slot,
            "verification_level": self.verification_level,
            "num_signatures": self.num_signatures,
            "confidence_bps": self.confidence_bps,
            "is_stale": self.is_stale,
            "is_confidence_heavy": self.is_confidence_heavy,
            "is_valid": self.is_valid,
            "status": self.status,
            "warnings": list(self.warnings),
        }


class PythPriceUpdateHydrator(StateHydrator):
    """Parser for committed Pyth Pull ``PriceUpdateV2`` fixture accounts."""

    program_id: str = PYTH_SOLANA_RECEIVER_PROGRAM
    schema_version: int = 1

    def __init__(
        self,
        *,
        reference_unix_timestamp: int | None = None,
        maximum_age_seconds: int | None = None,
        max_confidence_bps: int = _DEFAULT_MAX_CONFIDENCE_BPS,
        min_partial_signatures: int = _DEFAULT_MIN_PARTIAL_SIGNATURES,
    ) -> None:
        self.reference_unix_timestamp = reference_unix_timestamp
        self.maximum_age_seconds = maximum_age_seconds
        self.max_confidence_bps = max_confidence_bps
        self.min_partial_signatures = min_partial_signatures

    def account_filters(self) -> list[AccountFilter]:
        return [AccountFilter(discriminator=PYTH_PRICE_UPDATE_V2_DISCRIMINATOR)]

    def oracle_dependencies(self) -> list[OracleId]:
        return []

    def parse_price_update(self, pubkey: str, data: bytes) -> PythPriceUpdate:
        """Parse raw ``PriceUpdateV2`` account bytes into a typed price view."""
        if len(data) < _MIN_FULL_LEN:
            raise ValueError(
                f"Pyth PriceUpdateV2 account {pubkey!r} is {len(data)} bytes; "
                f"need at least {_MIN_FULL_LEN} bytes."
            )
        if data[:8] != PYTH_PRICE_UPDATE_V2_DISCRIMINATOR:
            raise ValueError(
                f"Pyth PriceUpdateV2 account {pubkey!r} has discriminator "
                f"{data[:8].hex()}; expected "
                f"{PYTH_PRICE_UPDATE_V2_DISCRIMINATOR.hex()}."
            )

        verification_level, num_signatures, message_offset = (
            _verification_level_and_offset(data, pubkey)
        )
        feed_id, price, confidence, exponent, publish_time, prev_publish_time, ema_price, ema_confidence = (
            _parse_price_message(data, message_offset, pubkey)
        )
        posted_slot = _u64(data, message_offset + _PRICE_MESSAGE_LEN, pubkey)
        confidence_bps = (
            None if price == 0 else abs(confidence) * 10_000 // abs(price)
        )

        warnings: list[str] = []
        if verification_level == "partial" and (
            num_signatures is None
            or num_signatures < self.min_partial_signatures
        ):
            warnings.append("insufficient_verification")
        if price <= 0:
            warnings.append("non_positive_price")
        if (
            self.reference_unix_timestamp is not None
            and self.maximum_age_seconds is not None
            and publish_time + self.maximum_age_seconds
            < self.reference_unix_timestamp
        ):
            warnings.append("stale")
        if (
            confidence_bps is not None
            and confidence_bps > self.max_confidence_bps
        ):
            warnings.append("confidence_heavy")

        is_stale = "stale" in warnings
        is_confidence_heavy = "confidence_heavy" in warnings
        is_valid = not {
            "insufficient_verification",
            "non_positive_price",
        }.intersection(warnings)
        if not is_valid:
            status = "invalid"
        elif is_stale:
            status = "stale"
        elif is_confidence_heavy:
            status = "confidence_heavy"
        else:
            status = "valid"

        return PythPriceUpdate(
            pubkey=pubkey,
            feed_id="0x" + feed_id.hex(),
            price=price,
            confidence=confidence,
            exponent=exponent,
            publish_time=publish_time,
            prev_publish_time=prev_publish_time,
            ema_price=ema_price,
            ema_confidence=ema_confidence,
            posted_slot=posted_slot,
            verification_level=verification_level,
            num_signatures=num_signatures,
            confidence_bps=confidence_bps,
            is_stale=is_stale,
            is_confidence_heavy=is_confidence_heavy,
            is_valid=is_valid,
            status=status,
            warnings=tuple(warnings),
        )

    def parse_account(self, pubkey: str, data: bytes) -> InitialStateFragment:
        price_update = self.parse_price_update(pubkey, data)
        return InitialStateFragment(
            kind="oracle_price",
            protocol_model="PythPull",
            pubkey=pubkey,
            owner=None,
            payload=price_update.to_payload(),
        )


def _verification_level_and_offset(
    data: bytes,
    pubkey: str,
) -> tuple[str, int | None, int]:
    tag = data[40]
    if tag == 0:
        if len(data) < _MIN_PARTIAL_LEN:
            raise ValueError(
                f"Pyth PriceUpdateV2 partial account {pubkey!r} is "
                f"{len(data)} bytes; need at least {_MIN_PARTIAL_LEN} bytes."
            )
        return "partial", data[41], _PARTIAL_MESSAGE_OFFSET
    if tag == 1:
        if len(data) < _MIN_FULL_LEN:
            raise ValueError(
                f"Pyth PriceUpdateV2 full account {pubkey!r} is {len(data)} "
                f"bytes; need at least {_MIN_FULL_LEN} bytes."
            )
        return "full", None, _best_full_message_offset(data)
    raise ValueError(
        f"Pyth PriceUpdateV2 account {pubkey!r} has unsupported "
        f"VerificationLevel tag {tag}."
    )


def _best_full_message_offset(data: bytes) -> int:
    """Handle both normal Borsh Full and max-size padded fixture accounts."""
    if len(data) >= _MIN_PARTIAL_LEN and _looks_like_price_message(
        data, _PARTIAL_MESSAGE_OFFSET
    ):
        if not _looks_like_price_message(data, _FULL_MESSAGE_OFFSET):
            return _PARTIAL_MESSAGE_OFFSET
    return _FULL_MESSAGE_OFFSET


def _looks_like_price_message(data: bytes, offset: int) -> bool:
    if offset + _PRICE_MESSAGE_LEN + _POSTED_SLOT_LEN > len(data):
        return False
    feed_id = data[offset : offset + 32]
    if not any(feed_id):
        return False
    exponent = int.from_bytes(data[offset + 48 : offset + 52], "little", signed=True)
    return -30 <= exponent <= 30


def _parse_price_message(
    data: bytes,
    offset: int,
    pubkey: str,
) -> tuple[bytes, int, int, int, int, int, int, int]:
    if offset + _PRICE_MESSAGE_LEN + _POSTED_SLOT_LEN > len(data):
        raise ValueError(
            f"Pyth PriceUpdateV2 account {pubkey!r} is too short for "
            "PriceFeedMessage and posted_slot."
        )
    feed_id = data[offset : offset + 32]
    return (
        feed_id,
        _i64(data, offset + 32, pubkey),
        _u64(data, offset + 40, pubkey),
        _i32(data, offset + 48, pubkey),
        _i64(data, offset + 52, pubkey),
        _i64(data, offset + 60, pubkey),
        _i64(data, offset + 68, pubkey),
        _u64(data, offset + 76, pubkey),
    )


def _i64(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 8, True, pubkey)


def _u64(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 8, False, pubkey)


def _i32(data: bytes, offset: int, pubkey: str) -> int:
    return _int(data, offset, 4, True, pubkey)


def _int(data: bytes, offset: int, length: int, signed: bool, pubkey: str) -> int:
    end = offset + length
    if end > len(data):
        raise ValueError(
            f"Pyth PriceUpdateV2 account {pubkey!r} is too short to read "
            f"{length} bytes at offset {offset}."
        )
    return int.from_bytes(data[offset:end], "little", signed=signed)
