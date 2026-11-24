"""3D ROV ducted propeller multiphysics evaluation and baseline verification."""

from .baseline import (
    DEFAULT_CAD_PATH,
    DEFAULT_CCW_MODEL,
    DEFAULT_CW_MODEL,
    DEFAULT_OUT_DIR,
    FROZEN_SHA256,
    REFERENCE_KPIS,
    reproduce_propeller_baselines,
    verify_baseline_kpis,
)

__all__ = [
    "DEFAULT_CAD_PATH",
    "DEFAULT_CCW_MODEL",
    "DEFAULT_CW_MODEL",
    "DEFAULT_OUT_DIR",
    "FROZEN_SHA256",
    "REFERENCE_KPIS",
    "reproduce_propeller_baselines",
    "verify_baseline_kpis",
]
