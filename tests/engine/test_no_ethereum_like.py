"""Lock the EthereumLikeExecution deletion (PRD US-003 line 321).

Phase 1 of the Solana pivot deletes ``EthereumLikeExecution``. Any test that
previously instantiated it has been rewritten to use ``BatchExecution`` (parent
behaviour) or ``SolanaLikeExecution`` (Solana-shaped behaviour). This module
pins the deletion: importing the symbol must raise ``ImportError``.
"""

from __future__ import annotations

import pytest


def test_ethereum_like_execution_does_not_exist() -> None:
    with pytest.raises(ImportError):
        from defi_sim.engine.execution import EthereumLikeExecution  # noqa: F401
