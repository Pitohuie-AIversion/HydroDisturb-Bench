"""E2E Test Harness Configuration and Reusable Synthetic Fixtures.

Provides comprehensive synthetic data fixtures for HydroDisturb-Bench E2E suites:
- 2D and 3D flow velocity fields and Cartesian grid coordinate objects.
- Temporary COMSOL simulation export directories and NPZ sequence directories.
- Disturbance inversion ground truth, predictions, metrics, and confusion objects.
- Sliding window indexing specifications and dataset records.
- Pytest marker registration for Tier 1-4 suites.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from hydro_disturb_bench.c04_windows import C04WindowSpec
from hydro_disturb_bench.disturbance_inversion import (
    DisturbanceGroundTruth,
    DisturbancePrediction,
    InversionMetrics,
    evaluate_disturbance_inversion,
)
from hydro_disturb_bench.sampling_3d import (
    C04GridCoordinates,
    C04SliceCoordinates,
    create_c04_2d_slice_grid,
    create_c04_sampling_grid,
)

# Alias for interface contract
DisturbanceInversionMetric = InversionMetrics


def pytest_configure(config: pytest.Config) -> None:
    """Register E2E tier markers to prevent PytestUnknownMarkWarning."""
    config.addinivalue_line("markers", "tier1: Tier 1 Feature Coverage E2E test")
    config.addinivalue_line("markers", "tier2: Tier 2 Boundary and Edge Cases E2E test")
    config.addinivalue_line("markers", "tier3: Tier 3 Pairwise Combinatorial E2E test")
    config.addinivalue_line("markers", "tier4: Tier 4 Real-World Workload Pipeline E2E test")


# ==============================================================================
# Grids and Coordinate Fixtures
# ==============================================================================

@pytest.fixture
def synthetic_grid_2d() -> dict[str, Any]:
    """Synthetic 2D Cartesian grid for cylinder wake sampling (Re=100 layout)."""
    nx, ny = 17, 9
    x = np.linspace(0.0, 0.40, nx, dtype=np.float64)
    y = np.linspace(0.0, 0.24, ny, dtype=np.float64)
    xx, yy = np.meshgrid(x, y)
    center = (0.20, 0.12)
    radius = 0.03
    cylinder_mask = np.hypot(xx - center[0], yy - center[1]) <= radius + 1e-12
    return {
        "nx": nx,
        "ny": ny,
        "x": x,
        "y": y,
        "xx": xx,
        "yy": yy,
        "cylinder_mask": cylinder_mask,
        "center": center,
        "radius": radius,
        "bounds": [0.0, 0.40, 0.0, 0.24],
    }


@pytest.fixture
def synthetic_slice_coords_c04() -> C04SliceCoordinates:
    """Cell-centered 2D slice coordinates at fixed z for C04 benchmark."""
    return create_c04_2d_slice_grid(D=0.30, z_fixed=1.5, cells_per_D=16)


@pytest.fixture
def synthetic_grid_coords_c04_3d() -> C04GridCoordinates:
    """Cell-centered 3D regular grid for C04 benchmark (downscaled for fast testing)."""
    return create_c04_sampling_grid(
        D=0.30,
        x_range_D=(-1.0, 3.0),
        y_range_D=(-1.5, 1.5),
        z_range_D=(0.0, 1.0),
        cells_per_D=4,  # Compact size: fast allocation and evaluation
    )


# ==============================================================================
# Velocity Fields and Timestamps Fixtures
# ==============================================================================

@pytest.fixture
def synthetic_timestamps() -> np.ndarray:
    """1D array of 10 strictly increasing physical timestamps."""
    return np.linspace(100.0, 100.0 + 0.1 * 9, 10, dtype=np.float64)


@pytest.fixture
def synthetic_velocity_field_2d(synthetic_grid_2d, synthetic_timestamps) -> dict[str, Any]:
    """Synthetic (T, 3, Ny, Nx) velocity and pressure tensor with NaN in cylinder."""
    times = synthetic_timestamps
    xx, yy = synthetic_grid_2d["xx"], synthetic_grid_2d["yy"]
    cylinder = synthetic_grid_2d["cylinder_mask"]
    t_len, ny, nx = len(times), synthetic_grid_2d["ny"], synthetic_grid_2d["nx"]

    # Generate analytical flow field: u, v, p
    values = np.zeros((t_len, 3, ny, nx), dtype=np.float64)
    for i, t in enumerate(times):
        values[i, 0] = 0.2 + 0.05 * np.sin(t + xx) * np.cos(yy)  # u
        values[i, 1] = 0.02 * np.cos(t + yy) * np.sin(xx)        # v
        values[i, 2] = 101325.0 - 0.5 * 1025.0 * (values[i, 0] ** 2 + values[i, 1] ** 2)  # p

    # Mask cylinder
    values[:, :, cylinder] = np.nan
    mask = np.broadcast_to(cylinder, (t_len, ny, nx)).copy()

    return {
        "values": values,
        "mask": mask,
        "times": times,
        "x": synthetic_grid_2d["x"],
        "y": synthetic_grid_2d["y"],
        "cylinder_mask": cylinder,
        "center": synthetic_grid_2d["center"],
        "radius": synthetic_grid_2d["radius"],
        "shape": (t_len, 3, ny, nx),
    }


@pytest.fixture
def synthetic_velocity_field_3d(synthetic_grid_coords_c04_3d) -> np.ndarray:
    """Synthetic (T=8, 4, Nz, Ny, Nx) 3D flow tensor with channels (u, v, w, p)."""
    coords = synthetic_grid_coords_c04_3d
    nz, ny, nx = coords.shape
    t_len = 8
    fields = np.zeros((t_len, 4, nz, ny, nx), dtype=np.float32)
    fields[:, 0] = 0.50   # u (m/s)
    fields[:, 1] = 0.02   # v (m/s)
    fields[:, 2] = 0.00   # w (m/s)
    fields[:, 3] = 100.0  # p (Pa)
    return fields


@pytest.fixture
def synthetic_ensemble_predictions() -> tuple[np.ndarray, np.ndarray]:
    """Synthetic ensemble forecast (M=5, N=100) and ground truth (N=100) for CRPS."""
    rng = np.random.default_rng(42)
    ground_truth = rng.normal(loc=1.0, scale=0.2, size=100)
    # Ensemble with spread around ground truth
    ensemble = ground_truth[None, :] + rng.normal(loc=0.0, scale=0.1, size=(5, 100))
    return ensemble, ground_truth


# ==============================================================================
# Simulation & Sequence Directory Fixtures (tmp_path)
# ==============================================================================

@pytest.fixture
def mock_comsol_export_dir(tmp_path: Path) -> Path:
    """Mock COMSOL export directory containing text export and MPH model file."""
    export_dir = tmp_path / "comsol_export"
    export_dir.mkdir(parents=True)
    mph_path = export_dir / "case_model.mph"
    mph_path.write_bytes(b"MOCK_COMSOL_BINARY_PAYLOAD_V64")

    txt_path = export_dir / "grid_export.txt"
    lines = [
        "% Model: case_model.mph",
        "% Length unit: m",
        "% x y u (m/s) @ t=1.0 v (m/s) @ t=1.0 p (Pa) @ t=1.0",
        "0.0 0.0 0.50 0.01 100.0",
        "0.1 0.0 0.52 0.02 99.8",
        "0.0 0.1 0.49 -0.01 100.2",
    ]
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return export_dir


@pytest.fixture
def mock_saved_sequence_dir(tmp_path: Path, synthetic_velocity_field_2d) -> Path:
    """Mock compliant saved sequence directory structure with manifest and NPZs."""
    seq_dir = tmp_path / "mock_sequence"
    seq_dir.mkdir(parents=True)
    model_path = tmp_path / "source_model.mph"
    model_path.write_bytes(b"IMMUTABLE_SOURCE_MODEL")
    source_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()

    data = synthetic_velocity_field_2d
    values = data["values"]
    times = data["times"]
    t_len = len(times)
    nx, ny = len(data["x"]), len(data["y"])
    step = float(times[1] - times[0])

    batch_dir = seq_dir / "batch_0000"
    batch_dir.mkdir()

    npz_path = batch_dir / "field_sample.npz"
    np.savez_compressed(
        npz_path,
        values=values,
        mask=data["mask"],
        invalid_mask=np.zeros_like(data["mask"], dtype=bool),
        times=times,
        x=data["x"],
        y=data["y"],
        cylinder_mask=data["cylinder_mask"],
    )
    npz_sha = hashlib.sha256(npz_path.read_bytes()).hexdigest()

    geometry = {
        "circle": {
            "center": list(data.get("center", (0.20, 0.12))),
            "radius": float(data.get("radius", 0.03)),
        }
    }
    unit_evidence = [{"solnum": i + 1} for i in range(t_len)]

    meta = {
        "schema_version": 1,
        "synthetic_fixture": True,
        "tensor_layout": "(T, 3, Ny, Nx)",
        "channel_order": ["u", "v", "p"],
        "units": ["m/s", "m/s", "Pa"],
        "shape": list(values.shape),
        "npz_sha256": npz_sha,
        "source_model": str(model_path),
        "source_sha256": source_sha,
        "model_tag": "synthetic_model",
        "dataset": "dset1",
        "geometry": geometry,
        "requested_times": times.tolist(),
        "actual_times": times.tolist(),
        "coordinates": {"x": data["x"].tolist(), "y": data["y"].tolist()},
        "unit_evidence": unit_evidence,
    }
    (batch_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (batch_dir / "bridge_response.json").write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "status": "PASS",
        "operation": "export_full_field_sequence",
        "tensor_layout": "(T, 3, Ny, Nx)",
        "channel_order": ["u", "v", "p"],
        "units": ["m/s", "m/s", "Pa"],
        "source_model": str(model_path),
        "source_sha256": source_sha,
        "requested_times": times.tolist(),
        "actual_times": times.tolist(),
        "time_step_s": step,
        "grid_shape": [nx, ny],
        "shape": [t_len, 3, ny, nx],
        "solution_numbers": list(range(1, t_len + 1)),
        "batches": [{
            "path": "batch_0000",
            "start_index": 0,
            "stop_index": t_len,
            "count": t_len,
            "npz_sha256": npz_sha,
            "metadata_sha256": hashlib.sha256((batch_dir / "metadata.json").read_bytes()).hexdigest(),
            "response_sha256": hashlib.sha256((batch_dir / "bridge_response.json").read_bytes()).hexdigest(),
        }],
    }
    (seq_dir / "sequence_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return seq_dir


# ==============================================================================
# Disturbance Inversion Fixtures
# ==============================================================================

@pytest.fixture
def mock_disturbance_truth_pred_pairs() -> dict[str, tuple[DisturbanceGroundTruth, DisturbancePrediction]]:
    """Canonical truth-prediction pairs covering TP, FP, FN, TN, and timestamp-less TP."""
    return {
        "true_positive": (
            DisturbanceGroundTruth(active=True, t_on=180.0, t_off=182.0, center=(0.15, 0.12), amplitude=0.01),
            DisturbancePrediction(detected=True, t_detect=180.2, t_on=180.0, t_off=182.0, center=(0.15, 0.12), amplitude=0.01),
        ),
        "true_positive_no_timestamp": (
            DisturbanceGroundTruth(active=True, t_on=180.0, t_off=182.0, center=(0.15, 0.12), amplitude=0.01),
            DisturbancePrediction(detected=True, t_detect=None, center=(0.15, 0.12)),
        ),
        "false_positive": (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=True, t_detect=180.5),
        ),
        "false_negative": (
            DisturbanceGroundTruth(active=True, t_on=180.0, t_off=182.0),
            DisturbancePrediction(detected=False),
        ),
        "true_negative": (
            DisturbanceGroundTruth(active=False),
            DisturbancePrediction(detected=False),
        ),
    }


@pytest.fixture
def mock_inversion_metrics_list(mock_disturbance_truth_pred_pairs) -> list[InversionMetrics]:
    """List of InversionMetrics with known counts (2 TP, 1 FP, 1 FN, 1 TN)."""
    return [
        evaluate_disturbance_inversion(t, p)
        for t, p in mock_disturbance_truth_pred_pairs.values()
    ]


# ==============================================================================
# Sliding Forecast Windows Fixtures
# ==============================================================================

@pytest.fixture
def mock_window_spec() -> C04WindowSpec:
    """Sliding window spec (history=4, forecast=2, stride=1)."""
    return C04WindowSpec(history_frames=4, forecast_frames=2, stride=1)


@pytest.fixture
def mock_c04_dataset_file(tmp_path: Path, synthetic_velocity_field_3d, synthetic_grid_coords_c04_3d) -> Path:
    """Mock C04 .npz archive loadable by load_c04_dataset."""
    out_path = tmp_path / "mock_c04_dataset.npz"
    fields = synthetic_velocity_field_3d
    coords = synthetic_grid_coords_c04_3d
    times = np.linspace(0.0, 0.24, fields.shape[0])
    metadata = {
        "case_id": "C04",
        "reynolds_number": 126167,
        "is_3d": True,
        "spatial_shape": list(coords.shape),
    }
    np.savez_compressed(
        out_path,
        fields=fields,
        times=times,
        solid_mask=coords.solid_mask,
        sdf=coords.sdf,
        x_1d=coords.x_1d,
        y_1d=coords.y_1d,
        z_1d=coords.z_1d,
        metadata_json=json.dumps(metadata),
    )
    return out_path
