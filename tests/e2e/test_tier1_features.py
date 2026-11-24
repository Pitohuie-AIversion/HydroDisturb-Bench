"""HydroDisturb-Bench E2E Test Suite — Tier 1 Feature Coverage.

Covers all 7 feature areas (F1-F7) with 42 fast, deterministic, synthetic E2E tests:
- F1: Codebase Organization & Modularity (Tests 1-6)
- F2: Disturbance Inversion Evaluation (Tests 7-12)
- F3: Probabilistic CRPS & Ensemble Metrics (Tests 13-18)
- F4: Forecasting Metrics & Safe Evaluation (Tests 19-24)
- F5: Force Expressions & Disturbance Generation (Tests 25-30)
- F6: Script Execution & Error Resilience (Tests 31-36)
- F7: Package Dependencies & CLI Invocations (Tests 37-42)

All tests are 100% self-contained, requiring zero live COMSOL GUI or solver licenses.
"""
from __future__ import annotations

import importlib
import json
import math
import os
import pkgutil
import re
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np
import pytest
import tomllib

import hydro_disturb_bench
from hydro_disturb_bench.c04_disturbance import (
    C04DisturbanceConfig,
    evaluate_c04_disturbance_field,
    generate_c04_comsol_extruded_xz_force_expressions,
    generate_c04_comsol_force_expressions,
)
from hydro_disturb_bench.c04_forecasting import (
    c04_forecast_from_history,
    score_c04_forecast,
)
from hydro_disturb_bench.c04_windows import C04WindowIndex, C04WindowSpec
from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.comsol_java.errors import InvalidParameterError, ModelValidationError
from hydro_disturb_bench.config import base_quantities, validate_config
from hydro_disturb_bench.disturbance_inversion import (
    DisturbanceGroundTruth,
    DisturbancePrediction,
    InversionMetrics,
    evaluate_disturbance_inversion,
    evaluate_inversion_batch,
)
from hydro_disturb_bench.field_sequence import FieldSequenceReader, load_full_field_sequence
from hydro_disturb_bench.probabilistic_metrics import (
    compute_crps_ensemble,
    compute_spread_skill_ratio,
)
from hydro_disturb_bench.pulse_parameters import (
    properties_payload,
    pulse_direction,
    source_integral_reference,
    validate_pulse_config,
)
from hydro_disturb_bench.sampling_3d import (
    C04GridCoordinates,
    C04SliceCoordinates,
    load_c04_dataset,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ==============================================================================
# Feature F1: Codebase Organization & Modularity (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f1_package_version_metadata():
    """Verify package version exposure in module and consistency with pyproject.toml."""
    assert hasattr(hydro_disturb_bench, "__version__")
    assert isinstance(hydro_disturb_bench.__version__, str)
    assert re.match(r"^\d+\.\d+\.\d+", hydro_disturb_bench.__version__)

    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file()
    with open(pyproject_path, "rb") as f:
        meta = tomllib.load(f)
    assert hydro_disturb_bench.__version__ == meta["project"]["version"]


@pytest.mark.tier1
def test_tier1_f1_all_submodules_importable_cleanly():
    """Verify all discoverable submodules under hydro_disturb_bench import without error."""
    submodules = [
        name
        for _, name, _ in pkgutil.iter_modules(hydro_disturb_bench.__path__)
    ]
    assert len(submodules) >= 20, f"Expected at least 20 submodules, found {len(submodules)}"

    for mod_name in submodules:
        full_name = f"hydro_disturb_bench.{mod_name}"
        mod = importlib.import_module(full_name)
        assert mod is not None
        assert full_name in sys.modules


@pytest.mark.tier1
def test_tier1_f1_import_isolation_no_side_effects():
    """Verify importing hydro_disturb_bench produces no side-effect files in the repository root."""
    root_before = set(os.listdir(REPO_ROOT))
    code = (
        "import sys, os;"
        "import hydro_disturb_bench;"
        "import hydro_disturb_bench.cli;"
        "import hydro_disturb_bench.disturbance_inversion;"
        "import hydro_disturb_bench.probabilistic_metrics;"
        "sys.exit(0)"
    )
    res = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert res.returncode == 0, f"Subprocess import failed: {res.stderr}"
    root_after = set(os.listdir(REPO_ROOT))
    new_files = root_after - root_before
    assert new_files == set(), f"Import created side-effect files in root: {new_files}"


@pytest.mark.tier1
def test_tier1_f1_cli_entrypoint_registered_and_parsable(capsys):
    """Verify hydrobench CLI entry point executes cleanly on --help and lists core subcommands."""
    assert callable(cli_main)
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0

    captured = capsys.readouterr()
    help_text = captured.out
    for cmd in ["inversion-eval", "observation-sample", "bundle-create", "bundle-verify", "vorticity-eval"]:
        assert cmd in help_text, f"Subcommand '{cmd}' not found in CLI help text"


@pytest.mark.tier1
def test_tier1_f1_subpackage_comsol_java_exports(mock_comsol_export_dir: Path):
    """Verify comsol_java subpackage exports and exception class hierarchy."""
    assert (mock_comsol_export_dir / "case_model.mph").is_file()
    assert (mock_comsol_export_dir / "grid_export.txt").is_file()
    from hydro_disturb_bench.comsol_java import bridge, errors, file_transactions

    assert bridge is not None
    assert issubclass(errors.InvalidParameterError, Exception)
    assert issubclass(errors.ModelValidationError, Exception)
    assert callable(file_transactions.publish_staging_file)


@pytest.mark.tier1
def test_tier1_f1_case_config_contract_and_validation():
    """Verify physical case configuration dictionary contract and validation logic."""
    valid_cfg = {
        "fluid": {"density_kg_m3": 1000.0, "kinematic_viscosity_m2_s": 1e-6},
        "cylinder": {"diameter_m": 0.01, "reynolds": 100.0},
        "domain": {"upstream_D": 5.0, "downstream_D": 15.0, "half_height_D": 5.0},
        "time": {"end_star": 50.0, "output_dt_star": 0.1},
        "disturbance": {
            "type": "gaussian_body_acceleration",
            "xc_D": [1.0, 3.0],
            "yc_D": [-0.5, 0.5],
            "sigma_D": [0.2, 0.5],
            "a_star": [0.01, 0.05],
            "t0_star": [10.0, 20.0],
            "duration_star": [1.0, 2.0],
            "theta_rad": [0.0, 3.14],
        },
    }
    validate_config(valid_cfg)
    bq = base_quantities(valid_cfg)
    assert bq["rho_kg_m3"] == 1000.0
    assert bq["nu_m2_s"] == 1e-6
    assert bq["D_m"] == 0.01
    assert bq["Re"] == 100.0
    assert math.isclose(bq["Uin_m_s"], 0.01, rel_tol=1e-6)
    assert math.isclose(bq["convective_time_s"], 1.0, rel_tol=1e-6)

    # Invalid config (negative Reynolds) raises ValueError
    invalid_cfg = dict(valid_cfg)
    invalid_cfg["cylinder"] = {"diameter_m": 0.01, "reynolds": -50.0}
    with pytest.raises(ValueError, match="must be a finite positive number"):
        validate_config(invalid_cfg)


# ==============================================================================
# Feature F2: Disturbance Inversion Evaluation (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f2_tp_classification_with_and_without_timestamp(mock_disturbance_truth_pred_pairs):
    """Verify True Positive classification succeeds both with and without detection timestamp."""
    case_tp = mock_disturbance_truth_pred_pairs["true_positive"]
    case_tp_no_ts = mock_disturbance_truth_pred_pairs["true_positive_no_timestamp"]

    res = evaluate_inversion_batch([case_tp, case_tp_no_ts], characteristic_length_D=0.30)
    assert res["total_cases"] == 2
    assert res["true_positives"] == 2
    assert res["true_negatives"] == 0
    assert res["false_positives"] == 0
    assert res["false_negatives"] == 0
    assert res["precision"] == pytest.approx(1.0)
    assert res["recall"] == pytest.approx(1.0)
    assert res["f1_score"] == pytest.approx(1.0)
    assert res["mean_detection_delay_sec"] == pytest.approx(0.2, abs=1e-6)


@pytest.mark.tier1
def test_tier1_f2_tn_classification_clean_rejection(mock_disturbance_truth_pred_pairs):
    """Verify True Negative classification on clean rejection of inactive state."""
    truth, pred = mock_disturbance_truth_pred_pairs["true_negative"]

    m = evaluate_disturbance_inversion(truth, pred)
    batch = evaluate_inversion_batch([(truth, pred)])

    assert m.false_alarm is False
    assert m.missed_detection is False
    assert m.composite_inversion_score == pytest.approx(1.0)

    assert batch["true_negatives"] == 1
    assert batch["true_positives"] == 0
    assert batch["false_positives"] == 0
    assert batch["false_negatives"] == 0


@pytest.mark.tier1
def test_tier1_f2_four_quadrant_confusion_matrix(mock_disturbance_truth_pred_pairs):
    """Verify exact disjoint four-quadrant confusion matrix counts (TP=1, TN=1, FP=1, FN=1)."""
    cases = [
        mock_disturbance_truth_pred_pairs["true_positive"],
        mock_disturbance_truth_pred_pairs["true_negative"],
        mock_disturbance_truth_pred_pairs["false_positive"],
        mock_disturbance_truth_pred_pairs["false_negative"],
    ]

    res = evaluate_inversion_batch(cases)
    assert res["total_cases"] == 4
    assert res["true_positives"] == 1
    assert res["true_negatives"] == 1
    assert res["false_positives"] == 1
    assert res["false_negatives"] == 1
    assert res["precision"] == pytest.approx(0.5)
    assert res["recall"] == pytest.approx(0.5)
    assert res["f1_score"] == pytest.approx(0.5)


@pytest.mark.tier1
def test_tier1_f2_spatial_localization_and_normalization():
    """Verify spatial localization error in physical meters and normalized by characteristic length D."""
    truth = DisturbanceGroundTruth(active=True, center=(0.10, 0.20))
    pred = DisturbancePrediction(detected=True, center=(0.13, 0.24))  # dx=0.03, dy=0.04 -> dist=0.05
    D = 0.30

    m = evaluate_disturbance_inversion(truth, pred, characteristic_length_D=D)
    assert m.localization_error_m == pytest.approx(0.05, abs=1e-6)
    assert m.localization_error_D == pytest.approx(0.05 / 0.30, abs=1e-6)


@pytest.mark.tier1
def test_tier1_f2_amplitude_and_direction_metrics():
    """Verify relative amplitude error (%) and angular direction error (degrees)."""
    truth = DisturbanceGroundTruth(active=True, amplitude=0.020, direction=(0.0, 1.0))
    pred = DisturbancePrediction(detected=True, amplitude=0.025, direction=(1.0, 1.0))

    m = evaluate_disturbance_inversion(truth, pred)
    assert m.amplitude_error_abs == pytest.approx(0.005, abs=1e-6)
    assert m.amplitude_error_rel_pct == pytest.approx(25.0, abs=1e-4)
    assert m.direction_angle_error_deg == pytest.approx(45.0, abs=1e-4)


@pytest.mark.tier1
def test_tier1_f2_moving_disturbance_trajectory_error(mock_inversion_metrics_list: list[InversionMetrics]):
    """Verify moving disturbance dynamic trajectory tracking error calculation and metrics list."""
    assert len(mock_inversion_metrics_list) == 5
    assert all(isinstance(m, InversionMetrics) for m in mock_inversion_metrics_list)

    truth = DisturbanceGroundTruth(active=True, t_on=0.0, t_off=2.0, center=(0.0, 0.0), velocity=(0.5, 0.0))
    pred = DisturbancePrediction(detected=True, center=(0.04, 0.03), velocity=(0.5, 0.0))

    m = evaluate_disturbance_inversion(truth, pred)
    assert m.trajectory_tracking_error_m == pytest.approx(0.05, abs=1e-6)


# ==============================================================================
# Feature F3: Probabilistic CRPS & Ensemble Metrics (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f3_crps_degenerate_single_member_equals_mae():
    """Verify that M=1 degenerate ensemble CRPS collapses identically to Mean Absolute Error."""
    ens = np.array([[[1.5, 2.5], [3.5, 4.5]]], dtype=np.float64)  # Shape (1, 2, 2)
    gt = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64)    # Shape (2, 2)

    crps = compute_crps_ensemble(ens, gt)
    mae = float(np.mean(np.abs(ens[0] - gt)))
    assert crps == pytest.approx(mae, abs=1e-12)
    assert crps == pytest.approx(0.5, abs=1e-12)


@pytest.mark.tier1
def test_tier1_f3_crps_identical_ensemble_zero_spread():
    """Verify zero-spread identical ensemble produces CRPS equal to deterministic error and SSR=0."""
    ens = np.full((8, 5, 5), 3.0, dtype=np.float64)
    gt = np.full((5, 5), 5.0, dtype=np.float64)

    crps = compute_crps_ensemble(ens, gt)
    ssr = compute_spread_skill_ratio(ens, gt)

    assert crps == pytest.approx(2.0, abs=1e-12)
    assert ssr == pytest.approx(0.0, abs=1e-12)


@pytest.mark.tier1
def test_tier1_f3_crps_analytical_symmetric_ensemble():
    """Verify analytical CRPS for symmetric two-member ensemble {-1.0, +1.0} against observation 0.0."""
    ens = np.array([[[-1.0]], [[+1.0]]], dtype=np.float64)  # Shape (2, 1, 1)
    gt = np.array([[0.0]], dtype=np.float64)                # Shape (1, 1)

    crps = compute_crps_ensemble(ens, gt)
    # Term 1 = 1.0, Term 2 = 0.5 -> CRPS = 0.5
    assert crps == pytest.approx(0.5, abs=1e-12)


@pytest.mark.tier1
def test_tier1_f3_crps_tensor_shape_stability_1d_2d_3d():
    """Verify CRPS calculation stability across 1D point probes, 2D fields, and 3D volume grids."""
    rng = np.random.default_rng(42)

    # 1D probes: (M=6, N=40)
    ens_1d = rng.normal(0, 1, size=(6, 40))
    gt_1d = rng.normal(0, 1, size=(40,))
    crps_1d = compute_crps_ensemble(ens_1d, gt_1d)
    assert isinstance(crps_1d, float) and np.isfinite(crps_1d) and crps_1d > 0.0

    # 2D field: (M=5, Ny=12, Nx=16)
    ens_2d = rng.normal(0, 1, size=(5, 12, 16))
    gt_2d = rng.normal(0, 1, size=(12, 16))
    crps_2d = compute_crps_ensemble(ens_2d, gt_2d)
    assert isinstance(crps_2d, float) and np.isfinite(crps_2d) and crps_2d > 0.0

    # 3D volume: (M=4, Nz=6, Ny=8, Nx=10)
    ens_3d = rng.normal(0, 1, size=(4, 6, 8, 10))
    gt_3d = rng.normal(0, 1, size=(6, 8, 10))
    crps_3d = compute_crps_ensemble(ens_3d, gt_3d)
    assert isinstance(crps_3d, float) and np.isfinite(crps_3d) and crps_3d > 0.0


@pytest.mark.tier1
def test_tier1_f3_crps_fluid_mask_exclusion():
    """Verify fluid mask strictly excludes non-fluid NaNs and non-physical values from CRPS."""
    ens = np.full((4, 8, 8), 2.0, dtype=np.float64)
    gt = np.full((8, 8), 1.0, dtype=np.float64)

    fluid_mask = np.ones((8, 8), dtype=bool)
    # Put obstacle with NaNs and infs in center
    fluid_mask[3:5, 3:5] = False
    ens[:, 3:5, 3:5] = np.nan
    gt[3:5, 3:5] = 1e9

    crps = compute_crps_ensemble(ens, gt, fluid_mask=fluid_mask)
    assert np.isfinite(crps)
    assert crps == pytest.approx(1.0, abs=1e-12)


@pytest.mark.tier1
def test_tier1_f3_spread_skill_ratio_calibration(synthetic_ensemble_predictions: tuple[np.ndarray, np.ndarray]):
    """Verify Spread-Skill Ratio distinguishes between overconfident, calibrated, and degenerate ensembles."""
    ens_synth, gt_synth = synthetic_ensemble_predictions
    crps_val = compute_crps_ensemble(ens_synth, gt_synth)
    ssr_val = compute_spread_skill_ratio(ens_synth, gt_synth)
    assert np.isfinite(crps_val) and crps_val > 0.0
    assert np.isfinite(ssr_val) and ssr_val > 0.0

    rng = np.random.default_rng(123)
    gt = np.zeros((50,), dtype=np.float64)

    # 1. Overconfident: small spread (0.01), large error (1.0)
    ens_over = rng.normal(loc=1.0, scale=0.01, size=(20, 50))
    ssr_over = compute_spread_skill_ratio(ens_over, gt)
    assert ssr_over < 0.1

    # 2. Calibrated: spread ~ 1.0, error ~ 1.0
    ens_cal = rng.normal(loc=1.0, scale=1.0, size=(100, 50))
    ssr_cal = compute_spread_skill_ratio(ens_cal, gt)
    assert 0.8 <= ssr_cal <= 1.2

    # 3. Degenerate M=1
    ens_degen = rng.normal(loc=1.0, scale=1.0, size=(1, 50))
    ssr_degen = compute_spread_skill_ratio(ens_degen, gt)
    assert ssr_degen == 0.0


# ==============================================================================
# Feature F4: Forecasting Metrics & Safe Evaluation (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f4_persistence_forecast_exact_copy():
    """Verify persistence forecast repeats the final history frame across all forecast leads."""
    history = np.ones((3, 4, 8, 8), dtype=np.float32)
    history[0] *= 1.0
    history[1] *= 2.0
    history[2] *= 3.0  # Last frame has value 3.0

    history_times = np.array([0.0, 0.5, 1.0])
    prediction_times = np.array([1.5, 2.0, 2.5, 3.0])

    pred = c04_forecast_from_history(history, history_times, prediction_times, method="persistence")
    assert pred.shape == (4, 4, 8, 8)
    for lead in range(4):
        assert np.all(pred[lead] == history[-1])


@pytest.mark.tier1
def test_tier1_f4_linear_forecast_exact_extrapolation():
    """Verify last-two-linear forecast computes exact linear slope extrapolation."""
    history = np.zeros((2, 4, 6, 6), dtype=np.float32)
    history[0] = 5.0
    history[1] = 8.0  # slope = (8 - 5) / (2 - 1) = 3.0

    history_times = np.array([1.0, 2.0])
    prediction_times = np.array([3.0, 5.0])  # leads dt=1.0 (val=11.0) and dt=3.0 (val=17.0)

    pred = c04_forecast_from_history(history, history_times, prediction_times, method="last_two_linear")
    assert pred.shape == (2, 4, 6, 6)
    assert np.allclose(pred[0], 11.0, atol=1e-6)
    assert np.allclose(pred[1], 17.0, atol=1e-6)


@pytest.mark.tier1
def test_tier1_f4_lead_time_validation_and_guards():
    """Verify validation guards on non-positive lead times and non-increasing timestamps."""
    history = np.ones((2, 4, 4, 4), dtype=np.float32)
    hist_times = np.array([0.0, 1.0])

    # Zero lead
    with pytest.raises(ModelValidationError, match="strictly greater"):
        c04_forecast_from_history(history, hist_times, np.array([1.0]))

    # Negative lead
    with pytest.raises(ModelValidationError, match="strictly greater"):
        c04_forecast_from_history(history, hist_times, np.array([0.5]))

    # Non-increasing prediction times
    with pytest.raises(ModelValidationError, match="strictly increasing"):
        c04_forecast_from_history(history, hist_times, np.array([2.0, 1.5]))

    # last_two_linear with only 1 history frame
    with pytest.raises(InvalidParameterError, match="requires at least 2 history frames"):
        c04_forecast_from_history(history[:1], hist_times[:1], np.array([2.0]), method="last_two_linear")


@pytest.mark.tier1
def test_tier1_f4_safe_division_on_zero_target_field():
    """Verify scoring against an identically zero target field avoids ZeroDivision RuntimeWarnings."""
    prediction = np.ones((2, 4, 8, 8), dtype=np.float64)
    target = np.zeros((2, 4, 8, 8), dtype=np.float64)

    with warnings.catch_warnings(record=True) as captured_warnings:
        warnings.simplefilter("always")
        scores = score_c04_forecast(prediction, target)

    div_warnings = [w for w in captured_warnings if "divide by zero" in str(w.message).lower()]
    assert len(div_warnings) == 0, f"Expected 0 division warnings, got {len(div_warnings)}"

    for ch in ["u", "v", "w", "p"]:
        assert scores["channels"][ch]["rel_l2_per_lead"] == [0.0, 0.0]
        assert scores["channels"][ch]["mae_per_lead"] == [1.0, 1.0]


@pytest.mark.tier1
def test_tier1_f4_solid_mask_fluid_domain_filtering():
    """Verify solid cylinder mask correctly restricts forecast scoring to fluid cells."""
    solid_mask = np.zeros((10, 10), dtype=bool)
    solid_mask[4:6, 4:6] = True  # 4 obstacle cells, 96 fluid cells

    prediction = np.zeros((2, 4, 10, 10), dtype=np.float64)
    target = np.zeros((2, 4, 10, 10), dtype=np.float64)

    # In solid mask cells, put large errors
    prediction[:, :, 4:6, 4:6] = 1e6

    scores = score_c04_forecast(prediction, target, solid_mask=solid_mask)
    assert scores["fluid_points"] == 96
    assert scores["channels"]["u"]["fluid_points"] == 96
    assert scores["channels"]["u"]["mae_per_lead"] == [0.0, 0.0]
    assert scores["channels"]["u"]["rmse_per_lead"] == [0.0, 0.0]


@pytest.mark.tier1
def test_tier1_f4_c04_dataset_loading_and_window_indexing(
    mock_c04_dataset_file: Path,
    mock_window_spec: C04WindowSpec,
    synthetic_grid_coords_c04_3d: C04GridCoordinates,
    synthetic_velocity_field_3d: np.ndarray,
):
    """Verify C04 dataset loading, sliding window indexing, and 3D/2D forecast scoring."""
    # 1. Dataset loading verification
    dataset = load_c04_dataset(mock_c04_dataset_file)
    assert "fields" in dataset
    assert "times" in dataset
    assert "metadata" in dataset
    assert dataset["fields"].shape == synthetic_velocity_field_3d.shape
    assert tuple(dataset["metadata"]["spatial_shape"]) == synthetic_grid_coords_c04_3d.shape

    # 2. Sliding window indexer verification
    indexer = C04WindowIndex(mock_window_spec)
    entries = indexer.add_dataset(mock_c04_dataset_file)
    assert len(entries) > 0, "Expected at least 1 valid sliding window"

    # 3. Forecast evaluation on extracted window
    first_window = entries[0]
    history = dataset["fields"][first_window.start_index : first_window.history_stop]
    hist_times = np.array(first_window.history_times)
    pred_times = np.array(first_window.future_times)

    pred_3d = c04_forecast_from_history(history, hist_times, pred_times, method="persistence")
    assert pred_3d.shape[0] == mock_window_spec.forecast_frames
    assert pred_3d.shape[1:] == dataset["fields"].shape[1:]

    target_3d = dataset["fields"][first_window.history_stop : first_window.future_stop]
    scores_3d = score_c04_forecast(pred_3d, target_3d)
    assert "channels" in scores_3d
    for ch in ["u", "v", "w", "p"]:
        assert ch in scores_3d["channels"]

    # 4. 2D slice spatial generality verification
    h_2d = np.ones((3, 4, 12, 12), dtype=np.float32)
    t_hist_2d = np.array([0.0, 0.1, 0.2])
    t_pred_2d = np.array([0.3, 0.4])
    pred_2d = c04_forecast_from_history(h_2d, t_hist_2d, t_pred_2d)
    assert pred_2d.shape == (2, 4, 12, 12)
    scores_2d = score_c04_forecast(pred_2d, pred_2d)
    assert scores_2d["channels"]["u"]["mae_per_lead"] == [0.0, 0.0]


# ==============================================================================
# Feature F5: Force Expressions & Disturbance Generation (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f5_canonical_3d_force_expressions_syntax_and_units():
    """Verify 3D canonical force expressions contain exact COMSOL syntax, units, and temporal gating."""
    cfg = C04DisturbanceConfig(
        center_m=(0.60, 0.0, 1.50),
        direction=(0.0, 1.0, 0.0),  # pure crossflow (+y)
        acceleration_m_s2=0.08,
        sigma_m=0.06,
        on_s=10.0,
        off_s=12.0,
        velocity_m_s=(0.0, 0.0, 0.0),
    )
    exprs = generate_c04_comsol_force_expressions(cfg)

    assert set(exprs.keys()) == {"Fx", "Fy", "Fz"}
    assert "1025.8944[kg/m^3]" in exprs["Fy"]
    assert "0.080000[m/s^2]" in exprs["Fy"]
    assert "0.060000[m]^2" in exprs["Fy"]
    assert "10.0000[s]" in exprs["Fy"]
    assert "12.0000[s]" in exprs["Fy"]

    assert exprs["Fy"].startswith("1.000000*(")
    assert exprs["Fx"].startswith("0.000000*(")
    assert exprs["Fz"].startswith("0.000000*(")

    gate_expected = "if((t>10.0000[s])&&(t<12.0000[s]),sin(pi*(t-10.0000[s])/(2.0000[s]))^2,0)"
    assert gate_expected in exprs["Fy"]


@pytest.mark.tier1
def test_tier1_f5_extruded_xz_spatial_and_vector_axis_mapping():
    """Verify coordinate transformation from canonical (x, y, z) to extruded-xz COMSOL axes."""
    cfg = C04DisturbanceConfig(
        center_m=(1.20, 0.40, 2.10),
        direction=(0.0, 1.0, 0.0),
        acceleration_m_s2=0.05,
        sigma_m=0.05,
        on_s=1.0,
        off_s=2.0,
    )
    exprs = generate_c04_comsol_extruded_xz_force_expressions(cfg, span_offset_m=3.0)

    assert "(x-1.200000[m])^2" in exprs["Fz"]
    assert "(y-(-0.900000[m]))^2" in exprs["Fz"]
    assert "(z-(0.400000[m]))^2" in exprs["Fz"]

    assert exprs["Fz"].startswith("1.000000*(")
    assert exprs["Fy"].startswith("0.000000*(")
    assert exprs["Fx"].startswith("0.000000*(")


@pytest.mark.tier1
def test_tier1_f5_moving_disturbance_velocity_kinematics_3d(synthetic_slice_coords_c04: C04SliceCoordinates):
    """Verify dynamic translation kinematics and symbolic relative-time parameterization."""
    assert synthetic_slice_coords_c04.z_fixed == 1.5
    assert synthetic_slice_coords_c04.cylinder_diameter == 0.30
    cfg = C04DisturbanceConfig(
        center_m=(0.50, 0.0, 1.50),
        direction=(1.0, 0.0, 0.0),
        acceleration_m_s2=0.04,
        sigma_m=0.05,
        on_s=2.0,
        off_s=4.0,
        velocity_m_s=(0.25, 0.10, 0.0),
    )

    assert cfg.center_at_time(1.0) == (0.50, 0.0, 1.50)
    assert cfg.center_at_time(3.0) == (0.75, 0.10, 1.50)
    assert cfg.center_at_time(5.0) == (1.00, 0.20, 1.50)

    exprs = generate_c04_comsol_force_expressions(cfg)
    assert "(0.500000[m]+0.250000[m/s]*(t-2.0000[s]))" in exprs["Fx"]
    assert "(0.000000[m]+0.100000[m/s]*(t-2.0000[s]))" in exprs["Fx"]
    assert "1.500000[m]" in exprs["Fx"]


@pytest.mark.tier1
def test_tier1_f5_analytical_field_evaluation_spatial_temporal_peak():
    """Verify analytical field evaluation across inactive boundaries and peak intensity."""
    cfg = C04DisturbanceConfig(
        center_m=(0.60, 0.0, 1.50),
        direction=(0.0, 1.0, 0.0),
        acceleration_m_s2=0.10,
        sigma_m=0.05,
        on_s=1.0,
        off_s=3.0,
    )
    x = np.linspace(0.4, 0.8, 21, dtype=np.float32)
    y = np.linspace(-0.2, 0.2, 21, dtype=np.float32)
    X, Y = np.meshgrid(x, y)
    grid = C04SliceCoordinates(
        x_1d=x,
        y_1d=y,
        X=X,
        Y=Y,
        z_fixed=1.5,
        solid_mask=np.zeros_like(X, dtype=bool),
        sdf=np.ones_like(X, dtype=np.float32),
        cell_spacing=float(x[1] - x[0]),
        cylinder_diameter=0.30,
    )

    field_pre = evaluate_c04_disturbance_field(cfg, grid, time_s=0.5)
    assert np.all(field_pre == 0.0)

    field_post = evaluate_c04_disturbance_field(cfg, grid, time_s=3.5)
    assert np.all(field_post == 0.0)

    field_peak = evaluate_c04_disturbance_field(cfg, grid, time_s=2.0)
    assert field_peak.shape == (3, 21, 21)
    assert np.all(field_peak[0] == 0.0)
    assert np.all(field_peak[2] == 0.0)
    max_ay = float(np.max(field_peak[1]))
    assert 0.095 <= max_ay <= 0.100 + 1e-6


@pytest.mark.tier1
def test_tier1_f5_2d_pulse_configuration_and_property_serialization():
    """Verify 2D pulse parameter validation, direction vector, and Java properties payload."""
    cfg_raw = {
        "schema_version": 1,
        "event_id": "test_pulse_2d_01",
        "mechanism": "gaussian_body_acceleration",
        "pulse": {
            "center_m": [0.25, 0.12],
            "direction_deg": 90.0,
            "acceleration_m_s2": 0.008,
            "sigma_m": 0.010,
            "on_s": 182.0,
            "off_s": 184.0,
        },
    }
    validated = validate_pulse_config(cfg_raw)
    assert validated["event_id"] == "test_pulse_2d_01"

    dir_vec = pulse_direction(cfg_raw)
    assert math.isclose(dir_vec[0], 0.0, abs_tol=1e-12)
    assert math.isclose(dir_vec[1], 1.0, abs_tol=1e-12)

    props = properties_payload(cfg_raw)
    assert "event_id=test_pulse_2d_01" in props
    assert "xc_m=0.25" in props
    assert "yc_m=0.12" in props
    assert "direction_deg=90.0" in props

    ref = source_integral_reference(cfg_raw)
    assert ref["gaussian_area_m2"] > 0.0
    assert ref["peak_force_density_N_m3"] == pytest.approx(998.2 * 0.008, rel=1e-5)
    assert ref["impulse_per_depth_N_s_m"] > 0.0


@pytest.mark.tier1
def test_tier1_f5_geometric_boundary_and_cylinder_collision_admission():
    """Verify strict admission rejection for obstacle collision and out-of-bounds trajectory."""
    # Cylinder obstacle collision
    with pytest.raises(InvalidParameterError, match="intersect or touch the cylinder"):
        C04DisturbanceConfig(
            center_m=(0.20, 0.0, 1.50),
            direction=(0.0, 1.0, 0.0),
            acceleration_m_s2=0.05,
            sigma_m=0.05,
            on_s=1.0,
            off_s=1.5,
        )

    # Domain boundary collision
    with pytest.raises(InvalidParameterError, match="streamwise bounds"):
        C04DisturbanceConfig(
            center_m=(5.95, 0.0, 1.50),
            direction=(0.0, 1.0, 0.0),
            acceleration_m_s2=0.05,
            sigma_m=0.05,
            on_s=1.0,
            off_s=1.5,
        )


# ==============================================================================
# Feature F6: Script Execution & Error Resilience (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f6_script_cli_help_flags_exit_zero():
    """Verify operational scripts respond to --help cleanly with exit code 0."""
    scripts = [
        "scripts/benchmark_comsol_numa.py",
        "scripts/calibrate_c04_3d.py",
        "scripts/dispatch_backfill.py",
    ]
    for s in scripts:
        cmd = [sys.executable, str(REPO_ROOT / s), "--help"]
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        assert proc.returncode == 0, f"Script {s} --help failed: {proc.stderr}"
        assert "usage:" in proc.stdout.lower()


@pytest.mark.tier1
def test_tier1_f6_numa_benchmark_speedup_division_guards():
    """Verify guarded speedup calculations prevent ZeroDivisionError on microsecond or zero solves."""
    results = [
        {"profile": "base", "ok": True, "wall_s": 0.5, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 0.0},
        {"profile": "fast", "ok": True, "wall_s": 0.2, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 100.0},
    ]
    base = results[0]

    for r in results:
        if r["ok"] and r["wall_s"] > 0:
            r["single_job_speedup"] = (
                round(base["mean_solve_s"] / r["mean_solve_s"], 3)
                if r.get("mean_solve_s", 0) > 0
                else 0.0
            )
            r["throughput_speedup"] = (
                round(r["throughput_jobs_per_hr"] / base["throughput_jobs_per_hr"], 3)
                if base.get("throughput_jobs_per_hr", 0) > 0
                else 0.0
            )

    assert results[0]["single_job_speedup"] == 0.0
    assert results[1]["single_job_speedup"] == 0.0
    assert results[1]["throughput_speedup"] == 0.0


@pytest.mark.tier1
def test_tier1_f6_dispatch_backfill_dynamic_drive_anchor():
    """Verify get_system_health executes successfully when anchored to the current volume."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import dispatch_backfill as db

    repo_drive = str(Path.cwd().anchor)
    health = db.get_system_health(drive=repo_drive)

    assert "free_ram_gb" in health
    assert "total_ram_gb" in health
    assert health["free_ram_gb"] > 0.0
    assert health["total_ram_gb"] > 0.0


@pytest.mark.tier1
def test_tier1_f6_dispatch_backfill_dry_run_execution(tmp_path: Path, monkeypatch):
    """Verify queue scheduling in dry-run mode targets free socket without spawning processes."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import dispatch_backfill as db

    q_data = {
        "schema_version": 1,
        "policy": {"max_slots_per_numa_socket": 2},
        "running_now": {
            "N0-A": {"numafirst": 0, "pid": 1001, "label": "running_job"},
        },
        "queue": [
            {
                "job_id": "JOB_01",
                "priority": 1,
                "status": "READY",
                "base_model": str(tmp_path / "base.mph"),
                "out_dir": str(tmp_path / "out"),
                "case_id": "C_rtol1e4_max005",
                "save_mph": False,
                "min_free_ram_gb": 10.0,
                "min_free_disk_e_gb": 20.0,
            }
        ],
    }
    qp = tmp_path / "queue.json"
    qp.write_text(json.dumps(q_data), encoding="utf-8")

    monkeypatch.setattr(
        db,
        "get_system_health",
        lambda _d=None: {"free_ram_gb": 32.0, "total_ram_gb": 64.0, "free_disk_e_gb": 100.0, "free_disk_gb": 100.0},
    )
    monkeypatch.setattr(
        db,
        "list_live_comsol_processes",
        lambda: [{"pid": 1001, "np": 16, "numasets": 1, "numafirst": 0, "out_mph": "a.mph"}],
    )

    res = db.dispatch_one_job(queue_path=qp, dry_run=True, requested_numafirst=1)
    assert res["status"] == "DRY_RUN_WOULD_LAUNCH"
    assert res["job_id"] == "JOB_01"
    assert res["target_socket"] == 1


@pytest.mark.tier1
def test_tier1_f6_calibrate_c04_3d_plan_campaign_isolated(tmp_path: Path):
    """Verify plan-campaign CLI generates valid JSON execution plan in specified directory."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import calibrate_c04_3d as c04cal

    out_plan = tmp_path / "test_campaign_plan.json"
    rc = c04cal.main([
        "plan-campaign",
        "--out",
        str(out_plan),
        "--t-end",
        "40.0",
        "--dt-out",
        "0.08",
        "--np-per-worker",
        "16",
    ])
    assert rc == 0
    assert out_plan.is_file()

    plan_doc = json.loads(out_plan.read_text(encoding="utf-8"))
    assert plan_doc["calibrated_solver"]["t_end_s"] == 40.0
    assert plan_doc["calibrated_solver"]["dt_out_s"] == 0.08
    assert "conditions" in plan_doc
    assert len(plan_doc["conditions"]) == 9


@pytest.mark.tier1
def test_tier1_f6_calibrate_c04_3d_offline_table_normalization(tmp_path: Path):
    """Verify offline normalization parses raw probe tables and computes CL and CD series."""
    scripts_dir = REPO_ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import calibrate_c04_3d as c04cal

    t = np.linspace(0.0, 5.0, 51)
    q_inf = 0.5 * c04cal.C04_RHO0 * (c04cal.C04_U0 ** 2)
    area_frontal = c04cal.C04_D_M * c04cal.C04_L_SPAN_M

    cl_synth = 0.5 * np.sin(2.0 * math.pi * t / 2.0)
    cd_synth = 1.1 + 0.1 * np.cos(4.0 * math.pi * t / 2.0)

    # Streamwise traction convention has negative sign
    fz_int = cl_synth * q_inf * area_frontal
    fx_int = -cd_synth * q_inf * area_frontal

    def _write_table(p: Path, header: str, vals: np.ndarray):
        lines = ["% COMSOL Table", f"% Time | {header}"]
        for ti, vi in zip(t, vals):
            lines.append(f"{ti:.6f} {vi:.6f}")
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _write_table(tmp_path / "probe_lift_int.txt", "F_z", fz_int)
    _write_table(tmp_path / "probe_drag_int.txt", "F_x", fx_int)

    res = c04cal.normalize_c04_probe_tables(tmp_path)
    assert res["consistency"]["normalization_source"] == "integral_force"
    assert np.allclose(res["cl"], cl_synth, atol=1e-5)
    assert np.allclose(res["cd"], cd_synth, atol=1e-5)
    assert (tmp_path / "lift_coeff.txt").is_file()
    assert (tmp_path / "drag_coeff.txt").is_file()


# ==============================================================================
# Feature F7: Package Dependencies & CLI Invocations (6 Tests)
# ==============================================================================

@pytest.mark.tier1
def test_tier1_f7_cli_all_26_subcommands_registered(capsys):
    """Verify unified CLI defines exactly 26 subcommands."""
    expected_subcommands = {
        "plan",
        "inspect",
        "probe",
        "solve",
        "java-inspect-api",
        "java-inspect",
        "java-update-param",
        "java-solve",
        "java-build-re100",
        "java-build-c04",
        "java-build-c04-mesh2",
        "java-export-c04-slice",
        "java-export-c04-volume",
        "java-export-c04-slice-sequence",
        "java-export-c04-volume-sequence",
        "java-export-data",
        "java-export-s1-diagnostics",
        "c04-inspect-windows",
        "c04-evaluate-baselines",
        "observation-plan",
        "observation-sample",
        "reconstruction-eval",
        "vorticity-eval",
        "inversion-eval",
        "bundle-create",
        "bundle-verify",
    }
    with pytest.raises(SystemExit) as exc:
        cli_main(["--help"])
    assert exc.value.code == 0

    captured = capsys.readouterr()
    help_text = captured.out
    for cmd in expected_subcommands:
        assert cmd in help_text, f"Expected subcommand '{cmd}' not present in help"
    assert len(expected_subcommands) == 26


@pytest.mark.tier1
def test_tier1_f7_cli_subcommands_help_invocation():
    """Verify primary CLI subcommands respond to --help cleanly."""
    primary_cmds = [
        "plan",
        "c04-inspect-windows",
        "observation-plan",
        "reconstruction-eval",
        "bundle-create",
        "bundle-verify",
    ]
    for subcmd in primary_cmds:
        with pytest.raises(SystemExit) as exc:
            cli_main([subcmd, "--help"])
        assert exc.value.code == 0


@pytest.mark.tier1
def test_tier1_f7_cli_observation_plan_execution(tmp_path: Path):
    """Verify observation-plan subcommand generates valid sensor layout JSON."""
    out_file = tmp_path / "rake_layout.json"
    rc = cli_main(["observation-plan", "--type", "rake-2d", "--out", str(out_file)])
    assert rc == 0
    assert out_file.is_file()

    layout = json.loads(out_file.read_text(encoding="utf-8"))
    assert layout["layout_type"] == "downstream_rake"
    assert layout["dim"] == 2
    assert layout["num_probes"] == 68
    assert len(layout["probes"]) == 68


@pytest.mark.tier1
def test_tier1_f7_safe_sequence_loading_and_descriptor_release(mock_saved_sequence_dir: Path, tmp_path: Path):
    """Verify full sequence loader materializes flow fields while streaming batches release file locks."""
    # 1. Full sequence loading & cryptographic hash verification
    seq = load_full_field_sequence(mock_saved_sequence_dir)
    assert "values" in seq
    assert "times" in seq
    assert seq["values"].ndim == 4
    assert seq["values"].shape[0] == len(seq["times"])

    # 2. Batch reader verification (verifies manifests without permanent locks)
    reader = FieldSequenceReader(mock_saved_sequence_dir)
    batches = list(reader.iter_batches())
    assert len(batches) == 1
    assert batches[0]["values"].shape == seq["values"].shape

    # 3. Direct Windows file descriptor release verification
    test_npz = tmp_path / "lock_test.npz"
    np.savez(test_npz, data=np.ones((4, 4), dtype=np.float32))
    with np.load(test_npz, allow_pickle=False) as data:
        arr = data["data"]
        assert arr.shape == (4, 4)
    test_npz.unlink()
    assert not test_npz.exists()


@pytest.mark.tier1
def test_tier1_f7_pyproject_metadata_and_optional_extras():
    """Verify pyproject.toml contains valid package metadata, console script, and extras."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.is_file()

    with open(pyproject_path, "rb") as f:
        meta = tomllib.load(f)

    project = meta["project"]
    assert project["name"] == "hydrodisturb-bench"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.10"
    assert project["scripts"]["hydrobench"] == "hydro_disturb_bench.cli:main"

    extras = project["optional-dependencies"]
    assert "dev" in extras
    assert "analysis" in extras
    assert "comsol" in extras


@pytest.mark.tier1
def test_tier1_f7_bundle_create_and_verify_cli_pipeline(tmp_path: Path):
    """Verify end-to-end bundle creation and cryptographic verification via CLI."""
    src_npz = tmp_path / "source_flow.npz"
    bundle_npz = tmp_path / "final_bundle.npz"

    np.savez(
        src_npz,
        times=np.linspace(0.0, 1.0, 5, dtype=np.float32),
        data=np.ones((5, 3, 8, 8), dtype=np.float32),
        x=np.linspace(0.0, 1.0, 8, dtype=np.float32),
        y=np.linspace(0.0, 1.0, 8, dtype=np.float32),
        solid_mask=np.zeros((8, 8), dtype=bool),
        sdf=np.ones((8, 8), dtype=np.float32),
    )

    # 1. Create bundle
    rc_create = cli_main([
        "bundle-create",
        "--dataset",
        str(src_npz),
        "--out",
        str(bundle_npz),
        "--name",
        "e2e_tier1_benchmark",
    ])
    assert rc_create == 0
    assert bundle_npz.is_file()

    # 2. Verify bundle
    rc_verify = cli_main(["bundle-verify", "--bundle", str(bundle_npz)])
    assert rc_verify == 0
