"""Offline sequence admission through the real parser, batch writer and loader.

Only the external COMSOL call is replaced. Spatially varying analytic fields
make axis/channel/time ordering observable, including at batch boundaries.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java import bridge

BOUNDS = (0.0, 0.4, 0.0, 0.24)
SHAPE = (17, 9)  # Nx, Ny: cheap offline grid, deliberately nonsquare.
CENTER = (0.2, 0.12)
RADIUS = 0.035
REFERENCE_TIMES = (180.0, 185.0, 190.0, 195.0, 200.0)
SAVED_TIMES = np.asarray([0.0, *(float(f"{170 + i / 5:.1f}") for i in range(151))])
BATCH_COMMAND = "export-full-field-sequence-batch"


def _analytic(t, x, y):
    return np.asarray([1 + .01 * t + 2 * x + 3 * y,
                       -1 + .02 * t - 5 * x + 7 * y,
                       100 + .03 * t + 11 * x - 13 * y])


def _write_grid(path, x, y, t, values):
    rows = ["% Length unit: m", "% x y " + " ".join(
        f"{name} ({unit}) @ t={t:.17g}"
        for name, unit in zip(("u", "v", "p"), ("m/s", "m/s", "Pa")))]
    for iy, yy in enumerate(y):
        for ix, xx in enumerate(x):
            rows.append(" ".join(f"{v:.17g}" for v in (xx, yy, *values[:, iy, ix])))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


class AnalyticComsol:
    """Write actual TXT exports and source metadata at the external boundary."""

    def __init__(self, model):
        self.model = model
        self.fault = None
        self.calls = []
        self.responses = []
        self.directories = []

    def __call__(self, args, root=None):
        assert args[0] in ("export-full-field-sample", BATCH_COMMAND)
        assert Path(args[1]).resolve() == self.model.resolve()
        batch = args[0] == BATCH_COMMAND
        if batch:
            self.calls.append(list(args))
        fault = self.fault if batch and len(self.calls) == 2 else None
        directory = Path(args[2])
        directory.mkdir(parents=True, exist_ok=True)
        requested = np.asarray([float(v) for v in args[3].split(",")])
        saved = (np.sort(np.append(SAVED_TIMES, 176.3))
                 if batch and self.fault == "saved_solution_gap" else SAVED_TIMES)
        indices = [int(np.argmin(abs(saved - t))) for t in requested]
        actual = saved[indices]
        assert np.allclose(actual, requested, rtol=0, atol=1e-12)
        nx, ny = int(args[4]), int(args[5])
        bounds = [float(v) for v in args[6:10]]
        x, y = np.linspace(bounds[0], bounds[1], nx), np.linspace(bounds[2], bounds[3], ny)
        xx, yy = np.meshgrid(x, y)
        radius = RADIUS * 1.7 if fault == "geometry_mask_drift" else RADIUS
        solid = np.hypot(xx - CENTER[0], yy - CENTER[1]) <= radius + 1e-12
        dataset = "dset_changed" if fault == "dataset_drift" else "dset_actual"
        response = {
            "status": "SUCCESS", "operation": "export_full_field_sample",
            "model_path": str(self.model.resolve()),
            "model_tag": "OtherModel" if fault == "model_tag_drift" else "Model",
            "comsol_version": "6.4",
            "dataset": {"tag": dataset, "type": "Solution", "solution_tag": "sol_actual"},
            "geometry": {
                "component_tag": "comp1", "geometry_tag": "geom1", "length_unit": "m",
                "bounds": bounds,
                "circle": {"feature_tag": "c1", "center": list(CENTER), "radius": radius,
                           "unit": "m", "source": "different readback" if fault == "geometry_source_drift" else "live GeomFeature readback"},
            },
            "saved_times": saved.tolist(), "requested_times": requested.tolist(),
            "actual_times": actual.tolist(),
            "grid": {"nx": nx, "ny": ny, "xmin": bounds[0], "xmax": bounds[1],
                     "ymin": bounds[2], "ymax": bounds[3]},
            "direct_evaluation_node": {
                "tag": "num_actual_check", "type": "EvalPoint", "dataset_tag": "cpt_actual_check",
                "point_dataset_type": "CutPoint2D", "source_dataset_tag": dataset,
                "expressions": ["u", "v", "p", "x", "y", "t"],
                "units": ["m/s", "m/s", "Pa", "m", "m", "s"],
                "solrepresentation": "solnum", "innerinput": "manual",
            },
            "exports": [], "direct_evaluations": [],
        }
        if batch:
            response["batch_mode"] = True
        for ti, t in enumerate(actual):
            values = _analytic(t, xx, yy)
            # Mix raw NaN and extrapolated solid values; both require a solid mask.
            for si, (iy, ix) in enumerate(np.argwhere(solid)):
                if si % 2 == 0:
                    values[:, iy, ix] = np.nan
            if batch and self.fault == "overlap_value_mismatch" and abs(t - 180) < 1e-12:
                values[0, 1, 1] += .05  # A fluid point outside the direct-check subset.
            filename = f"field_t{ti}.txt"
            _write_grid(directory / filename, x, y, t, values)
            response["exports"].append({
                "filename": filename, "export_tag": f"exp_actual_{ti}", "export_type": "Data",
                "dataset_tag": dataset, "expressions": ["u", "v", "p"],
                "units": ["m/s", "m/s", "Pa"], "solnum": indices[ti] + 1,
                "target_t": float(requested[ti]), "actual_t": float(t), "innerinput": "manual",
                "gridx": x.tolist(), "gridy": y.tolist(),
            })
            for ix, iy in ((0, 0), (nx - 1, ny - 1), (nx - 1, ny // 2)):
                px, py = float(x[ix]), float(y[iy])
                response["direct_evaluations"].append({
                    "time_index": ti, "grid_ix": ix, "grid_iy": iy,
                    "target_t": float(requested[ti]), "actual_t": float(t),
                    "requested_x": px, "requested_y": py, "x": px, "y": py,
                    "values": _analytic(t, px, py).tolist(),
                    "numerical_tag": "num_actual_check", "point_dataset_tag": "cpt_actual_check",
                })
        if fault == "missing_frame":
            response["exports"].pop(0)
        elif fault == "duplicate_boundary_time":
            response["actual_times"][0] = self.responses[-1]["actual_times"][-1]
        elif fault == "coordinate_drift":
            response["exports"][0]["gridx"][1] += .001
        elif fault == "wrong_solnum":
            response["exports"][0]["solnum"] -= 1
        elif fault == "missing_batch_marker":
            response.pop("batch_mode")
        elif fault == "source_mutated":
            self.model.write_bytes(b"source model unexpectedly changed")
        if batch:
            self.directories.append(directory)
            if fault == "external_failure":
                raise bridge.ComsolConnectionError("Injected second-batch read failure", details="original COMSOL diagnostics")
            self.responses.append(deepcopy(response))
        return response


@pytest.fixture
def sequence_case(tmp_path, monkeypatch):
    model = tmp_path / "existing_m3.mph"
    model.write_bytes(b"immutable solved model fixture")
    fake = AnalyticComsol(model)
    monkeypatch.setattr(bridge, "_run_bridge", fake)
    reference_root = tmp_path / "reference"
    bridge.export_full_field_sample(model, reference_root, requested_times=REFERENCE_TIMES,
                                   grid_shapes=((9, 5), SHAPE), bounds=BOUNDS)
    return model, tmp_path / "sequence", fake, reference_root / "grid_17x9"


def _export(case, **kwargs):
    from hydro_disturb_bench.field_sequence import export_full_field_sequence

    model, output, fake, reference = case
    arguments = {"grid_shape": SHAPE, "overlap_reference": reference, **kwargs}
    return export_full_field_sequence(model, output, **arguments)


def _manifest(output):
    return json.loads((output / "sequence_manifest.json").read_text(encoding="utf-8"))


def _write_manifest(output, manifest):
    (output / "sequence_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_sequence_roundtrips_151_saved_frames_across_uneven_batches(sequence_case):
    from hydro_disturb_bench.field_sequence import load_full_field_sequence

    model, output, fake, reference = sequence_case
    original_model = model.read_bytes()
    result = _export(sequence_case)
    assert result["status"] == "PASS"
    assert model.read_bytes() == original_model
    assert [len(call[3].split(",")) for call in fake.calls] == [32, 32, 32, 32, 23]
    loaded = load_full_field_sequence(output)
    assert loaded["values"].shape == (151, 3, 9, 17)
    np.testing.assert_array_equal(loaded["times"], SAVED_TIMES[1:])
    np.testing.assert_array_equal(loaded["x"], np.linspace(0, .4, 17))
    np.testing.assert_array_equal(loaded["y"], np.linspace(0, .24, 9))
    assert loaded["mask"].dtype == loaded["invalid_mask"].dtype == loaded["cylinder_mask"].dtype == np.bool_
    xx, yy = np.meshgrid(loaded["x"], loaded["y"])
    expected_solid = np.hypot(xx - CENTER[0], yy - CENTER[1]) <= RADIUS + 1e-12
    np.testing.assert_array_equal(loaded["cylinder_mask"], expected_solid)
    for ti, t in enumerate(loaded["times"]):
        expected = _analytic(t, xx, yy)
        np.testing.assert_allclose(loaded["values"][ti][:, ~expected_solid], expected[:, ~expected_solid], rtol=0, atol=1e-12)
        assert np.isnan(loaded["values"][ti][:, expected_solid]).all()
    manifest = loaded["metadata"]
    assert manifest == _manifest(output)
    assert manifest["source_sha256"].lower() == hashlib.sha256(original_model).hexdigest()
    assert manifest["solver_called"] is False and manifest["source_unchanged"] is True
    assert manifest["tensor_layout"] == "(T, 3, Ny, Nx)"
    assert manifest["shape"] == [151, 3, 9, 17]
    assert manifest["channel_order"] == ["u", "v", "p"]
    assert manifest["units"] == ["m/s", "m/s", "Pa"]
    np.testing.assert_array_equal(manifest["solution_numbers"], np.arange(2, 153))
    assert manifest["overlap_check"]["frames_checked"] == 5
    np.testing.assert_array_equal(manifest["overlap_check"]["times"], REFERENCE_TIMES)
    np.testing.assert_array_equal(manifest["overlap_check"]["max_absolute_errors"], [0, 0, 0])
    assert (output / "sequence_report.json").is_file()
    assert [(b["start_index"], b["stop_index"]) for b in manifest["batches"]] == [(0, 32), (32, 64), (64, 96), (96, 128), (128, 151)]


def test_single_frame_batches_retain_consecutive_saved_times(sequence_case):
    from hydro_disturb_bench.field_sequence import load_full_field_sequence

    result = _export(sequence_case, start=170, stop=170.8, step=.2, batch_size=1,
                     overlap_reference=None)
    assert result["status"] == "PASS"
    assert [len(call[3].split(",")) for call in sequence_case[2].calls] == [1] * 5
    loaded = load_full_field_sequence(sequence_case[1])
    np.testing.assert_array_equal(loaded["times"], SAVED_TIMES[1:6])
    assert loaded["values"].shape == (5, 3, 9, 17)
    assert loaded["metadata"]["overlap_check"] == {"status": "NOT_CHECKED", "frames_checked": 0}


@pytest.mark.parametrize("fault", [
    "missing_frame", "duplicate_boundary_time", "wrong_solnum", "coordinate_drift",
    "model_tag_drift", "dataset_drift", "geometry_mask_drift", "geometry_source_drift",
    "missing_batch_marker", "source_mutated", "overlap_value_mismatch", "saved_solution_gap",
])
def test_sequence_rejects_invalid_batch_or_cross_batch_provenance(sequence_case, fault):
    model, output, fake, reference = sequence_case
    fake.fault = fault
    with pytest.raises(bridge.ModelValidationError):
        _export(sequence_case)
    assert not output.exists(), "A failed sequence must not appear as a published result"
    staging = list(output.parent.glob(".staging_field_sequence_*"))
    assert len(staging) == 1
    failure = json.loads((staging[0] / "failure.json").read_text(encoding="utf-8"))
    assert failure["status"] == "FAIL"
    assert failure["source_unchanged"] is (fault != "source_mutated")
    for directory, response in zip(fake.directories, fake.responses):
        assert json.loads((directory / "bridge_response.json").read_text(encoding="utf-8")) == response
        assert list(directory.glob("*.txt")), "Raw return evidence must survive failure"


def test_second_batch_external_failure_retains_completed_batch_and_diagnostics(sequence_case):
    model, output, fake, reference = sequence_case
    fake.fault = "external_failure"
    with pytest.raises(bridge.ComsolConnectionError, match="second-batch"):
        _export(sequence_case)
    assert not output.exists()
    failure_path, = output.parent.glob(".staging_field_sequence_*/failure.json")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["details"] == "original COMSOL diagnostics"
    assert (fake.directories[0] / "field_sample.npz").is_file()
    assert (fake.directories[0] / "bridge_response.json").is_file()
    assert list(fake.directories[1].glob("*.txt"))


@pytest.mark.parametrize("changes", [
    {"batch_size": 0}, {"batch_size": 33}, {"batch_size": True},
    {"step": 0}, {"step": -0.2}, {"step": float("nan")},
    {"start": 201, "stop": 200}, {"start": 170, "stop": 200, "step": .7},
])
def test_invalid_sequence_schedule_rejected_before_external_reads(sequence_case, changes):
    with pytest.raises(bridge.InvalidParameterError):
        _export(sequence_case, **changes)
    assert sequence_case[2].calls == []
    assert not sequence_case[1].exists()


def test_sequence_never_overwrites_existing_output(sequence_case):
    output = sequence_case[1]
    output.mkdir()
    sentinel = output / "historical.dat"
    sentinel.write_bytes(b"must stay byte-identical")
    with pytest.raises(FileExistsError):
        _export(sequence_case)
    assert sentinel.read_bytes() == b"must stay byte-identical"
    assert sequence_case[2].calls == []


@pytest.mark.parametrize("fault", [
    "coverage_gap", "coverage_duplicate", "batch_hash", "metadata_hash", "response_hash",
    "path_escape", "requested_time_gap", "actual_time_gap",
])
def test_sequence_loader_rejects_manifest_coverage_or_evidence_tampering(sequence_case, fault):
    from hydro_disturb_bench.field_sequence import load_full_field_sequence

    _export(sequence_case, start=170, stop=170.8, step=.2, batch_size=2,
            overlap_reference=None)
    output = sequence_case[1]
    manifest = _manifest(output)
    second = manifest["batches"][1]
    if fault == "coverage_gap":
        second["start_index"] += 1
    elif fault == "coverage_duplicate":
        second["start_index"] -= 1
    elif fault == "path_escape":
        second["path"] = "../reference/grid_17x9"
    elif fault == "requested_time_gap":
        manifest["requested_times"][2] += .05
    elif fault == "actual_time_gap":
        manifest["actual_times"][2] += .05
    else:
        filename = {"batch_hash": "field_sample.npz", "metadata_hash": "metadata.json", "response_hash": "bridge_response.json"}[fault]
        path = output / second["path"] / filename
        path.write_bytes(path.read_bytes() + b" ")
    _write_manifest(output, manifest)
    with pytest.raises(bridge.ModelValidationError):
        load_full_field_sequence(output)
