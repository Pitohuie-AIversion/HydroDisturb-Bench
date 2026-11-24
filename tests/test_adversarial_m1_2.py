"""Empirical Adversarial Challenge Suite for Milestone 1 (Features F1, F4, F5).

Role: EMPIRICAL CHALLENGER (critic, specialist)
Features Under Test:
1. Feature F1: Disturbance Inversion Confusion Logic
   - 10,000 randomized cases (active, detected, t_detect variations, missing parameters).
   - Invariant conservation: TP + FP + FN + TN == total across all runs and sub-batches.
   - Zero-division guards and NaN/Inf freedom for precision, recall, and F1.
2. Feature F4: Extruded 3D Force Kinematics
   - Mapped velocity vectors: positive, negative, zero, high (10 m/s), asymmetric directions.
   - Coordinate transform verification: v_x -> x, v_z -> y (spanwise), v_y -> z (crossflow).
   - COMSOL unit tags ([m], [m/s], [s], [kg/m^3], [m/s^2]), syntax parsing, and AST numerical evaluation.
3. Feature F5: Windows File Locking & Resource Management
   - Empirical contrast proving [WinError 32] on unclosed handles on Windows NTFS.
   - 500 rapid create/read/unlink cycles using safe context managers.
   - Multi-threaded concurrent open/read/unlink stress harness.
   - 100 rapid CLI subcommand invocations (observation-sample, reconstruction-eval, vorticity-eval, bundle-create) with immediate file unlinking.
"""

from __future__ import annotations

import concurrent.futures
import json
import math
import os
import re
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.c04_disturbance import (
    C04_DENSITY_KG_M3,
    C04DisturbanceConfig,
    generate_c04_comsol_extruded_xz_force_expressions,
)
from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.disturbance_inversion import (
    ConfusionMetrics,
    DisturbanceGroundTruth,
    DisturbancePrediction,
    InversionMetrics,
    compute_inversion_confusion_metrics,
    evaluate_disturbance_inversion,
    evaluate_inversion_batch,
)

# ==============================================================================
# Feature F1: Disturbance Inversion Confusion Logic Stress Suite
# ==============================================================================


def test_adversarial_f1_10k_conservation_and_div_zero():
    """Adversarial stress test: 10,000 randomized cases verifying TP+FP+FN+TN==total and zero NaNs."""
    rng = np.random.default_rng(42)
    num_cases = 10_000

    evaluations: list[tuple[DisturbanceGroundTruth, DisturbancePrediction]] = []
    oracle_expected_counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}

    for i in range(num_cases):
        truth_active = bool(rng.choice([True, False]))
        t_on = float(rng.uniform(0.0, 100.0))
        duration = float(rng.uniform(0.1, 10.0))
        t_off = t_on + duration

        truth = DisturbanceGroundTruth(
            active=truth_active,
            t_on=t_on,
            t_off=t_off,
            center=(float(rng.uniform(-2, 5)), float(rng.uniform(-2, 2)), float(rng.uniform(0.5, 2.5))),
            amplitude=float(rng.uniform(0.05, 1.0)),
            direction=(float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))),
            velocity=(float(rng.uniform(-0.5, 0.5)), float(rng.uniform(-0.5, 0.5)), float(rng.uniform(-0.5, 0.5)))
            if rng.random() > 0.5
            else None,
        )

        pred_detected = bool(rng.choice([True, False]))

        # Diverse t_detect regimes
        t_mode = rng.choice(["none", "before_on", "at_on", "inside", "at_off", "after_off", "extreme_neg", "extreme_pos"])
        if t_mode == "none":
            t_detect = None
        elif t_mode == "before_on":
            t_detect = t_on - float(rng.uniform(0.01, 10.0))
        elif t_mode == "at_on":
            t_detect = t_on
        elif t_mode == "inside":
            t_detect = t_on + float(rng.uniform(0.0, 1.0)) * duration
        elif t_mode == "at_off":
            t_detect = t_off
        elif t_mode == "after_off":
            t_detect = t_off + float(rng.uniform(0.01, 10.0))
        elif t_mode == "extreme_neg":
            t_detect = -9999.0
        else:  # extreme_pos
            t_detect = 999999.0

        # Parameter presence toggles
        has_center = bool(rng.random() > 0.5)
        has_amplitude = bool(rng.random() > 0.5)
        has_direction = bool(rng.random() > 0.5)
        has_velocity = bool(rng.random() > 0.5)
        has_timing = bool(rng.random() > 0.5)

        pred = DisturbancePrediction(
            detected=pred_detected,
            t_detect=t_detect,
            t_on=t_on + float(rng.uniform(-1.0, 1.0)) if has_timing else None,
            t_off=t_off + float(rng.uniform(-1.0, 1.0)) if has_timing else None,
            center=(float(rng.uniform(-3, 6)), float(rng.uniform(-3, 3))) if has_center else None,
            amplitude=float(rng.uniform(0.0, 2.0)) if has_amplitude else None,
            direction=(float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))) if has_direction else None,
            velocity=(float(rng.uniform(-1, 1)), float(rng.uniform(-1, 1))) if has_velocity else None,
        )

        evaluations.append((truth, pred))

        # Maintain independent oracle count
        if truth_active and pred_detected:
            oracle_expected_counts["tp"] += 1
        elif truth_active and not pred_detected:
            oracle_expected_counts["fn"] += 1
        elif not truth_active and pred_detected:
            oracle_expected_counts["fp"] += 1
        else:
            oracle_expected_counts["tn"] += 1

    # Evaluate individual metric objects and verify mutual exclusivity of classification flags
    all_metrics: list[InversionMetrics] = []
    for truth, pred in evaluations:
        m = evaluate_disturbance_inversion(truth, pred, characteristic_length_D=0.30)
        all_metrics.append(m)

        # Flag exclusivity: exactly one classification must be active
        is_tp = bool(m.detected_correctly and not m.false_alarm)
        is_fp = bool(m.false_alarm)
        is_fn = bool(m.missed_detection)
        is_tn = bool(m.true_negative)

        flag_sum = int(is_tp) + int(is_fp) + int(is_fn) + int(is_tn)
        assert flag_sum == 1, f"Flag conservation violated: TP={is_tp}, FP={is_fp}, FN={is_fn}, TN={is_tn}"

        # Verify composite score is bounded in [0.0, 1.0] and not NaN
        assert not math.isnan(m.composite_inversion_score)
        assert 0.0 <= m.composite_inversion_score <= 1.0

    # Aggregate confusion matrix on the 10,000 cases
    conf: ConfusionMetrics = compute_inversion_confusion_metrics(all_metrics)
    total_classified = conf.true_positives + conf.false_positives + conf.false_negatives + conf.true_negatives

    assert total_classified == num_cases, f"Invariant violated: {total_classified} != {num_cases}"
    assert conf.true_positives == oracle_expected_counts["tp"]
    assert conf.false_positives == oracle_expected_counts["fp"]
    assert conf.false_negatives == oracle_expected_counts["fn"]
    assert conf.true_negatives == oracle_expected_counts["tn"]

    # Full batch evaluation
    batch_res = evaluate_inversion_batch(evaluations, characteristic_length_D=0.30)
    assert batch_res["total_cases"] == num_cases
    assert batch_res["true_positives"] == oracle_expected_counts["tp"]
    assert batch_res["false_positives"] == oracle_expected_counts["fp"]
    assert batch_res["false_negatives"] == oracle_expected_counts["fn"]
    assert batch_res["true_negatives"] == oracle_expected_counts["tn"]

    # Numerical validity of aggregated metrics
    for metric_name in ("precision", "recall", "f1_score", "mean_composite_inversion_score"):
        val = batch_res[metric_name]
        assert not math.isnan(val), f"Metric {metric_name} is NaN"
        assert not math.isinf(val), f"Metric {metric_name} is Inf"
        assert 0.0 <= val <= 1.0, f"Metric {metric_name} = {val} outside [0, 1]"

    # Sub-batch partitioning: random sub-batches with variable sizes (1 to 500)
    shuffled_evals = list(evaluations)
    rng.shuffle(shuffled_evals)
    idx = 0
    sub_batch_count = 0
    while idx < num_cases:
        sub_size = int(rng.integers(1, 501))
        chunk = shuffled_evals[idx : idx + sub_size]
        idx += sub_size
        sub_batch_count += 1

        chunk_res = evaluate_inversion_batch(chunk)
        chunk_total = (
            chunk_res["true_positives"]
            + chunk_res["false_positives"]
            + chunk_res["false_negatives"]
            + chunk_res["true_negatives"]
        )
        assert chunk_total == len(chunk), f"Sub-batch invariant failed: {chunk_total} != {len(chunk)}"
        assert not math.isnan(chunk_res["precision"])
        assert not math.isnan(chunk_res["recall"])
        assert not math.isnan(chunk_res["f1_score"])
        assert 0.0 <= chunk_res["precision"] <= 1.0
        assert 0.0 <= chunk_res["recall"] <= 1.0
        assert 0.0 <= chunk_res["f1_score"] <= 1.0

    print(f"\n[F1 ADVERSARIAL PASS] 10,000 cases + {sub_batch_count} sub-batches strictly conserved!")


def test_adversarial_f1_pathological_boundary_batches():
    """Test extreme boundary cases where denominators would normally be 0."""
    t_true = DisturbanceGroundTruth(active=True, t_on=5.0, t_off=10.0)
    t_false = DisturbanceGroundTruth(active=False)
    p_det = DisturbancePrediction(detected=True, t_detect=6.0)
    p_not = DisturbancePrediction(detected=False)

    # 1. Empty batch
    res_empty = evaluate_inversion_batch([])
    assert res_empty == {"total_cases": 0}

    # 2. 100% True Negatives: tp=0, fp=0, fn=0, tn=100
    res_all_tn = evaluate_inversion_batch([(t_false, p_not)] * 100)
    assert res_all_tn["true_positives"] == 0
    assert res_all_tn["false_positives"] == 0
    assert res_all_tn["false_negatives"] == 0
    assert res_all_tn["true_negatives"] == 100
    assert res_all_tn["precision"] == 1.0
    assert res_all_tn["recall"] == 1.0
    assert res_all_tn["f1_score"] == 1.0

    # 3. 100% False Positives: tp=0, fp=100, fn=0, tn=0
    res_all_fp = evaluate_inversion_batch([(t_false, p_det)] * 100)
    assert res_all_fp["true_positives"] == 0
    assert res_all_fp["false_positives"] == 100
    assert res_all_fp["precision"] == 0.0
    assert res_all_fp["recall"] == 1.0
    assert res_all_fp["f1_score"] == 0.0

    # 4. 100% False Negatives: tp=0, fp=0, fn=100, tn=0
    res_all_fn = evaluate_inversion_batch([(t_true, p_not)] * 100)
    assert res_all_fn["true_positives"] == 0
    assert res_all_fn["false_negatives"] == 100
    assert res_all_fn["precision"] == 1.0
    assert res_all_fn["recall"] == 0.0
    assert res_all_fn["f1_score"] == 0.0

    # 5. Zero True Positives, with FP and FN: tp=0, fp=50, fn=50, tn=0
    res_no_tp = evaluate_inversion_batch([(t_false, p_det)] * 50 + [(t_true, p_not)] * 50)
    assert res_no_tp["true_positives"] == 0
    assert res_no_tp["false_positives"] == 50
    assert res_no_tp["false_negatives"] == 50
    assert res_no_tp["precision"] == 0.0
    assert res_no_tp["recall"] == 0.0
    assert res_no_tp["f1_score"] == 0.0

    # 6. Single TP with no other cases
    res_single_tp = evaluate_inversion_batch([(t_true, p_det)])
    assert res_single_tp["precision"] == 1.0
    assert res_single_tp["recall"] == 1.0
    assert res_single_tp["f1_score"] == 1.0


# ==============================================================================
# Feature F4: Extruded 3D Force Kinematics Stress Suite
# ==============================================================================


def test_adversarial_f4_velocity_permutations_and_mappings():
    """Adversarially probe extruded-xz 3D force expressions across velocity and direction vectors."""
    test_cases = [
        # Stationary baseline
        {
            "name": "stationary",
            "center": (1.0, 0.8, 1.5),
            "velocity": (0.0, 0.0, 0.0),
            "dir": (1.0, 0.0, 0.0),
            "span_offset": 3.0,
            "duration": 1.0,
        },
        # Pure streamwise velocity (vx)
        {
            "name": "pure_vx_positive",
            "center": (0.5, 1.0, 1.5),
            "velocity": (2.5, 0.0, 0.0),
            "dir": (0.0, 1.0, 0.0),
            "span_offset": 3.0,
            "duration": 0.5,
        },
        {
            "name": "pure_vx_negative",
            "center": (3.0, 1.0, 1.5),
            "velocity": (-2.0, 0.0, 0.0),
            "dir": (0.0, 0.0, 1.0),
            "span_offset": 3.0,
            "duration": 0.5,
        },
        # Pure crossflow velocity (vy -> COMSOL z)
        {
            "name": "pure_vy_positive",
            "center": (1.5, -0.5, 1.5),
            "velocity": (0.0, 2.0, 0.0),
            "dir": (1.0, 1.0, 0.0),
            "span_offset": 3.0,
            "duration": 0.5,
        },
        # Pure spanwise velocity (vz -> COMSOL y)
        {
            "name": "pure_vz_negative",
            "center": (1.5, 1.0, 2.5),
            "velocity": (0.0, 0.0, -1.5),
            "dir": (0.0, 1.0, 1.0),
            "span_offset": 3.0,
            "duration": 0.5,
        },
        # High velocity (10 m/s) with tight duration satisfying physical bounds
        {
            "name": "high_10mps_velocity",
            "center": (1.0, 1.5, 1.5),
            "velocity": (10.0, -5.0, 5.0),
            "dir": (3.0, 4.0, 5.0),
            "span_offset": 3.0,
            "duration": 0.1,  # 1.0 m displacement, stays within [-3, 6] x [-3, 3] x [0, 3]
        },
        # Subtle velocity
        {
            "name": "small_subtle_velocity",
            "center": (2.0, 0.8, 1.5),
            "velocity": (0.0001, -0.0002, 0.0003),
            "dir": (-1.0, 2.0, -2.0),
            "span_offset": 1.5,
            "duration": 1.0,
        },
    ]

    for tc in test_cases:
        duration = tc["duration"]
        cfg = C04DisturbanceConfig(
            center_m=tc["center"],
            direction=tc["dir"],
            velocity_m_s=tc["velocity"],
            acceleration_m_s2=0.5,
            sigma_m=0.05,
            on_s=2.0,
            off_s=2.0 + duration,
        )

        exprs = generate_c04_comsol_extruded_xz_force_expressions(cfg, span_offset_m=tc["span_offset"])
        assert set(exprs.keys()) == {"Fx", "Fy", "Fz"}

        vx, vy, vz = tc["velocity"]
        xc, yc, zc = tc["center"]
        y_comsol = zc - tc["span_offset"]
        z_comsol = yc

        dir_norm = math.sqrt(sum(d**2 for d in tc["dir"]))
        ex, ey, ez = [d / dir_norm for d in tc["dir"]]

        for comp_name, comp_expr in exprs.items():
            # 1. Structural bracket and unit syntax validity
            assert comp_expr.count("(") == comp_expr.count(")"), f"Mismatched parens in {comp_name}"
            assert comp_expr.count("[") == comp_expr.count("]"), f"Mismatched brackets in {comp_name}"

            # Check required units
            for unit_tag in ("[kg/m^3]", "[m/s^2]", "[m]", "[s]"):
                assert unit_tag in comp_expr, f"Missing unit {unit_tag} in {comp_name}"

            if any(v != 0.0 for v in tc["velocity"]):
                assert "[m/s]" in comp_expr, f"Expected [m/s] unit in moving pulse {comp_name}"
            else:
                assert "[m/s]" not in comp_expr, f"Did not expect [m/s] in stationary pulse {comp_name}"

            # 2. Coordinate and Velocity Mapping Verification
            # vx -> x coordinate
            if vx != 0.0:
                expected_x_sub = f"({xc:.6f}[m]+{vx:.6f}[m/s]*(t-2.0000[s]))"
                assert expected_x_sub in comp_expr, f"vx mapping incorrect in {comp_name}: expected {expected_x_sub}"
            else:
                assert f"{xc:.6f}[m]" in comp_expr

            # vz -> y coordinate (spanwise in extruded geometry)
            if vz != 0.0:
                expected_y_sub = f"({y_comsol:.6f}[m]+{vz:.6f}[m/s]*(t-2.0000[s]))"
                assert expected_y_sub in comp_expr, f"vz mapping to y incorrect in {comp_name}: expected {expected_y_sub}"
            else:
                assert f"{y_comsol:.6f}[m]" in comp_expr

            # vy -> z coordinate (crossflow in extruded geometry)
            if vy != 0.0:
                expected_z_sub = f"({z_comsol:.6f}[m]+{vy:.6f}[m/s]*(t-2.0000[s]))"
                assert expected_z_sub in comp_expr, f"vy mapping to z incorrect in {comp_name}: expected {expected_z_sub}"
            else:
                assert f"{z_comsol:.6f}[m]" in comp_expr

        # 3. Direction Vector Projection Verification
        # Fx corresponds to canonical ex
        # Fy corresponds to canonical ez (extruded spanwise)
        # Fz corresponds to canonical ey (crossflow)
        assert exprs["Fx"].startswith(f"{ex:.6f}*")
        assert exprs["Fy"].startswith(f"{ez:.6f}*")
        assert exprs["Fz"].startswith(f"{ey:.6f}*")

    print("\n[F4 ADVERSARIAL PASS] All velocity permutations, mappings, and unit strings verified!")


def test_adversarial_f4_comsol_expression_ast_numerical_evaluation():
    """Numerically evaluate the generated COMSOL expressions to verify peak tracking and time windowing."""
    cfg = C04DisturbanceConfig(
        center_m=(1.0, 1.0, 1.5),
        direction=(0.0, 1.0, 0.0),
        velocity_m_s=(1.0, -0.5, 0.5),
        acceleration_m_s2=0.8,
        sigma_m=0.05,
        on_s=2.0,
        off_s=3.0,
    )
    span_offset = 3.0
    exprs = generate_c04_comsol_extruded_xz_force_expressions(cfg, span_offset_m=span_offset)

    # Function to evaluate COMSOL expression string in Python
    def eval_comsol_expr(expr_str: str, x: float, y: float, z: float, t: float) -> float:
        # Strip COMSOL unit tags
        clean = re.sub(r"\[[a-zA-Z0-9\/\^\-_]+\]", "", expr_str)
        clean = clean.replace("^", "**")
        clean = clean.replace("&&", " and ")

        safe_env = {
            "x": x,
            "y": y,
            "z": z,
            "t": t,
            "pi": math.pi,
            "exp": math.exp,
            "sin": math.sin,
            "cos": math.cos,
        }
        clean = re.sub(r"\bif\(", "_comsol_if(", clean)
        safe_env["_comsol_if"] = lambda cond, val_true, val_false: val_true if cond else val_false
        return float(eval(clean, {"__builtins__": {}}, safe_env))

    # Test 1: Before t_on (t = 1.5s) -> must be identically 0.0
    for comp in ("Fx", "Fy", "Fz"):
        val_before = eval_comsol_expr(exprs[comp], 1.0, -1.5, 1.0, 1.5)
        assert val_before == 0.0, f"Expected 0 force before t_on in {comp}, got {val_before}"

    # Test 2: After t_off (t = 3.5s) -> must be identically 0.0
    for comp in ("Fx", "Fy", "Fz"):
        val_after = eval_comsol_expr(exprs[comp], 1.0, -1.5, 1.0, 3.5)
        assert val_after == 0.0, f"Expected 0 force after t_off in {comp}, got {val_after}"

    # Test 3: At mid-pulse (t = 2.5s, half of [2.0, 3.0]):
    # sin(pi * 0.5 / 1.0)^2 = sin(pi/2)^2 = 1.0 (peak envelope)
    t_test = 2.5
    dt = t_test - cfg.on_s  # 0.5 s
    xc_peak = cfg.center_m[0] + cfg.velocity_m_s[0] * dt  # 1.0 + 1.0 * 0.5 = 1.5 m
    yc_peak = (cfg.center_m[2] - span_offset) + cfg.velocity_m_s[2] * dt  # (1.5 - 3.0) + 0.5 * 0.5 = -1.25 m
    zc_peak = cfg.center_m[1] + cfg.velocity_m_s[1] * dt  # 1.0 + (-0.5) * 0.5 = 0.75 m

    # Since direction is (0, 1, 0) canonical:
    # Fx = 0, Fy (canonical z) = 0, Fz (canonical y) = 1.0 * base_f
    fz_peak = eval_comsol_expr(exprs["Fz"], xc_peak, yc_peak, zc_peak, t_test)
    expected_peak = C04_DENSITY_KG_M3 * cfg.acceleration_m_s2 * 1.0 * 1.0
    assert abs(fz_peak - expected_peak) < 1e-3, f"Peak Fz {fz_peak} != expected {expected_peak}"

    # Check Fx and Fy are 0 due to direction
    assert eval_comsol_expr(exprs["Fx"], xc_peak, yc_peak, zc_peak, t_test) == 0.0
    assert eval_comsol_expr(exprs["Fy"], xc_peak, yc_peak, zc_peak, t_test) == 0.0

    # Off-center: at distance 1-sigma, force should drop by exp(-1/2) ~ 0.60653
    fz_1sigma = eval_comsol_expr(exprs["Fz"], xc_peak + cfg.sigma_m, yc_peak, zc_peak, t_test)
    ratio = fz_1sigma / fz_peak
    assert abs(ratio - math.exp(-0.5)) < 1e-4, f"Gaussian spatial profile mismatch: ratio {ratio}"

    print(f"\n[F4 NUMERICAL PASS] COMSOL expressions evaluated accurately at moving pulse center (t={t_test}s)!")


# ==============================================================================
# Feature F5: Windows File Locking & Context Management Stress Suite
# ==============================================================================


def test_adversarial_f5_windows_lock_contrast(tmp_path: Path):
    """Contrast test proving [WinError 32] on unclosed handles on Windows NTFS."""
    leaked_file = tmp_path / "leaked_contrast.npz"
    np.savez(leaked_file, arr=np.zeros((10, 10)))

    # Unclosed np.load handle
    unclosed = np.load(leaked_file, allow_pickle=False)
    _ = unclosed["arr"]

    # Windows enforces mandatory/opportunistic locking on unclosed file handles
    if os.name == "nt":
        with pytest.raises(PermissionError) as exc_info:
            leaked_file.unlink()
        assert "WinError 32" in str(exc_info.value), f"Expected WinError 32 on unclosed handle, got {exc_info.value}"
        print(f"\n[F5 CONTRAST CONFIRMED] Windows unclosed handle properly reproduced {exc_info.value}")

    # Now close handle and unlink cleanly
    unclosed.close()
    leaked_file.unlink()
    assert not leaked_file.exists()


def test_adversarial_f5_direct_500_rapid_create_read_unlink_cycles(tmp_path: Path):
    """Stress test: 500 rapid sequential create/read/unlink cycles with zero leaks."""
    num_cycles = 500
    temp_target = tmp_path / "rapid_cycle.npz"

    for i in range(num_cycles):
        dummy_data = np.full((10, 10), fill_value=i, dtype=np.float32)
        np.savez(temp_target, data=dummy_data)

        # Read with context manager
        with np.load(temp_target, allow_pickle=False) as npz:
            val = float(npz["data"][0, 0])
            assert val == float(i)

        # Immediate unlink
        temp_target.unlink()
        assert not temp_target.exists(), f"File remained present at cycle {i}"

    print(f"\n[F5 RAPID DIRECT PASS] {num_cycles} cycles of create/read/unlink completed with 0 errors!")


def test_adversarial_f5_concurrent_multithreaded_file_locking(tmp_path: Path):
    """Stress test: 10 concurrent worker threads performing rapid open/read/unlink cycles."""
    num_workers = 10
    cycles_per_worker = 20

    def worker_job(worker_id: int) -> int:
        worker_dir = tmp_path / f"worker_{worker_id}"
        worker_dir.mkdir(parents=True, exist_ok=True)
        for c in range(cycles_per_worker):
            fpath = worker_dir / f"test_{c}.npz"
            arr = np.random.randn(8, 8).astype(np.float32)
            np.savez(fpath, arr=arr)

            with np.load(fpath, allow_pickle=False) as npz:
                data = np.asarray(npz["arr"])
                assert data.shape == (8, 8)

            fpath.unlink()
            assert not fpath.exists()
        return cycles_per_worker

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker_job, w) for w in range(num_workers)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert sum(results) == num_workers * cycles_per_worker
    print(f"\n[F5 CONCURRENT PASS] {num_workers} threads x {cycles_per_worker} cycles completed with 0 file lock errors!")


def test_adversarial_f5_cli_rapid_subcommand_cycles(tmp_path: Path):
    """Stress test: 100 rapid CLI subcommand invocations with immediate file unlinking."""
    # 1. cli observation-sample (25 cycles)
    layout_file = tmp_path / "obs_layout.json"
    layout_file.write_text(
        json.dumps({
            "name": "probe_grid",
            "dim": 2,
            "probes": [{"probe_id": "p0", "coords": [0.5, 0.0]}],
        }),
        encoding="utf-8",
    )

    for i in range(25):
        ds_file = tmp_path / f"obs_ds_{i}.npz"
        out_file = tmp_path / f"obs_out_{i}.json"
        data = np.ones((2, 3, 10, 10), dtype=np.float32)
        np.savez(ds_file, data=data, x=np.linspace(0, 1, 10), y=np.linspace(0, 1, 10), channel_names=["u", "v", "p"])

        rc = cli_main(["observation-sample", "--dataset", str(ds_file), "--layout", str(layout_file), "--out", str(out_file)])
        assert rc == 0
        ds_file.unlink()
        out_file.unlink()
        assert not ds_file.exists()

    # 2. cli reconstruction-eval (25 cycles)
    for i in range(25):
        p_file = tmp_path / f"p_{i}.npz"
        t_file = tmp_path / f"t_{i}.npz"
        out_file = tmp_path / f"recon_out_{i}.json"
        field = np.ones((3, 10, 10), dtype=np.float32)
        np.savez(p_file, data=field, channel_names=["u", "v", "p"])
        np.savez(t_file, data=field, channel_names=["u", "v", "p"])

        rc = cli_main(["reconstruction-eval", "--pred", str(p_file), "--true", str(t_file), "--out", str(out_file)])
        assert rc == 0
        p_file.unlink()
        t_file.unlink()
        out_file.unlink()
        assert not p_file.exists()
        assert not t_file.exists()

    # 3. cli vorticity-eval (25 cycles)
    for i in range(25):
        ve_file = tmp_path / f"ve_{i}.npz"
        out_file = tmp_path / f"ve_out_{i}.json"
        data = np.ones((3, 12, 12), dtype=np.float64)
        np.savez(ve_file, data=data, x=np.linspace(0, 1, 12), y=np.linspace(0, 1, 12))

        rc = cli_main(["vorticity-eval", "--dataset", str(ve_file), "--out", str(out_file)])
        assert rc == 0
        ve_file.unlink()
        out_file.unlink()
        assert not ve_file.exists()

    # 4. cli bundle-create (25 cycles)
    for i in range(25):
        bc_file = tmp_path / f"bc_{i}.npz"
        out_bundle = tmp_path / f"bundle_{i}.npz"
        times = np.array([0.0, 0.1, 0.2])
        data = np.ones((3, 3, 8, 8), dtype=np.float32)
        np.savez(bc_file, times=times, data=data, x=np.linspace(0, 1, 8), y=np.linspace(0, 1, 8))

        rc = cli_main(["bundle-create", "--dataset", str(bc_file), "--out", str(out_bundle)])
        assert rc == 0
        bc_file.unlink()
        out_bundle.unlink()
        assert not bc_file.exists()
        assert not out_bundle.exists()

    layout_file.unlink()
    print("\n[F5 CLI RAPID PASS] 100 CLI executions across 4 subcommands cleanly released all locks!")
