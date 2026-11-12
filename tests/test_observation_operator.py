"""Unit tests for observation operators, sensor layouts, and probabilistic reconstruction metrics."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from hydro_disturb_bench.c04_disturbance import (
    C04DisturbanceConfig,
    evaluate_c04_disturbance_field,
    generate_c04_comsol_force_expressions,
)
from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.observation_operator import (
    ObservationOperator,
    SensorLayout,
    make_auv_patrol_path,
    make_cylinder_surface_probes_2d,
    make_downstream_rake_2d,
    make_downstream_rake_3d,
)
from hydro_disturb_bench.probabilistic_metrics import (
    compute_crps_ensemble,
    compute_spread_skill_ratio,
    evaluate_field_reconstruction,
)


def test_sensor_layout_serialization(tmp_path: Path):
    """Verify standard sensor layouts can be generated, saved to JSON, and reloaded identically."""
    rake2d = make_downstream_rake_2d(
        x_stations=[0.10, 0.20],
        y_min=0.05,
        y_max=0.15,
        n_probes_per_station=5,
    )
    assert rake2d.num_probes == 10
    assert rake2d.dim == 2

    json_path = tmp_path / "rake2d.json"
    rake2d.save_json(json_path)
    loaded = SensorLayout.load_json(json_path)

    assert loaded.name == rake2d.name
    assert loaded.num_probes == rake2d.num_probes
    assert loaded.dim == rake2d.dim
    assert loaded.probes[0].probe_id == rake2d.probes[0].probe_id
    assert loaded.probes[0].coords == rake2d.probes[0].coords

    # 3D rake
    rake3d = make_downstream_rake_3d(
        x_stations=[1.0, 2.0],
        y_min=-0.5,
        y_max=0.5,
        z_stations=[0.5, 1.5],
        n_probes_y=3,
    )
    assert rake3d.num_probes == 2 * 2 * 3
    assert rake3d.dim == 3

    # Surface probes
    surf = make_cylinder_surface_probes_2d(
        center_x=0.0,
        center_y=0.0,
        radius=0.15,
        n_sensors=8,
    )
    assert surf.num_probes == 8

    # AUV trajectory
    auv, t_samples = make_auv_patrol_path(
        start_coords=(0.0, 0.0),
        end_coords=(1.0, 1.0),
        speed_m_s=0.5,
        t_start=10.0,
        sample_dt=0.5,
    )
    assert auv.num_probes >= 2
    assert len(t_samples) == auv.num_probes


def test_observation_operator_2d_interpolation():
    """Verify 2D bilinear interpolation from a known synthetic flow field."""
    x = np.linspace(0.0, 1.0, 11)  # dx = 0.1
    y = np.linspace(0.0, 1.0, 11)  # dy = 0.1
    Y, X = np.meshgrid(y, x, indexing="ij")

    # Analytical field: u = 2*x + y, v = -x + 3*y, p = 100.0
    u_field = 2.0 * X + Y
    v_field = -1.0 * X + 3.0 * Y
    p_field = np.full_like(X, 100.0)

    # Shape: (T=2, C=3, Ny=11, Nx=11)
    grid_data = np.stack(
        [
            np.stack([u_field, v_field, p_field], axis=0),
            np.stack([u_field * 2.0, v_field * 2.0, p_field], axis=0),
        ],
        axis=0,
    )

    rake = make_downstream_rake_2d(
        x_stations=[0.25, 0.75],
        y_min=0.35,
        y_max=0.65,
        n_probes_per_station=2,
    )
    operator = ObservationOperator(rake)
    sampled = operator.sample_2d_field(grid_data, x, y, channel_names=("u", "v", "p"))

    # Expected shape: (T=2, num_probes=4, C=3)
    assert sampled.shape == (2, 4, 3)

    coords = rake.probe_coordinates()
    for p_idx, (px, py) in enumerate(coords):
        expected_u_t0 = 2.0 * px + py
        expected_v_t0 = -1.0 * px + 3.0 * py
        np.testing.assert_allclose(sampled[0, p_idx, 0], expected_u_t0, rtol=1e-5)
        np.testing.assert_allclose(sampled[0, p_idx, 1], expected_v_t0, rtol=1e-5)
        np.testing.assert_allclose(sampled[0, p_idx, 2], 100.0, rtol=1e-5)

        # t=1 has scaled velocities
        np.testing.assert_allclose(sampled[1, p_idx, 0], expected_u_t0 * 2.0, rtol=1e-5)


def test_observation_operator_3d_interpolation():
    """Verify 3D trilinear interpolation from a known linear field."""
    x = np.linspace(0.0, 1.0, 6)
    y = np.linspace(0.0, 1.0, 6)
    z = np.linspace(0.0, 1.0, 6)
    Z, Y, X = np.meshgrid(z, y, x, indexing="ij")

    # u = x + 2*y + 3*z
    u_field = X + 2.0 * Y + 3.0 * Z
    v_field = X * 0.0
    w_field = Y * 0.0
    p_field = np.full_like(X, 50.0)

    # Shape: (C=4, Nz=6, Ny=6, Nx=6)
    grid_3d = np.stack([u_field, v_field, w_field, p_field], axis=0)

    rake3d = make_downstream_rake_3d(
        x_stations=[0.3],
        y_min=0.4,
        y_max=0.4,
        z_stations=[0.5],
        n_probes_y=1,
    )
    operator = ObservationOperator(rake3d)
    sampled = operator.sample_3d_field(grid_3d, x, y, z)

    assert sampled.shape == (1, 4)
    expected_u = 0.3 + 2.0 * 0.4 + 3.0 * 0.5
    np.testing.assert_allclose(sampled[0, 0], expected_u, rtol=1e-5)
    np.testing.assert_allclose(sampled[0, 3], 50.0, rtol=1e-5)


def test_sensor_noise_injection():
    """Verify realistic measurement noise generation."""
    data = np.ones((10, 16, 3), dtype=np.float64) * 5.0
    noisy_snr = ObservationOperator.add_sensor_noise(data, snr_db=20.0, drift_bias=0.1)
    assert noisy_snr.shape == data.shape
    assert not np.allclose(noisy_snr, data)
    assert np.mean(noisy_snr) > 5.0  # reflects drift bias

    noisy_rel = ObservationOperator.add_sensor_noise(data, relative_sigma=0.05)
    assert noisy_rel.shape == data.shape


def test_crps_and_spread_skill_ratio():
    """Verify CRPS scoring and Spread-Skill Ratio for ensemble forecasts."""
    ground_truth = np.array([1.0, 2.0, 3.0, 4.0])
    mask = np.array([True, True, True, False])

    # 1. Deterministic case (M=1) -> CRPS == MAE
    single_pred = np.array([[1.5, 2.0, 2.5, 99.0]])  # M=1
    crps_single = compute_crps_ensemble(single_pred, ground_truth, fluid_mask=mask)
    # diff on mask: |1.5-1|=0.5, |2-2|=0, |2.5-3|=0.5 -> mean = 1.0 / 3
    assert abs(crps_single - (1.0 / 3.0)) < 1e-6

    # 2. Perfect ensemble -> CRPS == 0
    perfect_ens = np.repeat(ground_truth[None, :], 5, axis=0)
    crps_perfect = compute_crps_ensemble(perfect_ens, ground_truth, fluid_mask=mask)
    assert crps_perfect == 0.0

    # 3. Spread-skill ratio
    ssr = compute_spread_skill_ratio(perfect_ens, ground_truth, fluid_mask=mask)
    assert ssr == 1.0

    # Dispersed ensemble
    ens = np.array([
        [0.8, 1.8, 2.8, 0.0],
        [1.2, 2.2, 3.2, 0.0],
    ])
    ssr_disp = compute_spread_skill_ratio(ens, ground_truth, fluid_mask=mask)
    assert ssr_disp > 0.0


def test_field_reconstruction_metrics():
    """Verify fluid-restricted field reconstruction evaluation."""
    true_field = np.ones((3, 10, 10), dtype=np.float64) * 2.0
    pred_field = true_field.copy()
    mask = np.ones((10, 10), dtype=bool)

    metrics_perfect = evaluate_field_reconstruction(
        pred_field, true_field, fluid_mask=mask, channel_names=("u", "v", "p")
    )
    assert metrics_perfect["overall_rel_l2"] == 0.0
    assert metrics_perfect["u"]["rel_l2"] == 0.0
    assert metrics_perfect["u"]["psnr_db"] == 100.0

    # Perturb channel u
    pred_field[0] += 0.2
    metrics_pert = evaluate_field_reconstruction(
        pred_field, true_field, fluid_mask=mask, channel_names=("u", "v", "p")
    )
    assert metrics_pert["u"]["rel_l2"] > 0.0
    assert metrics_pert["v"]["rel_l2"] == 0.0


def test_moving_disturbance_source():
    """Verify moving disturbance source trajectory, COMSOL formula synthesis, and field evaluation."""
    cfg = C04DisturbanceConfig(
        center_m=(0.5, 0.0, 1.5),
        direction=(0.0, 1.0, 0.0),
        acceleration_m_s2=0.05,
        sigma_m=0.08,
        on_s=1.0,
        off_s=3.0,
        velocity_m_s=(0.5, 0.0, 0.0),  # translates downstream at 0.5 m/s
    )

    # Initial center at t=1.0 is (0.5, 0, 1.5)
    assert cfg.center_at_time(1.0) == (0.5, 0.0, 1.5)
    # Mid-time at t=2.0 (dt=1.0) is (1.0, 0, 1.5)
    assert cfg.center_at_time(2.0) == (1.0, 0.0, 1.5)
    # End-time at t=3.0 (dt=2.0) is (1.5, 0, 1.5)
    assert cfg.center_at_time(3.0) == (1.5, 0.0, 1.5)

    # COMSOL formula expressions
    exprs = generate_c04_comsol_force_expressions(cfg)
    assert "Fx" in exprs and "Fy" in exprs and "Fz" in exprs
    assert "0.500000[m/s]*(t-1.0000[s])" in exprs["Fy"]

    from hydro_disturb_bench.sampling_3d import create_c04_2d_slice_grid

    grid = create_c04_2d_slice_grid(D=0.30, z_fixed=1.5)
    field_t2 = evaluate_c04_disturbance_field(cfg, grid, time_s=2.0)
    assert field_t2.shape == (3, *grid.shape)
    # Center moved to (1.0, 0.0, 1.5) -> peak acceleration near x=1.0, y=0.0
    ix = np.argmin(np.abs(grid.x_1d - 1.0))
    iy = np.argmin(np.abs(grid.y_1d - 0.0))
    peak_ay = field_t2[1, iy, ix]
    assert peak_ay > 0.04  # Near max amplitude 0.05 m/s^2


def test_cli_observation_commands(tmp_path: Path):
    """Verify CLI observation-plan and observation-sample commands."""
    layout_file = tmp_path / "test_rake_layout.json"
    ret = cli_main(["observation-plan", "--type", "rake-2d", "--out", str(layout_file)])
    assert ret == 0
    assert layout_file.is_file()

    # Create dummy 2D dataset
    x = np.linspace(0.0, 0.5, 20)
    y = np.linspace(0.0, 0.3, 15)
    data = np.ones((2, 3, 15, 20), dtype=np.float64)
    dataset_file = tmp_path / "dummy_2d.npz"
    np.savez_compressed(
        dataset_file,
        data=data,
        x=x,
        y=y,
        channel_names=np.array(["u", "v", "p"]),
    )

    sampled_out = tmp_path / "sampled_observations.json"
    ret_sample = cli_main([
        "observation-sample",
        "--layout",
        str(layout_file),
        "--dataset",
        str(dataset_file),
        "--out",
        str(sampled_out),
        "--snr",
        "25.0",
    ])
    assert ret_sample == 0
    assert sampled_out.is_file()
    sample_info = json.loads(sampled_out.read_text(encoding="utf-8"))
    assert sample_info["num_probes"] > 0
    assert sample_info["snr_db"] == 25.0

    # Reconstruction evaluation CLI
    recon_out = tmp_path / "recon_metrics.json"
    ret_recon = cli_main([
        "reconstruction-eval",
        "--pred",
        str(dataset_file),
        "--true",
        str(dataset_file),
        "--out",
        str(recon_out),
    ])
    assert ret_recon == 0
    assert recon_out.is_file()
    metrics = json.loads(recon_out.read_text(encoding="utf-8"))
    assert metrics["overall_rel_l2"] == 0.0
