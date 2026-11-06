"""Tests for Stage S1-A2 (200s) long-time wake stability, cycle statistics, and diagnostics."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from hydro_disturb_bench.comsol_java import (
    compute_file_hash,
)
from hydro_disturb_bench.io import (
    ComsolExportValidationError,
    load_comsol_1d_timeseries,
    load_comsol_vorticity_grid,
    validate_s1_diagnostics,
)

BASELINE_CASE = Path("case/cylinder_flow.mph")
BENCHMARK_MODEL = Path("models/cylinder_2d/re100_benchmark.mph")
S1A_SOLVED_MPH = Path("runs/re100_s1a_trial/re100_s1a_solved.mph")
S1A2_DIR = Path("runs/re100_s1a2_trial")
S1A2_SOLVED_MPH = S1A2_DIR / "re100_s1a2_solved.mph"

EXPECTED_CASE_HASH = "CE72D06EB181ACA2D152505A138F472B7EF424FE7A2CC20C900C670002F00A7A"
EXPECTED_S0_HASH = "3087119AF52A8B59DBBA4500F370A46044E71BC17CDD031A53710A74FBA0BEB0"
EXPECTED_S1A_HASH = "0BB3354255DA95F60622DAFDFDA11EB131383ECD3F9BA113B980D363CB0520DC"

U_INF = 0.010058104588
D = 0.01


@pytest.mark.artifact
def test_baseline_and_s1a_models_immutability():
    """Verify that original baseline, S0 benchmark, and S1-A solved models are strictly unmodified."""
    assert BASELINE_CASE.is_file(), f"Missing {BASELINE_CASE}"
    assert BENCHMARK_MODEL.is_file(), f"Missing {BENCHMARK_MODEL}"
    assert S1A_SOLVED_MPH.is_file(), f"Missing {S1A_SOLVED_MPH}"

    h_case = compute_file_hash(BASELINE_CASE)
    h_s0 = compute_file_hash(BENCHMARK_MODEL)
    h_s1a = compute_file_hash(S1A_SOLVED_MPH)

    assert h_case == EXPECTED_CASE_HASH, f"Baseline case mutated: {h_case} != {EXPECTED_CASE_HASH}"
    assert h_s0 == EXPECTED_S0_HASH, f"S0 benchmark mutated: {h_s0} != {EXPECTED_S0_HASH}"
    assert h_s1a == EXPECTED_S1A_HASH, f"S1-A solved model mutated: {h_s1a} != {EXPECTED_S1A_HASH}"


@pytest.mark.artifact
def test_s1a2_solution_time_table_alignment():
    """Verify that S1-A2 solved model metadata matches the 200s extended schedule."""
    import json

    meta_file = S1A2_DIR / "solver_metadata.json"
    assert meta_file.is_file(), f"Missing solver metadata file: {meta_file}"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))

    t_cfg = meta.get("time_config", {})
    assert t_cfg.get("t_start") == 0.0
    assert t_cfg.get("t_final") == 200.0
    assert t_cfg.get("dt") == 0.20
    assert t_cfg.get("num_steps") == 1001

    sol_stats = meta.get("solution_stats", {})
    assert sol_stats.get("num_timesteps") == 1001
    assert sol_stats.get("time_min") == 0.0
    assert sol_stats.get("time_max") == 200.0
    assert sol_stats.get("has_nan_or_inf") is False
    assert sol_stats.get("has_physical_field") is True

    s_settings = meta.get("solver_settings", {})
    assert s_settings.get("timemethod") == "bdf"
    assert s_settings.get("maxorder") == 2
    assert s_settings.get("rtol") == 0.001


@pytest.mark.artifact
def test_s1a2_diagnostics_data_contract_and_manifest():
    """Verify full data contract, timestamp alignment, and manifest token for S1-A2 diagnostics."""
    assert S1A2_DIR.is_dir(), f"Missing S1-A2 diagnostics directory: {S1A2_DIR}"
    val = validate_s1_diagnostics(
        S1A2_DIR,
        expected_steps=1001,
        expected_dt=0.20,
        expected_t_final=200.0,
        require_manifest=True,
    )
    assert val.get("valid") is True
    assert val.get("num_steps") == 1001
    assert val.get("time_range") == [0.0, 200.0]
    assert len(val.get("series", {})) == 7
    assert len(val.get("snapshots", [])) == 5
    assert val.get("manifest", {}).get("status") == "COMPLETED"


def test_s1a2_synthetic_sine_cycle_detection_correctness():
    """Test cycle extraction algorithm on known synthetic sine signal."""
    t = np.arange(0, 100.0 + 0.1, 0.2)
    # Known signal with period T = 5.0 s (f = 0.2 Hz), amplitude = 1.0
    f_known = 0.20
    t_known = 1.0 / f_known
    a_known = 1.0
    sig = a_known * np.sin(2.0 * np.pi * f_known * t)

    zc = []
    for i in range(len(t) - 1):
        if sig[i] <= 0.0 < sig[i + 1]:
            t_zero = t[i] - sig[i] * (t[i + 1] - t[i]) / (sig[i + 1] - sig[i])
            zc.append(float(t_zero))

    assert len(zc) >= 19
    periods = np.diff(zc)
    mean_t = float(np.mean(periods))
    assert abs(mean_t - t_known) < 1e-4

    # Extract amplitudes per cycle
    for k in range(len(zc) - 1):
        idx = (t >= zc[k]) & (t <= zc[k + 1])
        amp = (np.max(sig[idx]) - np.min(sig[idx])) / 2.0
        assert abs(amp - a_known) < 0.02


def test_growing_amplitude_signal_rejected_as_limit_cycle():
    """Verify that a growing amplitude signal fails the 2% relative drift criterion."""
    t = np.arange(0, 100.0 + 0.1, 0.2)
    f_known = 0.20
    # Growing amplitude from 0.1 to 0.5
    amp_profile = 0.10 + 0.004 * t
    sig = amp_profile * np.sin(2.0 * np.pi * f_known * t)

    zc = []
    for i in range(len(t) - 1):
        if sig[i] <= 0.0 < sig[i + 1]:
            t_zero = t[i] - sig[i] * (t[i + 1] - t[i]) / (sig[i + 1] - sig[i])
            zc.append(float(t_zero))

    amps = []
    for k in range(len(zc) - 1):
        idx = (t >= zc[k]) & (t <= zc[k + 1])
        amps.append((np.max(sig[idx]) - np.min(sig[idx])) / 2.0)

    last5_amps = amps[-5:]
    rel_drift = (max(last5_amps) - min(last5_amps)) / np.mean(last5_amps) * 100.0
    # Must exceed 2% threshold
    assert rel_drift > 2.0, (
        f"Growing amplitude signal was falsely deemed stable: drift={rel_drift:.2f}%"
    )


@pytest.mark.artifact
def test_s1a2_real_data_cycle_by_cycle_statistics_and_stability_thresholds():
    """Verify that real COMSOL S1-A2 data meets the recommended engineering stability criteria over the last 5 cycles."""
    t_cl, cl = load_comsol_1d_timeseries(S1A2_DIR / "lift_coeff.txt")[:2]
    t_cd, cd = load_comsol_1d_timeseries(S1A2_DIR / "drag_coeff.txt")[:2]
    t_wu, wu = load_comsol_1d_timeseries(S1A2_DIR / "wake_u.txt")[:2]
    t_wv, wv = load_comsol_1d_timeseries(S1A2_DIR / "wake_v.txt")[:2]

    # Find zero crossings in t >= 100s
    m_100 = t_cl >= 100.0
    t_late = t_cl[m_100]
    cl_late = cl[m_100]

    zc = []
    for i in range(len(t_late) - 1):
        if cl_late[i] <= 0.0 < cl_late[i + 1]:
            t_zero = t_late[i] - cl_late[i] * (t_late[i + 1] - t_late[i]) / (
                cl_late[i + 1] - cl_late[i]
            )
            zc.append(float(t_zero))

    n_cycles = len(zc) - 1
    assert n_cycles >= 10, f"Expected >= 10 complete cycles in [100, 200]s, got {n_cycles}"

    cycle_records = []
    for k in range(n_cycles):
        t_s = zc[k]
        t_e = zc[k + 1]
        T_k = t_e - t_s
        st_k = (1.0 / T_k) * D / U_INF
        idx = (t_cl >= t_s) & (t_cl <= t_e)
        cl_amp = (np.max(cl[idx]) - np.min(cl[idx])) / 2.0
        cd_mean = np.mean(cd[idx])
        cycle_records.append({"St": st_k, "Cl_amp": cl_amp, "Cd_mean": cd_mean, "T": T_k})

    last5 = cycle_records[-5:]
    st_vals = [c["St"] for c in last5]
    cl_vals = [c["Cl_amp"] for c in last5]
    cd_vals = [c["Cd_mean"] for c in last5]

    st_drift = (max(st_vals) - min(st_vals)) / np.mean(st_vals) * 100.0
    cl_drift = (max(cl_vals) - min(cl_vals)) / np.mean(cl_vals) * 100.0
    cd_drift = (max(cd_vals) - min(cd_vals)) / np.mean(cd_vals) * 100.0

    # Verify engineering stability criteria
    assert st_drift < 1.0, f"Strouhal drift {st_drift:.3f}% exceeded 1.0% threshold"
    assert cl_drift < 2.0, f"Lift amplitude drift {cl_drift:.3f}% exceeded 2.0% threshold"
    assert cd_drift < 1.0, f"Mean drag drift {cd_drift:.3f}% exceeded 1.0% threshold"

    # Verify wake probe trend in [160, 200]s
    m_160 = t_wu >= 160.0
    poly_wu = np.polyfit(t_wu[m_160], wu[m_160], 1)
    poly_wv = np.polyfit(t_wv[m_160], wv[m_160], 1)
    assert abs(poly_wu[0]) < 1e-4, f"Wake u linear drift {poly_wu[0]:.2e} exceeded tolerance"
    assert abs(poly_wv[0]) < 1e-4, f"Wake v linear drift {poly_wv[0]:.2e} exceeded tolerance"


@pytest.mark.artifact
def test_s1a2_multi_window_statistics_consistency():
    """Verify that Window 1 [120, 160]s and Window 2 [160, 200]s show consistent limit-cycle properties."""
    t_cl, cl = load_comsol_1d_timeseries(S1A2_DIR / "lift_coeff.txt")[:2]
    t_cd, cd = load_comsol_1d_timeseries(S1A2_DIR / "drag_coeff.txt")[:2]

    m_w1 = (t_cl >= 120.0) & (t_cl <= 160.0)
    m_w2 = (t_cl >= 160.0) & (t_cl <= 200.0)

    cd_mean_w1 = np.mean(cd[m_w1])
    cd_mean_w2 = np.mean(cd[m_w2])
    cl_amp_w1 = (np.max(cl[m_w1]) - np.min(cl[m_w1])) / 2.0
    cl_amp_w2 = (np.max(cl[m_w2]) - np.min(cl[m_w2])) / 2.0

    cd_drift = abs(cd_mean_w2 - cd_mean_w1) / cd_mean_w1 * 100.0
    cl_drift = abs(cl_amp_w2 - cl_amp_w1) / cl_amp_w1 * 100.0

    assert cd_drift < 1.0, f"Window Cd drift {cd_drift:.3f}% exceeded 1.0%"
    assert cl_drift < 2.0, f"Window Cl amplitude drift {cl_drift:.3f}% exceeded 2.0%"


@pytest.mark.artifact
def test_s1a_legacy_100s_interface_unaffected():
    """Verify that validating existing S1-A 100s results remains 100% functional and backward-compatible."""
    s1a_dir = Path("runs/re100_s1a_trial")
    assert s1a_dir.is_dir()
    val = validate_s1_diagnostics(
        s1a_dir,
        expected_steps=501,
        expected_dt=0.20,
        expected_t_final=100.0,
        require_manifest=True,
    )
    assert val.get("valid") is True
    assert val.get("num_steps") == 501
    assert len(val.get("snapshots", [])) == 4
    assert val.get("manifest", {}).get("status") == "COMPLETED"


@pytest.mark.artifact
def test_s1a2_upgraded_vorticity_snapshots_and_comparison():
    """Verify that upgraded wake-zoom vorticity snapshots and t=195s comparison figure exist and meet quality specs."""
    from PIL import Image

    s1a2_dir = Path("runs/re100_s1a2_trial")
    snapshots = [
        "vorticity_t10s_comsol.png",
        "vorticity_t40s_comsol.png",
        "vorticity_t100s_comsol.png",
        "vorticity_t195s_comsol.png",
        "vorticity_t200s_comsol.png",
    ]
    for s in snapshots:
        p = s1a2_dir / s
        assert p.is_file(), f"Missing upgraded snapshot: {s}"
        assert p.stat().st_size > 50_000, f"Snapshot {s} too small ({p.stat().st_size} bytes)"
        with Image.open(p) as img:
            w, h = img.size
            assert w >= 1200, f"Snapshot {s} width {w} < 1200px"
            assert h >= 500, f"Snapshot {s} height {h} < 500px"

    comp_img = s1a2_dir / "re100_s1a2_vorticity_t195s_comparison.png"
    assert comp_img.is_file(), "Missing t=195s old vs upgraded comparison figure"
    assert comp_img.stat().st_size > 100_000, "Comparison figure too small"

    montage_img = s1a2_dir / "re100_s1a2_vorticity_snapshots.png"
    assert montage_img.is_file(), "Missing 5-moment vorticity montage figure"
    assert montage_img.stat().st_size > 100_000, "Montage figure too small"


@pytest.mark.artifact
def test_s1a2_visualization_specification_and_data_contract():
    """Verify that upgraded Karman street visualization adheres to all scientific visualization specifications."""
    stats_file = S1A2_DIR / "re100_s1a2_vorticity_stats.json"
    assert stats_file.is_file(), f"Missing vorticity stats report: {stats_file}"

    stats = json.loads(stats_file.read_text(encoding="utf-8"))

    # 1. Colormap and units
    assert stats.get("colormap") == "RdBu_r", f"Expected RdBu_r colormap, got {stats.get('colormap')}"
    assert stats.get("unit") in ["s^-1", "1/s"], f"Invalid vorticity unit: {stats.get('unit')}"

    # 2. Symmetrical zero-centered limits
    omega_lim = stats.get("omega_lim")
    assert omega_lim == 2.50, f"Expected unified omega_lim=2.50 s^-1, got {omega_lim}"
    color_range = stats.get("color_range")
    assert color_range == [-omega_lim, omega_lim], f"Asymmetric color range: {color_range}"
    assert stats.get("center") == 0.0, f"Non-zero center: {stats.get('center')}"

    # 3. Domain zoom and aspect ratio
    zoom = stats.get("domain_zoom", {})
    assert zoom.get("x_range_m") == [0.08, 0.30]
    assert zoom.get("y_range_m") == [0.07, 0.17]
    assert zoom.get("x_range_D") == [8.0, 30.0]
    assert zoom.get("y_range_D") == [7.0, 17.0]
    assert zoom.get("aspect_ratio") == 1.0, "Physical aspect ratio must be strictly 1:1"

    # 4. Five snapshot moments and raw physical data integrity
    snapshots = stats.get("snapshots", {})
    expected_times = ["10", "40", "100", "195", "200"]
    assert [int(k) for k in sorted(snapshots.keys(), key=int)] == [10, 40, 100, 195, 200]

    for t_str in expected_times:
        snap = snapshots[t_str]
        t_sec = snap.get("t_sec")
        assert float(t_sec) == float(t_str)

        # Raw CFD extrema preserved (not clamped to omega_lim)
        v_min = snap.get("min")
        v_max = snap.get("max")
        assert v_min < -15.0, f"Raw min vorticity altered/clamped: {v_min}"
        assert v_max > 15.0, f"Raw max vorticity altered/clamped: {v_max}"

        # Quantile ordering
        assert snap.get("q90") < snap.get("q95") < snap.get("q99") < snap.get("q995")

        # Color clipping ratio recorded and strictly bounded
        clip_pct = snap.get("clipping_pct_at_2_5")
        assert clip_pct is not None
        assert 0.0 < clip_pct < 1.0, f"Clipping pct {clip_pct}% out of expected range [0, 1%]"

    # 5. Phase alignment check
    # t=195s is near zero-crossing (lift near 0)
    cl_195 = snapshots["195"]["cl_instant"]
    assert abs(cl_195) < 0.05, f"t=195s lift {cl_195} not near zero crossing"

    # t=200s is near trough phase (large negative lift)
    cl_200 = snapshots["200"]["cl_instant"]
    assert cl_200 < -0.25, f"t=200s lift {cl_200} not near trough phase"

    # 6. Overall pooled statistics
    overall = stats.get("overall_stats", {})
    assert overall.get("pooled_q99") > 1.5
    assert overall.get("pooled_q995") > 2.0
    assert overall.get("pooled_clipping_pct_at_2_5") < 0.50, "Pooled clipping exceeded 0.50%"


@pytest.mark.artifact
def test_s1a2_visualization_all_output_artifacts_exist_and_valid():
    """Verify that all required visualization figures (Version A, Version B PNG/PDF, Overview, SymLog, Enhanced) exist and are non-empty."""
    from PIL import Image

    # Version A: 科研讨论版
    p_disc = S1A2_DIR / "re100_s1a2_vorticity_discussion.png"
    p_snap = S1A2_DIR / "re100_s1a2_vorticity_snapshots.png"
    assert p_disc.is_file() and p_disc.stat().st_size > 200_000
    assert p_snap.is_file() and p_snap.stat().st_size > 200_000

    # Version B: 论文展示版
    p_pub_png = S1A2_DIR / "re100_s1a2_vorticity_publication.png"
    p_pub_pdf = S1A2_DIR / "re100_s1a2_vorticity_publication.pdf"
    assert p_pub_png.is_file() and p_pub_png.stat().st_size > 300_000
    assert p_pub_pdf.is_file() and p_pub_pdf.stat().st_size > 50_000

    with Image.open(p_pub_png) as img:
        w, h = img.size
        assert w >= 2500, f"Publication PNG width {w} < 2500px (not high DPI)"
        assert h >= 2000, f"Publication PNG height {h} < 2000px"

    # Auxiliary: Frame-by-frame enhanced display
    p_enh = S1A2_DIR / "re100_s1a2_vorticity_enhanced_individual.png"
    assert p_enh.is_file() and p_enh.stat().st_size > 200_000

    # Auxiliary: SymLog display
    p_sym = S1A2_DIR / "re100_s1a2_vorticity_symlog.png"
    assert p_sym.is_file() and p_sym.stat().st_size > 200_000

    # Full Overview reference
    p_ov = S1A2_DIR / "re100_s1a2_vorticity_full_overview.png"
    assert p_ov.is_file() and p_ov.stat().st_size > 100_000

    # Comparison figure
    p_comp = S1A2_DIR / "re100_s1a2_vorticity_t195s_comparison.png"
    assert p_comp.is_file() and p_comp.stat().st_size > 100_000

    # 5 raw regular grid text files
    for t_s in [10, 40, 100, 195, 200]:
        grid_p = S1A2_DIR / f"vorticity_grid_t{t_s}s.txt"
        assert grid_p.is_file()
        assert grid_p.stat().st_size > 5_000_000, f"Grid file {grid_p.name} too small"


@pytest.mark.artifact
def test_vorticity_grid_real_five_snapshots_strict_admission():
    """Verify that all 5 real COMSOL 2D vorticity grid files pass strict admission criteria."""
    for t_s in [10, 40, 100, 195, 200]:
        grid_file = S1A2_DIR / f"vorticity_grid_t{t_s}s.txt"
        assert grid_file.is_file(), f"Missing grid file: {grid_file}"
        grid = load_comsol_vorticity_grid(
            grid_file,
            expected_time=float(t_s),
            expected_shape=(201, 441),
        )
        assert grid["num_rows"] == 88641
        assert grid["shape"] == [201, 441]
        assert grid["num_unique_points"] == 88641
        assert grid["length_unit"] == "m"
        assert grid["vorticity_unit"] == "1/s"
        assert grid["num_inf"] == 0
        assert grid["num_nan"] > 0
        assert np.isclose(grid["x"][0], 0.08) and np.isclose(grid["x"][-1], 0.30)
        assert np.isclose(grid["y"][0], 0.07) and np.isclose(grid["y"][-1], 0.17)
        assert grid["vorticity"].shape == (201, 441)


def _make_synthetic_grid_content(
    rows: list[list[float]],
    time: float = 10.0,
    nodes: int | None = None,
    length_unit: str = "m",
    expr_unit: str = "1/s",
) -> str:
    n = len(rows) if nodes is None else nodes
    lines = [
        "% Model: synthetic_test.mph",
        "% Version: COMSOL 6.4",
        "% Dimension: 2",
        f"% Nodes: {n}",
        "% Expressions: 1",
        "% Description: Vorticity test grid",
        f"% Length unit: {length_unit}",
        f"% x y vx-uy ({expr_unit}) @ t={time}",
    ]
    for r in rows:
        lines.append(f"{r[0]:.6f} {r[1]:.6f} {r[2]:.6f}")
    return "\n".join(lines) + "\n"


def test_vorticity_grid_rejection_incomplete_rows(tmp_path: Path):
    """Verify rejection when the grid has incomplete row count."""
    p = tmp_path / "incomplete.txt"
    rows = [
        [0.0, 0.0, 1.0],
        [0.01, 0.0, 2.0],
        [0.0, 0.01, 3.0],
    ]
    p.write_text(_make_synthetic_grid_content(rows, nodes=4), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="incomplete grid|row count"):
        load_comsol_vorticity_grid(
            p,
            expected_time=10.0,
            expected_shape=(2, 2),
            x_limits=(0.0, 0.01),
            y_limits=(0.0, 0.01),
            spacing=0.01,
            cylinder_center=(1.0, 1.0),
        )


def test_vorticity_grid_rejection_xy_swapped(tmp_path: Path):
    """Verify rejection when x and y columns are swapped."""
    p = tmp_path / "xy_swapped.txt"
    rows_swapped = [
        [0.0, 0.0, 1.0],
        [0.0, 0.01, 2.0],
        [0.01, 0.0, 3.0],
        [0.01, 0.01, 4.0],
    ]
    p.write_text(_make_synthetic_grid_content(rows_swapped), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="x coordinates are not the expected x-fastest COMSOL order"):
        load_comsol_vorticity_grid(
            p,
            expected_time=10.0,
            expected_shape=(2, 2),
            x_limits=(0.0, 0.01),
            y_limits=(0.0, 0.01),
            spacing=0.01,
            cylinder_center=(1.0, 1.0),
        )


def test_vorticity_grid_rejection_disordered_rows(tmp_path: Path):
    """Verify rejection when grid rows are in reverse or disordered order."""
    p = tmp_path / "disordered.txt"
    valid_rows = [
        [0.0, 0.0, 1.0],
        [0.01, 0.0, 2.0],
        [0.0, 0.01, 3.0],
        [0.01, 0.01, 4.0],
    ]
    p.write_text(_make_synthetic_grid_content(valid_rows[::-1]), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="x coordinates are not the expected x-fastest COMSOL order"):
        load_comsol_vorticity_grid(
            p,
            expected_time=10.0,
            expected_shape=(2, 2),
            x_limits=(0.0, 0.01),
            y_limits=(0.0, 0.01),
            spacing=0.01,
            cylinder_center=(1.0, 1.0),
        )


def test_vorticity_grid_rejection_duplicate_points(tmp_path: Path):
    """Verify rejection when grid contains duplicate spatial coordinates."""
    p = tmp_path / "duplicates.txt"
    dup_rows = [
        [0.0, 0.0, 1.0],
        [0.0, 0.0, 1.0],
        [0.0, 0.01, 3.0],
        [0.01, 0.01, 4.0],
    ]
    p.write_text(_make_synthetic_grid_content(dup_rows), encoding="utf-8")
    with pytest.raises(ComsolExportValidationError, match="x coordinates are not the expected x-fastest COMSOL order|duplicate grid points"):
        load_comsol_vorticity_grid(
            p,
            expected_time=10.0,
            expected_shape=(2, 2),
            x_limits=(0.0, 0.01),
            y_limits=(0.0, 0.01),
            spacing=0.01,
            cylinder_center=(1.0, 1.0),
        )


@pytest.mark.artifact
def test_s1a2_three_panel_comparison_and_clipping_stats():
    """Verify the three-way comparison figure and spatial clipping statistics."""
    from PIL import Image

    # Check 3-panel comparison figure
    p_comp = S1A2_DIR / "re100_s1a2_vorticity_t195s_comparison.png"
    assert p_comp.is_file() and p_comp.stat().st_size > 200_000
    with Image.open(p_comp) as img:
        w, h = img.size
        assert w >= 2000, f"Comparison figure width {w} < 2000"
        assert h >= 2000, f"Comparison figure height {h} < 2000"

    # Check vorticity stats JSON
    stats_file = S1A2_DIR / "re100_s1a2_vorticity_stats.json"
    assert stats_file.is_file()
    stats = json.loads(stats_file.read_text(encoding="utf-8"))

    # Colormap mapping
    c_map = stats.get("color_mapping", {})
    assert c_map.get("negative_values") == "blue side"
    assert c_map.get("zero_values") == "white center"
    assert c_map.get("positive_values") == "red side"

    # Clipping locations
    for t_s in ["10", "40", "100", "195", "200"]:
        snap = stats["snapshots"][t_s]
        clip_loc = snap.get("clip_locations_at_2_5", {})
        assert clip_loc.get("within_2_0D_pct") == 100.0, f"t={t_s}s clipping outside 2D"
        assert clip_loc.get("wake_vortices_clipped_beyond_12D") == 0, f"t={t_s}s wake clipped"
        assert clip_loc.get("max_radius_over_D") <= 2.0, f"t={t_s}s max radius > 2D"
