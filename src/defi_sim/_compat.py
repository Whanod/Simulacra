"""Compatibility shims for optional runtime dependencies."""

from __future__ import annotations

import pickle
from typing import Any

try:
    import msgpack as msgpack
except ModuleNotFoundError:  # pragma: no cover - exercised only in minimal test envs
    class _MsgpackCompat:
        @staticmethod
        def packb(obj: Any, use_bin_type: bool = True) -> bytes:
            return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

        @staticmethod
        def unpackb(data: bytes, raw: bool = False) -> Any:
            return pickle.loads(data)

    msgpack = _MsgpackCompat()
