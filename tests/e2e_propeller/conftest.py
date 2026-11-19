"""Shared fixtures, markers, baseline constants, and opaque-box contracts for Propeller E2E.

All tests in ``tests/e2e_propeller/`` are strictly requirement-driven and opaque-box:
they interact only with public contracts from ``ORIGINAL_REQUEST.md``, ``PROJECT.md``,
and ``TEST_INFRA.md`` — never internal implementation details.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from hydro_disturb_bench.comsol_java.bridge import (
    ComsolConnectionError,
    compute_file_hash,
    find_comsol_root,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE_DIR = REPO_ROOT / "models" / "prop test2"
CW_BASELINE_MPH = BASELINE_DIR / "COMSOL_CW_1400rpm" / "V10f-StaticFine-1400rpm.mph"
CCW_BASELINE_MPH = BASELINE_DIR / "COMSOL_CCW_1400rpm" / "V10f-CCWFine-1400rpm.mph"
BASELINE_XT = BASELINE_DIR / "3_320kgf_v10f.x_t"
CONFIG_PATH = REPO_ROOT / "configs" / "propeller_opt_v10f.json"
OPT_SCRIPT_PATH = REPO_ROOT / "scripts" / "run_propeller_optimization.py"

# Frozen baseline physical constants & SHA-256 digests (PROJECT.md § Interface Contracts 3)
BASELINE_CONSTANTS: dict[str, Any] = {
    "cw_mph_rel": "models/prop test2/COMSOL_CW_1400rpm/V10f-StaticFine-1400rpm.mph",
    "ccw_mph_rel": "models/prop test2/COMSOL_CCW_1400rpm/V10f-CCWFine-1400rpm.mph",
    "xt_rel": "models/prop test2/3_320kgf_v10f.x_t",
    "cw_sha256": "C8968A46A4BE1398AAAB377CCA21CE1BF6E581CFACE1710774B676E5D04E41D4",
    "ccw_sha256": "194072E12EF86E433B3F857257A8EAD598BEF82E17E1403D547BE247178B1205",
    "ccw_kpi": {
        "speed_rpm": -1400.0,
        "T_N": 2475.37,
        "F_rotor_x": -1387.10,
        "F_duct_x": -1088.27,
        "torque_nm": 84.07,
        "P_kw": 12.33,
        "T_over_P": 200.84,
        "p_min_kpa": -84.76,
        "cav_area_pct": 0.0,
        "upstream": [1],
        "open": [2, 3, 29],
    },
    "cw_kpi": {
        "speed_rpm": 1400.0,
        "T_N": 1262.36,
        "P_kw": 8.08,
        "T_over_P": 156.25,
        "p_min_kpa": -214.44,
        "cav_area_pct": 0.519,
        "upstream": [29],
        "open": [1, 2, 3],
    },
    "eta_sym": 0.5100,
    "T_mean_N": 1868.865,
    "T_over_P_mean": 178.545,
    "bpf_hz_1400": 70.0,
    "warm_start_recipe": {
        "segcflaa": "off",
        "submincfl": 500,
        "subdamp": 0.35,
        "stol": 0.0025,
    },
}

# Non-negotiable hard geometric, mounting & constraint invariants (PROJECT.md § Interface Contracts 1 & 3)
HARD_INVARIANTS: dict[str, Any] = {
    "D_prop_m": 0.320,
    "R_prop_m": 0.160,
    "radius_tol_m": 1e-4,
    "diameter_tol_m": 2e-4,
    "D_hub_m": 0.084,
    "R_hub_m": 0.042,
    "hub_x_min_m": 0.065,
    "hub_x_max_m": 0.130,
    "duct_x_min_m": 0.009966,
    "duct_x_max_m": 0.202606,
    "duct_outer_max_d_m": 0.388856,
    "duct_outer_max_r_m": 0.194428,
    "rotorflow_r_m": 0.1625,
    "rotorflow_x_min_m": 0.055,
    "rotorflow_x_max_m": 0.140,
    "blade_x_min_m": 0.056,
    "blade_x_max_m": 0.139,
    "tip_clearance_min_m": 0.0015,
    "tip_clearance_max_m": 0.0060,
    "blade_count": 3,
    "min_te_thickness_m": 0.0003,
    "eta_sym_floor": 0.75,
    "p_min_cav_kpa": -99.0,
    "mass_imbalance_max_pct": 1.0,
    "solver_rel_err_max": 0.0025,
    "y_plus_min": 11.0,
    "y_plus_max": 300.0,
    "momentum_closure_max_pct": 5.0,
    "modal_bpf_sep_min": 0.15,
    "stress_safety_factor_min": 2.0,
    "forbidden_dof_names": (
        "diameter",
        "D_prop",
        "d_prop_m",
        "r_prop_m",
        "R_prop",
        "hub_radius",
        "d_hub_m",
        "hub_x_min",
        "hub_x_max",
        "duct_outer_max_r",
        "duct_x_min",
        "duct_x_max",
    ),
    "free_dof_names": (
        "pitch_r04_deg",
        "pitch_r07_deg",
        "pitch_r10_deg",
        "chord_r04_m",
        "chord_r07_m",
        "chord_r10_m",
        "max_camber_ratio",
        "max_thickness_ratio",
        "skew_tip_deg",
        "rake_tip_m",
        "tip_clearance_m",
        "duct_inlet_lip_ratio",
        "duct_diffuser_angle_deg",
    ),
}


def pytest_configure(config: pytest.Config) -> None:
    """Register tier1-tier5 markers so pytest --strict-markers passes cleanly."""
    config.addinivalue_line("markers", "tier1: Tier 1 Propeller Feature Coverage E2E test")
    config.addinivalue_line("markers", "tier2: Tier 2 Propeller Boundary & Negative E2E test")
    config.addinivalue_line("markers", "tier3: Tier 3 Propeller Pairwise Interaction E2E test")
    config.addinivalue_line("markers", "tier4: Tier 4 Propeller Real-World ROV Scenario E2E test")
    config.addinivalue_line("markers", "tier5: Tier 5 Propeller Adversarial Hardening E2E test")


def require_contract(
    module_name: str,
    required_attrs: tuple[str, ...] = (),
    *,
    allow_pending_attrs: bool = False,
) -> Any:
    """Load an implementation module via importlib without silent false-passes.

    - If ``module_name`` is not yet present (``find_spec is None``): skips unless
      ``PROPELLER_E2E_REQUIRE_COMPLETE=1``, in which case it fails hard.
    - If ``module_name`` exists: imports it directly without swallowing
      ``ImportError`` or ``SyntaxError``, then verifies every symbol in
      ``required_attrs`` exists (failing if missing unless ``allow_pending_attrs``
      is True and ``PROPELLER_E2E_REQUIRE_COMPLETE != "1"``).
    """
    require_complete = os.environ.get("PROPELLER_E2E_REQUIRE_COMPLETE") == "1"
    parts = module_name.split(".")
    spec = None
    for idx in range(1, len(parts) + 1):
        prefix = ".".join(parts[:idx])
        spec = importlib.util.find_spec(prefix)
        if spec is None:
            break
    if spec is None:
        if require_complete:
            pytest.fail(f"Required module missing under completeness gate: {module_name}")
        pytest.skip(f"Milestone module not yet merged: {module_name}")

    mod = importlib.import_module(module_name)
    missing = [name for name in required_attrs if not hasattr(mod, name)]
    if missing:
        if allow_pending_attrs and not require_complete:
            pytest.skip(f"Pending milestone extension in {module_name}: {missing}")
        pytest.fail(f"{module_name} missing required contract symbols: {missing}")
    return mod


def require_repo_artifact(rel_path: str) -> Path:
    """Require an M4 config or script artifact, skipping only if not yet merged."""
    target = REPO_ROOT / rel_path
    if not target.is_file():
        if os.environ.get("PROPELLER_E2E_REQUIRE_COMPLETE") == "1":
            pytest.fail(f"Required artifact missing under completeness gate: {rel_path}")
        pytest.skip(f"Milestone artifact not yet merged: {rel_path}")
    return target


def resolve_contract_attr(mod: Any, candidates: tuple[str, ...], role_desc: str) -> Any:
    """Resolve one of the expected public contract symbols or fail loudly."""
    for name in candidates:
        if hasattr(mod, name):
            return getattr(mod, name)
    pytest.fail(f"{mod.__name__} missing expected {role_desc} symbol (tried {candidates})")


@pytest.fixture(scope="session")
def baseline_constants() -> dict[str, Any]:
    """Session-scoped frozen baseline constants."""
    return BASELINE_CONSTANTS


@pytest.fixture(scope="session")
def hard_invariants() -> dict[str, Any]:
    """Session-scoped hard geometric & mounting invariants."""
    return HARD_INVARIANTS


@pytest.fixture
def nominal_design_dict() -> dict[str, float]:
    """Nominal valid 13-DOF PropellerDesignVector dictionary."""
    return {
        "pitch_r04_deg": 28.0,
        "pitch_r07_deg": 22.0,
        "pitch_r10_deg": 17.5,
        "chord_r04_m": 0.058,
        "chord_r07_m": 0.072,
        "chord_r10_m": 0.048,
        "max_camber_ratio": 0.012,
        "max_thickness_ratio": 0.085,
        "skew_tip_deg": 14.0,
        "rake_tip_m": 0.004,
        "tip_clearance_m": 0.0030,
        "duct_inlet_lip_ratio": 1.12,
        "duct_diffuser_angle_deg": 6.5,
    }


@dataclass
class CalibratedROVOracle:
    """Deterministic blade-element/ducted-thruster physics oracle anchored at baseline KPIs.

    When called with ``baseline=True`` (or ``max_camber_ratio=0.042, tip_clearance_m=0.00495``
    reference), reproduces ``models/prop test2`` baseline KPIs accurately and responds
    monotonically/physically to pitch, chord, camber, thickness, skew, tip clearance,
    advance ratio ``J``, lateral crossflow ``U_lat``, and operating depth ``h``.
    """

    rho: float = 1025.0
    g: float = 9.81
    p_atm: float = 101325.0
    p_v: float = 2338.0
    sigma_y_pa: float = 276e6  # Al 6061-T6 yield strength

    def evaluate(
        self,
        design: dict[str, float],
        *,
        case_id: str = "oracle_case",
        speed_rpm: float = 1400.0,
        advance_j: float = 0.0,
        u_lat_mps: float = 0.0,
        depth_m: float = 0.0,
        baseline_anchor: bool = False,
    ) -> dict[str, Any]:
        d_prop = HARD_INVARIANTS["D_prop_m"]
        r_prop = HARD_INVARIANTS["R_prop_m"]
        n_rps = abs(speed_rpm) / 60.0

        if baseline_anchor:
            t_ccw = 2475.37
            p_ccw_kw = 12.33
            t_cw = 1262.36
            p_cw_kw = 8.08
            p_min_ccw_kpa = -84.76
            p_min_cw_kpa = -214.44
            cav_area_cw_pct = 0.519
            cav_area_ccw_pct = 0.0
        else:
            camber = float(design.get("max_camber_ratio", 0.012))
            pitch_mean = (
                float(design.get("pitch_r04_deg", 28.0))
                + float(design.get("pitch_r07_deg", 22.0))
                + float(design.get("pitch_r10_deg", 17.5))
            ) / 3.0
            chord_mean = (
                float(design.get("chord_r04_m", 0.058))
                + float(design.get("chord_r07_m", 0.072))
                + float(design.get("chord_r10_m", 0.048))
            ) / 3.0
            tip_gap = float(design.get("tip_clearance_m", 0.003))
            skew = float(design.get("skew_tip_deg", 14.0))
            lip_ratio = float(design.get("duct_inlet_lip_ratio", 1.12))
            diffuser = float(design.get("duct_diffuser_angle_deg", 6.5))

            pitch_factor = 1.0 + 0.018 * (pitch_mean - 22.0)
            chord_factor = 1.0 + 3.0 * (chord_mean - 0.060)
            gap_penalty = max(0.0, (tip_gap - 0.0015) * 28.0)
            lip_bonus = 1.0 + 0.06 * (lip_ratio - 1.08) - 0.003 * abs(diffuser - 6.5)

            base_sym_thrust = 2120.0 * pitch_factor * chord_factor * lip_bonus * (1.0 - gap_penalty)
            asymmetry = min(0.45, camber * 10.0)
            t_ccw = max(400.0, base_sym_thrust * (1.0 + 0.35 * asymmetry))
            t_cw = max(300.0, base_sym_thrust * (1.0 - 0.85 * asymmetry))

            eff_factor = max(0.65, 1.0 - 0.5 * gap_penalty + 0.002 * skew - 0.004 * abs(pitch_mean - 22.0))
            p_ccw_kw = max(1.0, t_ccw / (205.0 * eff_factor))
            p_cw_kw = max(1.0, t_cw / (196.0 * eff_factor * (1.0 - 0.35 * asymmetry)))

            suction_reduction = max(0.0, (0.045 - camber) * 2600.0) + max(0.0, (skew - 8.0) * 1.1)
            p_min_ccw_kpa = min(-25.0, -84.76 + 0.15 * suction_reduction - 2.0 * max(0.0, pitch_mean - 24.0))
            p_min_cw_kpa = min(-25.0, -214.44 + suction_reduction + 80.0 * max(0.0, 0.015 - camber))
            cav_area_ccw_pct = 0.0 if p_min_ccw_kpa >= -99.0 else round((-99.0 - p_min_ccw_kpa) * 0.005, 4)
            cav_area_cw_pct = 0.0 if p_min_cw_kpa >= -99.0 else round((-99.0 - p_min_cw_kpa) * 0.0045, 4)

        eta_sym = min(t_cw, t_ccw) / max(t_cw, t_ccw)
        t_mean = 0.5 * (t_ccw + t_cw)
        tp_ccw = t_ccw / p_ccw_kw
        tp_cw = t_cw / p_cw_kw
        tp_mean = 0.5 * (tp_ccw + tp_cw)

        # Open-water DP curve at advance ratio J
        u_a = advance_j * n_rps * d_prop
        t_dp = max(0.0, t_mean * max(0.05, 1.0 - 0.85 * advance_j))
        p_dp_kw = max(0.5, 0.5 * (p_ccw_kw + p_cw_kw) * (1.0 - 0.35 * advance_j))
        q_dp = (p_dp_kw * 1000.0) / (2.0 * math.pi * n_rps)
        k_t = t_dp / (self.rho * (n_rps**2) * (d_prop**4))
        k_q = q_dp / (self.rho * (n_rps**2) * (d_prop**5))
        eta_0 = (advance_j / (2.0 * math.pi)) * (k_t / k_q) if advance_j > 0.0 else 0.0

        # Hover crossflow disturbance sensitivity
        skew = float(design.get("skew_tip_deg", 14.0))
        tip_gap = float(design.get("tip_clearance_m", 0.003))
        if baseline_anchor:
            delta_t_frac = 0.085
            f_yz_n = 145.0
        else:
            delta_t_frac = max(0.012, 0.075 - 0.0018 * skew + 3.0 * (tip_gap - 0.0015)) * (
                1.0 + 0.8 * u_lat_mps
            )
            f_yz_n = max(12.0, t_mean * (0.065 - 0.0012 * skew) * max(0.25, u_lat_mps / 0.5))
        s_dist = delta_t_frac + 0.5 * (f_yz_n / t_mean)

        # Cavitation depth margin
        p_amb = self.p_atm + self.rho * self.g * depth_m
        v_tip = math.pi * n_rps * d_prop
        q_dyn = 0.5 * self.rho * (v_tip**2)
        sigma = (p_amb - self.p_v) / q_dyn
        worst_p_min_pa = min(p_min_cw_kpa, p_min_ccw_kpa) * 1000.0
        c_p_min = worst_p_min_pa / q_dyn
        cav_margin = sigma + c_p_min

        # Structural / wet modal / acoustics (calibrated analytical)
        thick = float(design.get("max_thickness_ratio", 0.085))
        chord_07 = float(design.get("chord_r07_m", 0.072))
        load_max = max(t_ccw, t_cw)
        sigma_vm_pa = (load_max / 3.0) * 0.08 / max(1e-7, (chord_07 * (thick**2))) * 22.0
        fatigue_sf = self.sigma_y_pa / max(1.0, sigma_vm_pa)
        wet_f1_hz = 62.0 + 680.0 * thick + 180.0 * (chord_07 - 0.04)
        bpf_hz = 3.0 * n_rps
        min_bpf_sep = min(abs(wet_f1_hz - m * bpf_hz) / wet_f1_hz for m in (1, 2, 3))
        spl_proxy = 128.0 - 0.35 * skew + 950.0 * (tip_gap - 0.0015) + 0.004 * load_max

        return {
            "case_id": case_id,
            "status": "SUCCESS",
            "geometry_audit": {
                "is_valid": True,
                "d_prop_m": d_prop,
                "r_prop_m": r_prop,
                "d_hub_m": HARD_INVARIANTS["D_hub_m"],
                "hub_x_span": [HARD_INVARIANTS["hub_x_min_m"], HARD_INVARIANTS["hub_x_max_m"]],
                "duct_outer_max_d_m": HARD_INVARIANTS["duct_outer_max_d_m"] - 0.0002,
                "duct_x_span": [HARD_INVARIANTS["duct_x_min_m"], HARD_INVARIANTS["duct_x_max_m"]],
                "tip_clearance_m": tip_gap,
                "blade_count": HARD_INVARIANTS["blade_count"],
            },
            "cfd_ccw": {
                "speed_rpm": -abs(speed_rpm),
                "thrust_n": round(t_ccw, 3),
                "power_kw": round(p_ccw_kw, 4),
                "t_over_p": round(tp_ccw, 3),
                "mass_imbalance_pct": 0.21,
                "rel_err": 0.0011,
                "y_plus_imp": 38.1,
                "y_plus_duct": 13.5,
            },
            "cfd_cw": {
                "speed_rpm": abs(speed_rpm),
                "thrust_n": round(t_cw, 3),
                "power_kw": round(p_cw_kw, 4),
                "t_over_p": round(tp_cw, 3),
                "mass_imbalance_pct": 0.24,
                "rel_err": 0.0014,
                "y_plus_imp": 34.8,
                "y_plus_duct": 14.2,
            },
            "t_mean_n": round(t_mean, 3),
            "t_over_p_mean": round(tp_mean, 3),
            "eta_sym": round(eta_sym, 4),
            "dp_maneuver": {
                "J": advance_j,
                "U_a_mps": round(u_a, 4),
                "K_T": round(k_t, 5),
                "K_Q": round(k_q, 6),
                "eta_0": round(eta_0, 5),
            },
            "hover_disturbance": {
                "u_lat_mps": u_lat_mps,
                "delta_t_rel": round(delta_t_frac, 5),
                "f_yz_n": round(f_yz_n, 3),
                "m_yz_nm": round(f_yz_n * 0.04, 3),
                "s_dist": round(s_dist, 5),
            },
            "cavitation": {
                "depth_m": depth_m,
                "p_min_ccw_kpa": round(p_min_ccw_kpa, 3),
                "p_min_cw_kpa": round(p_min_cw_kpa, 3),
                "worst_p_min_kpa": round(min(p_min_ccw_kpa, p_min_cw_kpa), 3),
                "cav_area_ccw_pct": cav_area_ccw_pct,
                "cav_area_cw_pct": cav_area_cw_pct,
                "sigma": round(sigma, 4),
                "c_p_min": round(c_p_min, 4),
                "margin": round(cav_margin, 4),
            },
            "structural_modal": {
                "fidelity": "calibrated_analytical",
                "sigma_vm_max_pa": round(sigma_vm_pa, 1),
                "sigma_y_pa": self.sigma_y_pa,
                "fatigue_sf": round(fatigue_sf, 3),
                "wet_f1_hz": round(wet_f1_hz, 3),
                "bpf_hz": round(bpf_hz, 3),
                "min_bpf_sep": round(min_bpf_sep, 4),
                "spl_proxy_db": round(spl_proxy, 3),
            },
            "fidelity": "calibrated_analytical",
            "wall_time_s": 0.02,
            "error_code": None,
        }


@pytest.fixture(scope="session")
def calibrated_rov_oracle() -> CalibratedROVOracle:
    """Session-scoped deterministic physics oracle anchored at baseline KPIs."""
    return CalibratedROVOracle()


@pytest.fixture
def mock_comsol_propeller_subprocess(tmp_path: Path):
    """Factory returning a deterministic subprocess.run mock for PropellerBridge IPC."""

    def _factory(
        *,
        payload: dict[str, Any] | None = None,
        raw_stdout: str | None = None,
        returncode: int = 0,
        raise_timeout: bool = False,
        create_staging_mph: bool = True,
    ):
        calls: list[dict[str, Any]] = []

        def _fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append({"cmd": list(cmd), "kwargs": dict(kwargs)})
            if raise_timeout:
                raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 60))

            if create_staging_mph:
                staging_file = tmp_path / "staging" / f"case_{len(calls)}.mph"
                staging_file.parent.mkdir(parents=True, exist_ok=True)
                staging_file.write_bytes(b"MOCK_SOLVED_PROPELLER_MPH_V64")

            if raw_stdout is not None:
                stdout_str = raw_stdout
            else:
                resp = payload or {
                    "status": "SUCCESS",
                    "sdim": 3,
                    "rot1": {"selection": "moving", "domains": [2]},
                    "cpl": ["intR", "intD", "intFar"],
                    "summary": {
                        "F_rotor_x": -1387.10,
                        "F_duct_x": -1088.27,
                        "T_total": 2475.37,
                        "torque_nm": 84.07,
                        "power_kw": 12.33,
                    },
                }
                stdout_str = f"COMSOL LOG\n<<<JSON_START>>>\n{json.dumps(resp)}\n<<<JSON_END>>>\n"

            return subprocess.CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=stdout_str,
                stderr="",
            )

        _fake_run.calls = calls  # type: ignore[attr-defined]
        return _fake_run

    return _factory


@pytest.fixture
def unicode_space_tmp_dir(tmp_path: Path) -> dict[str, Any]:
    """Create a Unicode + emoji + space temp directory mimicking models/prop test2."""
    work_dir = tmp_path / "📁 模型 prop test2"
    work_dir.mkdir(parents=True, exist_ok=True)
    mph_file = work_dir / "V10f-Mock-1400rpm.mph"
    mph_file.write_bytes(b"MOCK_PROPELLER_BASELINE_MPH_PAYLOAD_001")
    lock_file = work_dir / "V10f-Mock-1400rpm.mph.lock"
    lock_file.write_text("Administrator@LOCALHOST 12345\n", encoding="utf-8")
    return {
        "dir": work_dir,
        "mph": mph_file,
        "lock": lock_file,
        "sha256": compute_file_hash(mph_file),
    }


@pytest.fixture
def live_comsol_env():
    """Live COMSOL 6.4 + baseline models guard for @pytest.mark.integration tests."""
    try:
        comsol_root = find_comsol_root()
    except ComsolConnectionError as exc:
        if os.environ.get("PROPELLER_E2E_REQUIRE_LIVE") == "1":
            pytest.fail(f"COMSOL 6.4 missing under live integration gate: {exc}")
        pytest.skip(f"COMSOL 6.4 not available: {exc}")

    for req_file in (CW_BASELINE_MPH, CCW_BASELINE_MPH, BASELINE_XT):
        if not req_file.is_file():
            if os.environ.get("PROPELLER_E2E_REQUIRE_LIVE") == "1":
                pytest.fail(f"Baseline model missing under live integration gate: {req_file}")
            pytest.skip(f"Baseline model missing: {req_file}")

    cw_pre_sha = compute_file_hash(CW_BASELINE_MPH)
    ccw_pre_sha = compute_file_hash(CCW_BASELINE_MPH)
    staging_root = REPO_ROOT / "runs" / "propeller_opt" / "e2e_integration"
    staging_root.mkdir(parents=True, exist_ok=True)

    yield {
        "comsol_root": comsol_root,
        "cw_mph": CW_BASELINE_MPH,
        "ccw_mph": CCW_BASELINE_MPH,
        "xt_path": BASELINE_XT,
        "cw_pre_sha": cw_pre_sha,
        "ccw_pre_sha": ccw_pre_sha,
        "staging_root": staging_root,
    }

    assert compute_file_hash(CW_BASELINE_MPH) == cw_pre_sha, "CW baseline .mph mutated!"
    assert compute_file_hash(CCW_BASELINE_MPH) == ccw_pre_sha, "CCW baseline .mph mutated!"
    # Cleanup any scratch files in staging_root while preserving runs/ directory
    for leftover in staging_root.glob("tmp_e2e_*"):
        if leftover.is_dir():
            shutil.rmtree(leftover, ignore_errors=True)
