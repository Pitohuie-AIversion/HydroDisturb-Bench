"""Offline admission tests for the saved-solution full-field sampling entry.

Only the external COMSOL call is replaced. The production parser, provenance
checks, NPZ writer and reader all process deliberately nonuniform analytic data.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.comsol_java import bridge

TIMES = (180.0, 185.0, 190.0)
SHAPES = ((9, 5), (17, 9))  # (Nx, Ny), deliberately nonsquare
BOUNDS = (0.0, 0.4, 0.0, 0.24)
CENTER = (0.2, 0.12)  # Differs from M3: catches a hard-coded cylinder mask.
RADIUS = 0.035


def _analytic(t, x, y):
    return np.asarray([
        1.0 + 0.01 * t + 2.0 * x + 3.0 * y,
        -1.0 + 0.02 * t - 5.0 * x + 7.0 * y,
        100.0 + 0.03 * t + 11.0 * x - 13.0 * y,
    ])


def _solid(x, y):
    xx, yy = np.meshgrid(x, y)
    return (xx - CENTER[0]) ** 2 + (yy - CENTER[1]) ** 2 <= RADIUS**2


def _write_grid(path, x, y, t, values, *, columns=("u", "v", "p"), units=None, fault=None):
    units = units or {"u": "m/s", "v": "m/s", "p": "Pa"}
    header = "% x y " + " ".join(f"{c} ({units[c]}) @ t={t:.17g}" for c in columns)
    lines = ["% Dimension: 2", "% Length unit: m", f"% Nodes: {len(x)*len(y)}", "% Expressions: 3", header]
    rows = []
    for iy, yy in enumerate(y):
        for ix, xx in enumerate(x):
            fields = values[:, iy, ix]
            vals = [xx, yy, *(fields[("u", "v", "p").index(c)] for c in columns)]
            rows.append(" ".join(f"{v:.17g}" for v in vals))
    if fault == "duplicate_coordinate":
        rows[1] = rows[0]
    elif fault == "extra_column":
        rows[0] += " 999"
    elif fault == "malformed_row":
        rows[0] = "0 0 invalid 1 2"
    path.write_text("\n".join(lines + rows) + "\n", encoding="utf-8")


class FakeComsol:
    def __init__(self, model, fault=None):
        self.model = model
        self.fault = fault
        self.calls = []
        self.responses = []
        self.directories = []

    def __call__(self, args, root=None):
        assert args[0] == "export-full-field-sample", "Sampling may only call the read/export operation"
        assert Path(args[1]).resolve() == self.model.resolve()
        directory = Path(args[2])
        directory.mkdir(parents=True, exist_ok=True)
        requested = [float(v) for v in args[3].split(",")]
        actual = (np.nextafter(requested, np.inf).tolist()
                  if self.fault == "time_roundoff" else requested.copy())
        nx, ny = int(args[4]), int(args[5])
        bounds = [float(v) for v in args[6:10]]
        x = np.linspace(bounds[0], bounds[1], nx)
        y = np.linspace(bounds[2], bounds[3], ny)
        exported_x = x.copy()
        if self.fault == "wrong_bounds":
            exported_x = np.linspace(bounds[0] + .01, bounds[1] - .01, nx)
        mask = _solid(exported_x, y)
        response = {
            "status": "SUCCESS", "operation": "export_full_field_sample",
            "model_path": str(self.model.resolve()), "model_tag": "Model", "comsol_version": "6.4",
            "dataset": {"tag": "dset_actual", "type": "Solution", "solution_tag": "sol_actual"},
            "geometry": {
                "component_tag": "comp1", "geometry_tag": "geom1", "length_unit": "m",
                "bounds": bounds,
                "circle": {"feature_tag": "c1", "center": list(CENTER), "radius": RADIUS,
                           "unit": "m", "source": "live GeomFeature position/radius readback"},
            },
            "saved_times": [0.0, *actual, 200.0],
            "requested_times": requested,
            "actual_times": actual.copy(),
            "grid": {"nx": nx, "ny": ny, "xmin": bounds[0], "xmax": bounds[1],
                     "ymin": bounds[2], "ymax": bounds[3]},
            "direct_evaluation_node": {
                "tag": "num_actual_check", "type": "EvalPoint",
                "dataset_tag": "cpt_actual_check", "point_dataset_type": "CutPoint2D",
                "source_dataset_tag": "dset_actual", "expressions": ["u", "v", "p", "x", "y", "t"],
                "units": ["m/s", "m/s", "Pa", "m", "m", "s"],
                "solrepresentation": "solnum", "innerinput": "manual",
            },
            "exports": [], "direct_evaluations": [],
        }
        self.calls.append(list(args))
        self.directories.append(directory)
        for ti, t in enumerate(requested):
            actual_t = actual[ti]
            xx, yy = np.meshgrid(exported_x, y)
            values = _analytic(actual_t, xx, yy)
            # Some solid samples are NaN and some have extrapolated finite values.
            # Both must end up explicitly masked; fluid values must remain untouched.
            solid_indices = np.argwhere(mask)
            for si, (iy, ix) in enumerate(solid_indices):
                if si % 2 == 0:
                    values[:, iy, ix] = np.nan
            if self.fault == "fluid_nan":
                values[0, 0, 0] = np.nan
            elif self.fault == "fluid_inf":
                values[2, 0, 0] = np.inf
            filename = f"field_t{ti}.txt"
            unit_map = {"u": "m/s", "v": "m/s", "p": "kPa" if self.fault in ("wrong_pressure_unit", "wrong_pressure_header_unit") else "Pa"}
            header_t = t + .2 if self.fault == "wrong_header_time" else actual_t
            columns = ("p", "u", "v") if self.fault == "shuffled_columns" else ("u", "v", "p")
            _write_grid(directory / filename, exported_x, y, header_t, values, columns=columns,
                        units=unit_map, fault=self.fault)
            response["exports"].append({
                "filename": filename, "export_tag": f"exp_actual_{ti}", "export_type": "Data",
                "dataset_tag": "dset_actual", "expressions": ["u", "v", "p"],
                "units": ["m/s", "m/s", "Pa" if self.fault == "wrong_pressure_header_unit" else unit_map["p"]], "solnum": ti + 2,
                "target_t": t, "actual_t": actual_t, "innerinput": "manual",
                "gridx": x.tolist(), "gridy": y.tolist(),
            })
            for ix, iy in ((0, 0), (nx - 1, ny - 1), (nx - 1, ny // 2)):
                px, py = float(x[ix]), float(y[iy])
                response["direct_evaluations"].append({
                    "time_index": ti, "grid_ix": ix, "grid_iy": iy,
                    "target_t": t, "actual_t": actual_t,
                    "requested_x": px, "requested_y": py, "x": px, "y": py,
                    "values": _analytic(actual_t, px, py).tolist(),
                    "numerical_tag": "num_actual_check", "point_dataset_tag": "cpt_actual_check",
                })
        if self.fault == "actual_time_missing":
            response.pop("actual_times")
        elif self.fault == "actual_time_nearest":
            response["actual_times"][1] += .2
        elif self.fault == "missing_export":
            response["exports"].pop()
        elif self.fault == "missing_file":
            (directory / response["exports"][0]["filename"]).unlink()
        elif self.fault == "duplicate_export":
            response["exports"][1] = deepcopy(response["exports"][0])
        elif self.fault == "path_escape":
            response["exports"][0]["filename"] = "../field_t0.txt"
        elif self.fault == "wrong_dataset":
            response["exports"][0]["dataset_tag"] = "unrelated_dataset"
        elif self.fault == "missing_unit_evidence":
            response["exports"][0].pop("units")
        elif self.fault == "missing_geometry":
            response.pop("geometry")
        elif self.fault == "wrong_model":
            response["model_path"] = str(self.model.with_name("other.mph"))
        elif self.fault == "wrong_solnum":
            response["exports"][1]["solnum"] = 1
        elif self.fault == "direct_wrong_coordinate":
            response["direct_evaluations"][0]["x"] += .001
        elif self.fault == "direct_wrong_time":
            response["direct_evaluations"][0]["actual_t"] += .2
        elif self.fault == "direct_wrong_channel":
            response["direct_evaluations"][0]["values"] = list(reversed(response["direct_evaluations"][0]["values"]))
        elif self.fault == "direct_missing_time":
            response["direct_evaluations"][0].pop("actual_t")
        elif self.fault == "direct_insufficient":
            response["direct_evaluations"] = [p for p in response["direct_evaluations"] if p["grid_ix"] == 0]
        elif self.fault == "missing_direct_node":
            response.pop("direct_evaluation_node")
        elif self.fault == "missing_direct_node_units":
            response["direct_evaluation_node"].pop("units")
        elif self.fault == "wrong_direct_node_units":
            response["direct_evaluation_node"]["units"][2] = "kPa"
        elif self.fault == "wrong_direct_source_dataset":
            response["direct_evaluation_node"]["source_dataset_tag"] = "unrelated_dataset"
        elif self.fault == "wrong_direct_numerical_tag":
            response["direct_evaluations"][0]["numerical_tag"] = "unrelated_numerical"
        elif self.fault == "wrong_direct_point_dataset":
            response["direct_evaluations"][0]["point_dataset_tag"] = "unrelated_point_dataset"
        elif self.fault == "missing_direct_row_tag":
            response["direct_evaluations"][0].pop("numerical_tag")
        elif self.fault == "source_mutated":
            self.model.write_bytes(b"changed model during read-only export")
        elif self.fault == "external_failure":
            raise bridge.ComsolConnectionError("Injected COMSOL export read failure", details="original COMSOL traceback")
        self.responses.append(deepcopy(response))
        return response


def _setup(tmp_path, monkeypatch, fault=None):
    model = tmp_path / "existing_m3.mph"
    model.write_bytes(b"immutable solved M3 fixture")
    fake = FakeComsol(model, fault)
    monkeypatch.setattr(bridge, "_run_bridge", fake)
    return model, tmp_path / "sample", fake


def _run(model, output, **kwargs):
    return bridge.export_full_field_sample(model, output, requested_times=TIMES,
                                           grid_shapes=SHAPES, bounds=BOUNDS, **kwargs)


def test_saved_solution_sampling_roundtrips_nonsquare_tensor_axes_channels_and_mask(tmp_path, monkeypatch):
    from hydro_disturb_bench.field_sampling import load_full_field_sample

    model, output, fake = _setup(tmp_path, monkeypatch)
    before = model.read_bytes()
    result = _run(model, output)
    assert result["status"] == "PASS"
    assert len(fake.calls) == 2
    assert model.read_bytes() == before
    assert result["source_sha256"].lower() == hashlib.sha256(before).hexdigest()
    for nx, ny in SHAPES:
        grid_dir = output / f"grid_{nx}x{ny}"
        loaded = load_full_field_sample(grid_dir)
        x, y, values = loaded["x"], loaded["y"], loaded["values"]
        assert values.shape == (len(TIMES), 3, ny, nx)
        np.testing.assert_array_equal(x, np.linspace(BOUNDS[0], BOUNDS[1], nx))
        np.testing.assert_array_equal(y, np.linspace(BOUNDS[2], BOUNDS[3], ny))
        np.testing.assert_array_equal(loaded["times"], TIMES)
        assert loaded["mask"].dtype == np.bool_
        assert loaded["cylinder_mask"].dtype == np.bool_
        assert loaded["invalid_mask"].dtype == np.bool_
        assert loaded["mask"].shape == (len(TIMES), ny, nx)
        assert loaded["invalid_mask"].shape == (len(TIMES), ny, nx)
        np.testing.assert_array_equal(loaded["cylinder_mask"], _solid(x, y))
        assert loaded["cylinder_mask"].any()
        expanded = np.broadcast_to(loaded["mask"][:, None], values.shape)
        assert np.isnan(values[expanded]).all()
        assert np.isfinite(values[~expanded]).all()
        xx, yy = np.meshgrid(x, y)
        for ti, t in enumerate(TIMES):
            expected = _analytic(t, xx, yy)
            np.testing.assert_allclose(values[ti][:, ~loaded["mask"][ti]], expected[:, ~loaded["mask"][ti]], rtol=0, atol=1e-12)
        metadata = loaded["metadata"]
        assert metadata["tensor_layout"] == "(T, 3, Ny, Nx)"
        assert metadata["channel_order"] == ["u", "v", "p"]
        assert metadata["units"] == ["m/s", "m/s", "Pa"]
        assert metadata["source_sha256"].lower() == hashlib.sha256(before).hexdigest()
    assert (output / "sample_report.json").is_file()


@pytest.mark.parametrize("fault", [
    "fluid_nan", "fluid_inf", "wrong_pressure_unit", "wrong_pressure_header_unit", "wrong_header_time",
    "duplicate_coordinate", "extra_column", "malformed_row", "wrong_bounds",
    "actual_time_missing", "actual_time_nearest", "missing_export", "missing_file",
    "duplicate_export", "path_escape", "wrong_dataset", "missing_unit_evidence",
    "missing_geometry", "wrong_model", "wrong_solnum",
    "direct_wrong_coordinate", "direct_wrong_time", "direct_wrong_channel",
    "direct_missing_time", "direct_insufficient",
    "missing_direct_node", "missing_direct_node_units", "wrong_direct_node_units",
    "wrong_direct_source_dataset", "wrong_direct_numerical_tag", "wrong_direct_point_dataset",
    "missing_direct_row_tag",
])
def test_sampling_entry_rejects_corrupt_data_or_unbound_readbacks_without_publication(tmp_path, monkeypatch, fault):
    model, output, fake = _setup(tmp_path, monkeypatch, fault)
    original = model.read_bytes()
    with pytest.raises((bridge.ModelValidationError, ValueError)):
        _run(model, output)
    assert not output.exists()
    assert model.read_bytes() == original
    assert fake.calls
    # Failure evidence is retained; rejection must not silently delete raw TXT/JSON.
    retained_txt = list(tmp_path.rglob("field_t*.txt"))
    assert retained_txt
    retained_json = [json.loads(p.read_text(encoding="utf-8")) for p in tmp_path.rglob("*.json")]
    assert any(isinstance(item, dict) and all(item.get(k) == v for k, v in fake.responses[0].items())
               for item in retained_json), "The original external response must survive rejection"


def test_sampling_entry_retains_external_read_failure_and_original_raw_export(tmp_path, monkeypatch):
    model, output, fake = _setup(tmp_path, monkeypatch, "external_failure")
    with pytest.raises(bridge.ComsolConnectionError, match="Injected"):
        _run(model, output)
    assert not output.exists()
    assert list(tmp_path.rglob("field_t*.txt"))
    diagnostics = list(tmp_path.rglob("*.json")) + list(tmp_path.rglob("*.log"))
    assert any("original COMSOL traceback" in p.read_text(encoding="utf-8") for p in diagnostics)


def test_sampling_entry_rejects_source_mutation_before_publication(tmp_path, monkeypatch):
    model, output, fake = _setup(tmp_path, monkeypatch, "source_mutated")
    with pytest.raises(bridge.ModelValidationError, match="[Ss]ource|model.*changed"):
        _run(model, output)
    assert not output.exists()
    assert list(tmp_path.rglob("field_t*.txt"))


@pytest.mark.parametrize("allow_overwrite", [False, True])
def test_sampling_never_overwrites_existing_output_directory(tmp_path, monkeypatch, allow_overwrite):
    model, output, fake = _setup(tmp_path, monkeypatch)
    output.mkdir()
    historical = output / "historical.bin"
    historical.write_bytes(b"original\r\n\x89PNG\x00")
    original = historical.read_bytes()
    with pytest.raises((FileExistsError, bridge.InvalidParameterError)):
        _run(model, output, allow_overwrite=allow_overwrite)
    assert historical.read_bytes() == original
    assert fake.calls == []


@pytest.mark.parametrize("argument,value", [
    ("requested_times", (180.0, 185.0)),
    ("requested_times", (180.0, 181.0, 182.0, 183.0, 184.0, 185.0)),
    ("requested_times", (180.0, 180.0, 190.0)),
    ("requested_times", (190.0, 185.0, 180.0)),
    ("requested_times", (180.0, float("nan"), 190.0)),
    ("grid_shapes", ((9, 5),)),
    ("grid_shapes", ((9, 5), (9, 5))),
    ("grid_shapes", ((9.5, 5), (17, 9))),
    ("grid_shapes", ((True, 5), (17, 9))),
    ("grid_shapes", ((1, 5), (17, 9))),
    ("bounds", (0.4, 0.0, 0.0, .24)),
    ("bounds", (0.0, float("inf"), 0.0, .24)),
])
def test_sampling_rejects_invalid_request_before_external_access(tmp_path, monkeypatch, argument, value):
    model, output, fake = _setup(tmp_path, monkeypatch)
    arguments = {"requested_times": TIMES, "grid_shapes": SHAPES, "bounds": BOUNDS}
    arguments[argument] = value
    with pytest.raises((bridge.InvalidParameterError, ValueError)):
        bridge.export_full_field_sample(model, output, **arguments)
    assert fake.calls == []
    assert not output.exists()


@pytest.mark.parametrize("fault", ["wrong_tensor_shape", "mask_as_int", "finite_inside_solid", "fluid_nan", "wrong_channel_order", "time_mismatch", "requested_time_metadata_mismatch", "shape_metadata_mismatch"])
def test_npz_reader_rejects_corrupt_saved_arrays_and_metadata(tmp_path, monkeypatch, fault):
    from hydro_disturb_bench.field_sampling import load_full_field_sample

    model, output, fake = _setup(tmp_path, monkeypatch)
    _run(model, output)
    grid = output / "grid_9x5"
    npz_path = grid / "field_sample.npz"
    with np.load(npz_path, allow_pickle=False) as saved:
        arrays = {name: saved[name].copy() for name in saved.files}
    if fault == "wrong_tensor_shape":
        arrays["values"] = arrays["values"].swapaxes(-1, -2)
    elif fault == "mask_as_int":
        arrays["mask"] = arrays["mask"].astype(np.uint8)
    elif fault == "finite_inside_solid":
        arrays["values"][:, :, arrays["cylinder_mask"]] = 0.0
    elif fault == "fluid_nan":
        arrays["values"][0, 0, 0, 0] = np.nan
    elif fault == "time_mismatch":
        arrays["times"][0] += .2
    elif fault in ("wrong_channel_order", "requested_time_metadata_mismatch", "shape_metadata_mismatch"):
        meta_path = grid / "metadata.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if fault == "wrong_channel_order":
            meta["channel_order"] = ["p", "u", "v"]
        elif fault == "requested_time_metadata_mismatch":
            meta["requested_times"][0] += .2
        else:
            meta["shape"][-2:] = list(reversed(meta["shape"][-2:]))
        meta_path.write_text(json.dumps(meta), encoding="utf-8")
    np.savez_compressed(npz_path, **arrays)
    # These cases exercise the array/metadata contract beyond the checksum gate.
    meta_path = grid / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["npz_sha256"] = hashlib.sha256(npz_path.read_bytes()).hexdigest()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises((bridge.ModelValidationError, ValueError)):
        load_full_field_sample(grid)


def test_npz_reader_rejects_changed_archive_bytes_before_loading(tmp_path, monkeypatch):
    from hydro_disturb_bench.field_sampling import load_full_field_sample

    model, output, fake = _setup(tmp_path, monkeypatch)
    _run(model, output)
    grid = output / "grid_9x5"
    path = grid / "field_sample.npz"
    path.write_bytes(path.read_bytes() + b"unexpected trailing bytes")
    with pytest.raises(bridge.ModelValidationError, match="SHA-256"):
        load_full_field_sample(grid)


def test_saved_time_roundoff_is_tolerated_without_replacing_actual_with_requested(tmp_path, monkeypatch):
    from hydro_disturb_bench.field_sampling import load_full_field_sample

    model, output, fake = _setup(tmp_path, monkeypatch, "time_roundoff")
    expected_actual = np.nextafter(np.asarray(TIMES), np.inf)
    assert np.all(expected_actual != TIMES)
    assert np.max(np.abs(expected_actual - TIMES)) < 1e-12
    _run(model, output)
    for nx, ny in SHAPES:
        loaded = load_full_field_sample(output / f"grid_{nx}x{ny}")
        np.testing.assert_array_equal(loaded["times"], expected_actual)
        np.testing.assert_array_equal(loaded["metadata"]["actual_times"], expected_actual)
        np.testing.assert_array_equal(loaded["metadata"]["requested_times"], TIMES)
        assert loaded["metadata"]["actual_times"] != loaded["metadata"]["requested_times"]
