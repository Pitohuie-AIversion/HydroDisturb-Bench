"""Unit tests for self-contained benchmark dataset packaging and verification."""

import json
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.benchmark_bundle import (
    create_benchmark_bundle,
    load_benchmark_bundle,
    verify_benchmark_bundle,
)
from hydro_disturb_bench.comsol_java.errors import ModelValidationError


def test_bundle_creation_and_loading(tmp_path: Path):
    """Verify end-to-end benchmark bundle creation, verification, and loading."""
    out_bundle = tmp_path / "c04_mini_benchmark.npz"

    times = np.array([180.0, 180.2, 180.4, 180.6])
    x = np.linspace(-0.6, 3.0, 32)
    y = np.linspace(-0.9, 0.9, 16)
    values = np.random.normal(0.0, 1.0, size=(4, 3, 16, 32))  # (T, C, Ny, Nx)

    solid_mask = np.zeros((16, 32), dtype=np.int8)
    solid_mask[7:9, 5:7] = 1  # Simulated obstacle

    layout_info = json.dumps({"name": "test_rake", "num_probes": 10})
    dist_info = json.dumps({"active": True, "amplitude": 0.005, "t_on": 180.2})

    bundle_path = create_benchmark_bundle(
        output_path=out_bundle,
        times=times,
        coords={"x": x, "y": y},
        values=values,
        channels=["u", "v", "p"],
        solid_mask=solid_mask,
        layout_json=layout_info,
        disturbance_json=dist_info,
        benchmark_name="Test-Suite-2D",
    )

    assert bundle_path.is_file()

    # Verify bundle cryptographic integrity
    ver = verify_benchmark_bundle(bundle_path)
    assert ver["status"] == "VALID"
    assert ver["dim"] == 2
    assert ver["frames"] == 4
    assert ver["channels"] == ["u", "v", "p"]

    # Load bundle into memory
    loaded = load_benchmark_bundle(bundle_path)
    np.testing.assert_array_equal(loaded["times"], times)
    np.testing.assert_array_equal(loaded["values"], values)
    np.testing.assert_array_equal(loaded["coords"]["x"], x)
    np.testing.assert_array_equal(loaded["coords"]["y"], y)
    np.testing.assert_array_equal(loaded["solid_mask"], solid_mask)
    assert loaded["manifest"].benchmark_name == "Test-Suite-2D"
    assert loaded["manifest"].has_disturbance is True


def test_tamper_detection(tmp_path: Path):
    """Ensure that modifying array data triggers checksum mismatch and raises ModelValidationError."""
    out_bundle = tmp_path / "tamper_test.npz"

    times = np.array([0.0, 0.5, 1.0])
    x = np.linspace(0, 1, 10)
    y = np.linspace(0, 1, 10)
    values = np.ones((3, 3, 10, 10))

    create_benchmark_bundle(
        output_path=out_bundle,
        times=times,
        coords={"x": x, "y": y},
        values=values,
    )

    # Load archive data, tamper with 'values', and re-save
    with np.load(out_bundle, allow_pickle=True) as data:
        tampered_dict = {k: data[k] for k in data.files}

    # Deliberately modify one element in values array without updating manifest
    tampered_dict["values"][0, 0, 0, 0] = 999.0
    np.savez_compressed(out_bundle, **tampered_dict)

    # Verification must catch the checksum alteration
    with pytest.raises(ModelValidationError, match="Checksum mismatch for array 'values'"):
        verify_benchmark_bundle(out_bundle)


def test_non_monotonic_times_rejected(tmp_path: Path):
    """Ensure non-monotonic timestamps are strictly rejected during bundle creation."""
    out_bundle = tmp_path / "bad_times.npz"
    bad_times = np.array([10.0, 9.0, 11.0])
    values = np.zeros((3, 3, 5, 5))

    with pytest.raises(ModelValidationError, match="monotonically increasing"):
        create_benchmark_bundle(
            output_path=out_bundle,
            times=bad_times,
            coords={"x": np.arange(5), "y": np.arange(5)},
            values=values,
        )


def test_cli_bundle_and_eval_subcommands(tmp_path: Path):
    """Verify that bundle-create, bundle-verify, vorticity-eval, and inversion-eval CLI commands execute successfully."""
    from hydro_disturb_bench.cli import main

    # 1. Prepare raw dataset NPZ
    raw_npz_path = tmp_path / "raw_flow.npz"
    times = np.array([180.0, 180.2])
    x = np.linspace(-0.6, 2.0, 20)
    y = np.linspace(-0.6, 0.6, 12)
    data = np.zeros((2, 3, 12, 20), dtype=float)
    data[:, 0] = 0.50  # u = 0.50 m/s
    np.savez_compressed(raw_npz_path, times=times, x=x, y=y, data=data)

    # 2. Test bundle-create CLI
    bundle_path = tmp_path / "bundle.npz"
    res_create = main(["bundle-create", "--dataset", str(raw_npz_path), "--out", str(bundle_path)])
    assert res_create == 0
    assert bundle_path.is_file()

    # 3. Test bundle-verify CLI
    res_verify = main(["bundle-verify", "--bundle", str(bundle_path)])
    assert res_verify == 0

    # 4. Test vorticity-eval CLI
    vort_report_path = tmp_path / "vort_report.json"
    res_vort = main(["vorticity-eval", "--dataset", str(raw_npz_path), "--out", str(vort_report_path)])
    assert res_vort == 0
    assert vort_report_path.is_file()
    vort_data = json.loads(vort_report_path.read_text(encoding="utf-8"))
    assert vort_data["dim"] == 2
    assert "incompressibility" in vort_data

    # 5. Test inversion-eval CLI
    truth_file = tmp_path / "truth.json"
    pred_file = tmp_path / "pred.json"
    truth_file.write_text(json.dumps({"active": True, "t_on": 181.0, "t_off": 183.0, "center": [0.15, 0.12], "amplitude": 0.005}))
    pred_file.write_text(json.dumps({"detected": True, "t_detect": 181.1, "t_on": 181.0, "t_off": 183.0, "center": [0.15, 0.12], "amplitude": 0.005}))
    inv_report_path = tmp_path / "inv_report.json"
    res_inv = main(["inversion-eval", "--truth", str(truth_file), "--pred", str(pred_file), "--out", str(inv_report_path)])
    assert res_inv == 0
    assert inv_report_path.is_file()
    inv_data = json.loads(inv_report_path.read_text(encoding="utf-8"))
    assert inv_data["detected_correctly"] is True
    assert inv_data["composite_inversion_score"] == pytest.approx(1.0, abs=1e-4)

