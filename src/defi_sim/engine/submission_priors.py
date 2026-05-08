"""Submission-path landing priors for Solana-like execution.

These are *priors*, not models: each submission path (RPC, TPU/QUIC, Jito
relayer) lands with a configurable baseline probability, optionally degraded
by a congestion penalty.

Calibration scope (FIX-020):

* ``jito_relayer_landing_prob_baseline`` is calibrated against a real
  captured corpus of mainnet Jito bundles (see
  ``solana-plans/calibration/corpus/jito_bundles/`` and
  ``defi_sim_solana.calibration.tip_quote``). The fit uses the share of
  bundles whose every tx landed without ``meta.err`` as a proxy for the
  Jito-relayer landing probability — an *upper bound* on the true rate
  because pre-leader (block-engine) drops are not observable in finalized
  blocks. The prior is intentionally conservative; in-process telemetry
  can correct it as the sim runs.

* ``rpc_landing_prob_baseline`` and ``tpu_quic_landing_prob_baseline`` are
  intentionally NOT calibrated. RPC/TPU drops happen in transit and don't
  surface on chain (a dropped tx leaves no trace), so neither
  ``getBlock`` nor any public RPC method can reconstruct them from
  finalized state. Calibrating these requires sender-side telemetry from a
  running validator/forwarder, which is outside Phase 2.4's scope. The
  defaults are illustrative until a Phase 3 sender-side capture lands.

``calibrated_at`` is set when the Jito-relayer prior is replaced with a
fitted value; it stays ``None`` for the illustrative defaults so consumers
can detect the calibration state.
"""

from __future__ import annotations

from dataclasses import dataclass


# FIX-020: Calibrated against 54_024 mainnet Jito bundles captured across
# 1_456 finalized slots on 2026-05-05. The headline number is documented in
# ``solana-plans/calibration/jito_tip_curves.yaml::landing_rate``.
# When a TipQuoteCurve is loaded at runtime its landing_rate field is the
# authoritative value; this constant is the build-in default for callers
# that don't wire the YAML through.
#
# Method: ``1 - reverted_share`` over the captured corpus — an *upper bound*
# on the real Jito-relayer landing probability because pre-leader
# (block-engine) drops are not observable in finalized blocks.
JITO_RELAYER_LANDING_PROB_CALIBRATED: float = 0.78
JITO_RELAYER_CALIBRATION_DATE: str = "2026-05-05"


@dataclass
class SubmissionPathPriors:
    """Per-submission-path landing-probability priors.

    Only ``jito_relayer_landing_prob_baseline`` is calibrated against real
    data (see module docstring). The RPC and TPU/QUIC priors remain
    illustrative because the failure telemetry needed to fit them is not
    visible in finalized chain state.
    """

    rpc_landing_prob_baseline: float = 0.85
    tpu_quic_landing_prob_baseline: float = 0.95
    jito_relayer_landing_prob_baseline: float = JITO_RELAYER_LANDING_PROB_CALIBRATED
    calibrated_at: str | None = JITO_RELAYER_CALIBRATION_DATE
    congestion_penalty_per_pct_full: float = 0.005
