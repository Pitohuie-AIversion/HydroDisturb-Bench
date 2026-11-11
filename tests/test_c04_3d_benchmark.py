"""Tests for C04 3D Cylinder Flow benchmark configuration, sampling grids, and model assets."""

import json
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.sampling_3d import (
    C04GridCoordinates,
    C04SliceCoordinates,
    create_c04_2d_slice_grid,
    create_c04_sampling_grid,
    export_sampling_coordinates_csv,
)


def test_c04_config_file_specifications():
    """Verify configs/c04_re126k_3d_baseline.json adheres to ITTC 2024 and Lysenko 2025 specs."""
    config_path = Path("configs/c04_re126k_3d_baseline.json")
    assert config_path.is_file(), f"Config file missing: {config_path}"

    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    assert cfg["case_id"] == "C04"

    # 1. Fluid property verification (ITTC 2024 seawater at 15°C, 35 g/kg)
    fluid = cfg["fluid"]
    rho = fluid["density_kg_per_m3"]
    nu = fluid["kinematic_viscosity_m2_per_s"]
    mu = fluid["dynamic_viscosity_pa_s"]
    assert pytest.approx(1025.8944, rel=1e-5) == rho
    assert pytest.approx(1.1889e-6, rel=1e-5) == nu
    # Derived dynamic viscosity mu = rho * nu
    assert pytest.approx(rho * nu, rel=1e-5) == mu

    # 2. Cylinder & Reynolds number
    cyl = cfg["cylinder"]
    D = cyl["diameter_m"]
    U0 = cyl["inflow_speed_m_per_s"]
    alpha = cyl["inflow_angle_deg"]
    assert D == 0.30
    assert U0 == 0.50
    assert alpha == 90.0
    expected_re = U0 * D / nu
    assert pytest.approx(126167, abs=2) == expected_re
    assert cfg["cylinder"]["reynolds_number"] == 126167

    # 3. CFD Domain (Lysenko 2025 30D x 20D x 10D)
    domain = cfg["cfd_domain"]
    assert domain["dimensions_m"]["x_length"] == 30 * D  # 9.0 m
    assert domain["dimensions_m"]["y_width"] == 20 * D  # 6.0 m
    assert domain["dimensions_m"]["z_depth"] == 10 * D  # 3.0 m
    assert domain["coordinate_bounds_m"]["x"] == [-3.0, 6.0]
    assert domain["coordinate_bounds_m"]["y"] == [-3.0, 3.0]
    assert domain["coordinate_bounds_m"]["z"] == [0.0, 3.0]

    # 4. Sampling Specifications (192 x 96 x 64, cell-centered D/16)
    sampling = cfg["world_model_sampling"]
    assert sampling["grid_resolution"]["nx"] == 192
    assert sampling["grid_resolution"]["ny"] == 96
    assert sampling["grid_resolution"]["nz"] == 64
    assert pytest.approx(D / 16.0) == sampling["cell_spacing_m"]
    assert sampling["time"]["dt_physical_seconds"] == pytest.approx(0.1 * D / U0)  # 0.06 s


def test_c04_3d_sampling_grid_generation():
    """Verify 3D sampling grid shape, cell centering, and geometric channels."""
    D = 0.30
    grid = create_c04_sampling_grid(D=D)

    assert isinstance(grid, C04GridCoordinates)
    assert grid.shape == (64, 96, 192)
    assert grid.total_points == 192 * 96 * 64
    assert pytest.approx(0.01875) == grid.cell_spacing

    # Bounding check: cell centers must lie inside the open ROI interval
    # x in [-2D, 10D] = [-0.6, 3.0]
    assert grid.x_1d[0] > -0.60
    assert grid.x_1d[-1] < 3.00
    # y in [-3D, 3D] = [-0.9, 0.9]
    assert grid.y_1d[0] > -0.90
    assert grid.y_1d[-1] < 0.90
    # z in [3D, 7D] = [0.9, 2.1]
    assert grid.z_1d[0] > 0.90
    assert grid.z_1d[-1] < 2.10

    # Solid mask & SDF check
    # Cylinder radius R = 0.15 m
    R = D / 2.0
    assert grid.solid_mask.dtype == bool
    # At (0, 0), SDF should be negative (-R)
    # Origin is within the domain; let's check nearest point to (0, 0)
    origin_x_idx = np.argmin(np.abs(grid.x_1d))
    origin_y_idx = np.argmin(np.abs(grid.y_1d))
    assert grid.solid_mask[:, origin_y_idx, origin_x_idx].all()
    assert grid.sdf[:, origin_y_idx, origin_x_idx].max() < 0

    # Far away points should have positive SDF and solid_mask False
    assert not grid.solid_mask[:, -1, -1].any()
    assert grid.sdf[:, -1, -1].min() > 0


def test_c04_2d_slice_grid_generation():
    """Verify 2D slice grid at mid-span z = 5D = 1.5 m."""
    D = 0.30
    slice_grid = create_c04_2d_slice_grid(D=D)

    assert isinstance(slice_grid, C04SliceCoordinates)
    assert slice_grid.shape == (96, 192)
    assert slice_grid.total_points == 96 * 192
    assert slice_grid.z_fixed == pytest.approx(1.50)

    # Check coordinate arrays
    assert slice_grid.X.shape == (96, 192)
    assert slice_grid.Y.shape == (96, 192)
    assert slice_grid.solid_mask.shape == (96, 192)
    assert slice_grid.sdf.shape == (96, 192)


def test_export_sampling_coordinates_csv(tmp_path):
    """Test exporting coordinates to CSV for COMSOL Data Export."""
    grid = create_c04_2d_slice_grid(D=0.30)
    out_csv = tmp_path / "sampling_coords.csv"

    exported = export_sampling_coordinates_csv(grid, out_csv)
    assert exported.is_file()

    # Read back lines
    lines = exported.read_text(encoding="utf-8").strip().splitlines()
    assert lines[0].startswith("% x [m]")
    # 1 header + 96*192 data rows
    assert len(lines) == 1 + 96 * 192


@pytest.mark.artifact
def test_c04_benchmark_mph_file_exists():
    """Verify generated C04 benchmark model exists and has clean size."""
    mph_path = Path("models/cylinder_3d/c04_re126k_benchmark.mph")
    assert mph_path.is_file(), f"C04 benchmark MPH file does not exist: {mph_path}"

    size_bytes = mph_path.stat().st_size
    # Size must be compact (< 15 MB) because history solution data is cleared
    assert 1_000_000 < size_bytes < 15_000_000, f"Unexpected C04 MPH size: {size_bytes} bytes"


def test_c04_dataset_save_and_load_roundtrip(tmp_path):
    """Test saving and loading C04 dataset for 2D slice and 3D volume."""
    from hydro_disturb_bench.sampling_3d import load_c04_dataset, save_c04_dataset

    # 1. Test 2D slice dataset roundtrip
    grid_2d = create_c04_2d_slice_grid(D=0.30)
    t_steps = 5
    times = np.linspace(0.0, 0.24, t_steps, dtype=np.float32)
    fields_2d = np.ones((t_steps, 4, 96, 192), dtype=np.float32) * 0.5

    out_2d_npz = tmp_path / "c04_slice_test.npz"
    saved_path = save_c04_dataset(out_2d_npz, fields_2d, grid_2d, times)
    assert saved_path.is_file()

    loaded = load_c04_dataset(saved_path)
    assert loaded["fields"].shape == (t_steps, 4, 96, 192)
    assert np.allclose(loaded["fields"], fields_2d)
    assert np.allclose(loaded["times"], times)
    assert loaded["solid_mask"].shape == (96, 192)
    assert loaded["metadata"]["case_id"] == "C04"
    assert loaded["metadata"]["is_3d"] is False
    assert loaded["metadata"]["channels"] == ["u", "v", "w", "p"]

    # 2. Test 3D dataset roundtrip
    grid_3d = create_c04_sampling_grid(D=0.30)
    times_3d = np.array([0.0, 0.06], dtype=np.float32)
    fields_3d = np.zeros((2, 4, 64, 96, 192), dtype=np.float32)

    out_3d_npz = tmp_path / "c04_3d_test.npz"
    saved_3d_path = save_c04_dataset(out_3d_npz, fields_3d, grid_3d, times_3d)
    assert saved_3d_path.is_file()

    loaded_3d = load_c04_dataset(saved_3d_path)
    assert loaded_3d["fields"].shape == (2, 4, 64, 96, 192)
    assert loaded_3d["solid_mask"].shape == (64, 96, 192)
    assert loaded_3d["metadata"]["is_3d"] is True

    # 3. Validation rejection
    with pytest.raises(ValueError, match="does not match expected"):
        # Wrong shape
        save_c04_dataset(tmp_path / "err.npz", fields_3d[:1, :3], grid_3d, times_3d[:1])

    with pytest.raises(ValueError, match="non-finite"):
        bad_fields = fields_2d.copy()
        bad_fields[0, 0, 0, 0] = np.nan
        save_c04_dataset(tmp_path / "err_nan.npz", bad_fields, grid_2d, times)


def test_c04_export_rejects_coordinate_mismatch(tmp_path, monkeypatch):
    """Verify export fails when COMSOL output coordinates do not match canonical grid."""
    from hydro_disturb_bench.comsol_java import bridge
    from hydro_disturb_bench.comsol_java.errors import ModelValidationError

    dummy_model = tmp_path / "dummy.mph"
    dummy_model.write_text("dummy", encoding="utf-8")
    out_npz = tmp_path / "slice.npz"

    # Simulate export producing misaligned grid
    def fake_run_bridge(args, root=None):
        out_txt = Path(args[2])
        grid = create_c04_2d_slice_grid(D=0.30)
        x_bad = np.linspace(-0.6, 3.0, 192)
        y_good = grid.y_1d
        lines = ["% Header line"]
        for y in y_good:
            for x in x_bad:
                lines.append(f"{x:.6f} {y:.6f} 0.5 0.0 0.0 100.0")
        out_txt.write_text("\n".join(lines), encoding="utf-8")

    monkeypatch.setattr(bridge, "_run_bridge", fake_run_bridge)

    with pytest.raises(ModelValidationError, match="coordinates do not match canonical grid"):
        bridge.export_c04_slice_dataset(dummy_model, out_npz, allow_overwrite=True)


def test_c04_export_rejects_fluid_domain_nan(tmp_path, monkeypatch):
    """Verify export fails when fluid domain contains NaN or Inf, refusing to mask with nan_to_num."""
    from hydro_disturb_bench.comsol_java import bridge
    from hydro_disturb_bench.comsol_java.errors import ModelValidationError

    dummy_model = tmp_path / "dummy.mph"
    dummy_model.write_text("dummy", encoding="utf-8")
    out_npz = tmp_path / "slice.npz"

    def fake_run_bridge(args, root=None):
        out_txt = Path(args[2])
        grid = create_c04_2d_slice_grid(D=0.30)
        lines = ["% Header line @ t=0.0"]
        for j, y in enumerate(grid.y_1d):
            for i, x in enumerate(grid.x_1d):
                if i == 50 and j == 50:
                    val = "nan"
                else:
                    val = "0.500000"
                lines.append(f"{x:.6f} {y:.6f} {val} 0.0 0.0 100.0")
        out_txt.write_text("\n".join(lines), encoding="utf-8")

    monkeypatch.setattr(bridge, "_run_bridge", fake_run_bridge)

    with pytest.raises(ModelValidationError, match="Fluid domain contains non-finite"):
        bridge.export_c04_slice_dataset(dummy_model, out_npz, allow_overwrite=True)


def test_c04_export_rejects_time_mismatch(tmp_path, monkeypatch):
    """Verify export fails when physical time in COMSOL header comments differs from expected time."""
    from hydro_disturb_bench.comsol_java import bridge
    from hydro_disturb_bench.comsol_java.errors import ModelValidationError

    dummy_model = tmp_path / "dummy.mph"
    dummy_model.write_text("dummy", encoding="utf-8")
    out_npz = tmp_path / "slice.npz"

    def fake_run_bridge(args, root=None):
        out_txt = Path(args[2])
        grid = create_c04_2d_slice_grid(D=0.30)
        lines = ["% Header with @ t=0.50"]
        for y in grid.y_1d:
            for x in grid.x_1d:
                lines.append(f"{x:.6f} {y:.6f} 0.5 0.0 0.0 100.0")
        out_txt.write_text("\n".join(lines), encoding="utf-8")

    monkeypatch.setattr(bridge, "_run_bridge", fake_run_bridge)

    with pytest.raises(ModelValidationError, match="does not match expected"):
        bridge.export_c04_slice_dataset(dummy_model, out_npz, time_s=0.0, allow_overwrite=True)


def test_c04_atomic_save_preserves_old_file_on_failure(tmp_path):
    """Verify atomic transaction preserves original NPZ if new export/save fails."""
    from hydro_disturb_bench.sampling_3d import load_c04_dataset, save_c04_dataset

    grid = create_c04_2d_slice_grid(D=0.30)
    target = tmp_path / "protected.npz"
    times = np.array([0.0], dtype=np.float32)

    orig_fields = np.ones((1, 4, 96, 192), dtype=np.float32) * 42.0
    save_c04_dataset(target, orig_fields, grid, times, allow_overwrite=True)
    orig_bytes = target.read_bytes()

    bad_fields = orig_fields.copy()
    bad_fields[0, 0, 10, 10] = np.nan

    with pytest.raises(ValueError, match="non-finite"):
        save_c04_dataset(target, bad_fields, grid, times, allow_overwrite=True)

    assert target.is_file()
    assert target.read_bytes() == orig_bytes
    loaded = load_c04_dataset(target)
    assert np.allclose(loaded["fields"], orig_fields)


def test_c04_atomic_save_refuses_overwrite_when_forbidden(tmp_path):
    """Verify save_c04_dataset refuses overwrite when allow_overwrite=False."""
    from hydro_disturb_bench.sampling_3d import save_c04_dataset

    grid = create_c04_2d_slice_grid(D=0.30)
    target = tmp_path / "no_overwrite.npz"
    times = np.array([0.0], dtype=np.float32)
    fields = np.zeros((1, 4, 96, 192), dtype=np.float32)

    save_c04_dataset(target, fields, grid, times, allow_overwrite=True)
    assert target.is_file()

    with pytest.raises(FileExistsError, match="already exists and overwrite is forbidden"):
        save_c04_dataset(target, fields, grid, times, allow_overwrite=False)


@pytest.mark.artifact
def test_c04_s0_solved_model_and_datasets():
    """Verify solved Stage 1 model and exported physical slice/volume datasets."""
    from hydro_disturb_bench.sampling_3d import load_c04_dataset

    s0_model = Path("models/cylinder_3d/c04_re126k_s0_solved.mph")
    assert s0_model.is_file(), f"Stage 1 model missing: {s0_model}"
    assert s0_model.stat().st_size > 10_000_000

    slice_npz = Path("runs/c04_s0_slice.npz")
    assert slice_npz.is_file(), f"Stage 1 slice dataset missing: {slice_npz}"
    ds = load_c04_dataset(slice_npz)
    assert ds["fields"].shape == (1, 4, 96, 192)
    assert ds["metadata"]["is_3d"] is False
    assert np.isfinite(ds["fields"]).all()
    assert ds["solid_mask"].sum() == 208
    # Inflow speed is 0.5 m/s; accelerated flow over cylinder exceeds 0.6 m/s
    assert ds["fields"][0, 0].max() > 0.60
    # Stagnation pressure around 128 Pa
    assert ds["fields"][0, 3].max() > 100.0

    vol_npz = Path("runs/c04_s0_volume.npz")
    assert vol_npz.is_file(), f"Stage 1 volume dataset missing: {vol_npz}"
    ds_vol = load_c04_dataset(vol_npz)
    assert ds_vol["fields"].shape == (1, 4, 64, 96, 192)
    assert ds_vol["metadata"]["is_3d"] is True
    assert np.isfinite(ds_vol["fields"]).all()
    assert ds_vol["solid_mask"].sum() == 13312
    assert ds_vol["fields"][0, 0].max() > 0.60


@pytest.mark.artifact
def test_c04_s1_solved_model_and_sequences():
    """Verify solved Stage 2 transient model and multi-frame sequence datasets."""
    from hydro_disturb_bench.sampling_3d import load_c04_dataset

    s1_model = Path("models/cylinder_3d/c04_re126k_s1_trial.mph")
    assert s1_model.is_file(), f"Stage 2 model missing: {s1_model}"
    assert s1_model.stat().st_size > 200_000_000

    slice_seq = Path("runs/c04_s1_slice_sequence.npz")
    assert slice_seq.is_file(), f"Stage 2 slice sequence missing: {slice_seq}"
    ds = load_c04_dataset(slice_seq)
    assert ds["fields"].shape == (4, 4, 96, 192)
    assert ds["metadata"]["is_3d"] is False
    assert np.isfinite(ds["fields"]).all()
    assert np.allclose(ds["times"], [0.0, 0.06, 0.12, 0.18])
    assert ds["solid_mask"].shape == (96, 192)
    assert ds["solid_mask"].sum() == 208
    # Velocity develops with vortex shedding
    assert ds["fields"][:, 0].max() > 0.65
    assert ds["fields"][:, 1].max() > 0.30

    vol_seq = Path("runs/c04_s1_volume_sequence.npz")
    assert vol_seq.is_file(), f"Stage 2 volume sequence missing: {vol_seq}"
    ds_vol = load_c04_dataset(vol_seq)
    assert ds_vol["fields"].shape == (4, 4, 64, 96, 192)
    assert ds_vol["metadata"]["is_3d"] is True
    assert np.isfinite(ds_vol["fields"]).all()
    assert np.allclose(ds_vol["times"], [0.0, 0.06, 0.12, 0.18])
    assert ds_vol["solid_mask"].shape == (64, 96, 192)
    assert ds_vol["solid_mask"].sum() == 13312
    assert ds_vol["fields"][:, 0].max() > 0.65
    assert ds_vol["fields"][:, 1].max() > 0.30
