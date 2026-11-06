"""Independent auditor checks against exact analytic sums and altered artifacts."""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from sequence_fixtures import write_saved_sequence

from hydro_disturb_bench.data_windows import TrajectoryRecord, WindowSpec, build_window_index

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_forecast_baselines", ROOT / "scripts/audit_forecast_baselines.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def rehash(index):
    index.pop("payload_sha256", None)
    index["payload_sha256"] = hashlib.sha256(audit.canonical(index)).hexdigest()


@pytest.fixture
def audit_case(tmp_path):
    model = tmp_path / "synthetic.mph"
    model.write_bytes(b"analytic identity only; never read as a model")
    directory, manifest, _ = write_saved_sequence(tmp_path / "sequence", model, start=0.,
        step=.25, frames=8, grid_shape=(5, 5), batch_size=4)
    # Binary-exact time/space fields permit a hand-calculated zero linear error.
    slope = np.array([1., -2., 4.])[:, None, None]
    spatial = np.arange(75).reshape(3, 5, 5) / 4
    for batch in manifest["batches"]:
        folder = directory / batch["path"]
        with np.load(folder / "field_sample.npz") as original:
            arrays = {key: original[key] for key in original.files}
        arrays["values"] = spatial[None] + arrays["times"][:, None, None, None] * slope[None]
        arrays["values"][np.broadcast_to(arrays["mask"][:, None], arrays["values"].shape)] = np.nan
        np.savez_compressed(folder / "field_sample.npz", **arrays)
        metadata = audit.load_json(folder / "metadata.json")
        metadata["npz_sha256"] = audit.sha(folder / "field_sample.npz")
        write(folder / "metadata.json", metadata)
        for filename, key in audit.BATCH_FILES:
            batch[key] = audit.sha(folder / filename)
    write(directory / "sequence_manifest.json", manifest)
    record = TrajectoryRecord("analytic", "family", directory, audit.sha(directory / "sequence_manifest.json"), "a" * 64)
    index = build_window_index([record], {"family": "train"}, WindowSpec(3, 2, 2))
    index_path, report_path = tmp_path / "index.json", tmp_path / "report.json"
    write(index_path, index)
    count = [[48] * 3] * 2  # 24 fluid nodes, two forecast windows, each lead/channel.
    mae = np.array([[.25, .5, 1.], [.5, 1., 2.]])
    persistence = {"mae": mae.tolist(), "rmse": mae.tolist(), "count": count,
                   "sae": (mae * 48).tolist(), "sse": (mae * mae * 48).tolist()}
    linear = {"count": count, **{name: np.zeros((2, 3)).tolist() for name in ("sae", "sse", "mae", "rmse")}}
    numbers = {"window_count": 2, "forecast_frame_assignments": 4, "unique_target_frames": 4,
               "repeated_future_assignments": 0, "target_frames_seen_more_than_once": 0}
    identity = [{"path": str(directory / "sequence_manifest.json"), "sha256": record.manifest_sha256,
                 "kind": "manifest", "trajectory_id": "analytic"}]
    for batch in manifest["batches"]:
        identity.extend({"path": str(directory / batch["path"] / name), "sha256": batch[key],
                         "kind": name, "trajectory_id": "analytic"} for name, key in audit.BATCH_FILES)
    with np.load(directory / "batch_0000/field_sample.npz") as archive:
        mask_sha = hashlib.sha256(archive["mask"][0].tobytes()).hexdigest()
    methods = {"persistence": persistence, "last_two_linear": linear}
    report = {"schema_version": 1, "status": "PASS", "mode": "pipeline_smoke", "split": "train",
              "heldout_evaluation": False, "training_performed": False,
              "channels": ["u", "v", "p"], "units": ["m/s", "m/s", "Pa"], "lead_seconds": [.25, .5],
              "metric_units": {"mae": ["m/s", "m/s", "Pa"], "rmse": ["m/s", "m/s", "Pa"],
                               "sae": ["m/s", "m/s", "Pa"], "sse": ["(m/s)^2", "(m/s)^2", "Pa^2"],
                               "count": ["1", "1", "1"]},
              "method_definitions": {name: "fixed untrained formula" for name in audit.METHODS},
              "overall": {**numbers, "methods": deepcopy(methods)},
              "per_trajectory": [{"trajectory_id": "analytic", "family_id": "family", **numbers,
                                  "static_mask_sha256": mask_sha, "methods": deepcopy(methods)}],
              "identity_audit": {"index_payload_sha256": index["payload_sha256"], "inputs_unchanged": True,
                                 "files_checked": len(identity), "files": identity}}
    write(report_path, report)
    return index_path, report_path, directory, report


def run(case, output):
    return audit.audit_forecast(case[0], audit.sha(case[0]), case[1], output)


def test_exact_hand_calculated_metrics_and_no_input_changes(audit_case, tmp_path):
    before = {p: audit.sha(p) for p in tmp_path.rglob("*") if p.is_file()}
    result = run(audit_case, tmp_path / "audit.json")
    assert result["status"] == "PASS"
    assert result["overall"]["methods"] == audit_case[3]["overall"]["methods"]
    assert all(row["max_absolute_residual"] == 0 for row in result["residuals"])
    assert result["project_readers_or_forecasters_called"] is False
    assert result["solver_called"] is False and result["new_tensor_files"] == 0
    assert all(audit.sha(path) == value for path, value in before.items())
    assert result["tolerances"] == {"absolute": 1e-14, "relative": 1e-11, "lead_absolute_s": 1e-12, "count": "exact"}


def test_auditor_has_no_project_imports_and_cli_requires_pinned_index():
    tree = ast.parse((ROOT / "scripts/audit_forecast_baselines.py").read_text(encoding="utf-8"))
    imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    imports += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    assert all(not name.startswith(("hydro_disturb_bench", "forecast_", "data_windows")) for name in imports)
    with pytest.raises(SystemExit):
        audit.main(["--index", "index.json", "--report", "report.json", "--output", "audit.json"])


@pytest.mark.parametrize("fault", ["sae", "sse", "count", "mae", "rmse", "per_trajectory", "leads",
                                   "index_binding", "identity", "window_count", "heldout", "units", "metric_units"])
def test_changed_reports_reject_without_publishing(audit_case, tmp_path, fault):
    report = deepcopy(audit_case[3])
    if fault in audit.METRICS:
        report["overall"]["methods"]["persistence"][fault][0][0] += 1
    elif fault == "per_trajectory":
        report["per_trajectory"][0]["methods"]["last_two_linear"]["sse"][1][2] = 1.
    elif fault == "leads":
        report["lead_seconds"][1] += .1
    elif fault == "index_binding":
        report["identity_audit"]["index_payload_sha256"] = "0" * 64
    elif fault == "identity":
        report["identity_audit"]["files"][1]["sha256"] = "0" * 64
    elif fault == "window_count":
        report["overall"]["window_count"] += 1
    elif fault == "heldout":
        report["heldout_evaluation"] = True
    elif fault == "metric_units":
        report["metric_units"]["sse"][2] = "Pa"
    else:
        report["units"][2] = "normalized"
    write(audit_case[1], report)
    output = tmp_path / "failed.json"
    with pytest.raises(ValueError):
        run(audit_case, output)
    assert not output.exists()


def test_bad_index_pin_and_changed_npz_are_rejected(audit_case, tmp_path):
    with pytest.raises(ValueError, match="Index file SHA"):
        audit.audit_forecast(audit_case[0], "0" * 64, audit_case[1], tmp_path / "bad.json")
    path = audit_case[2] / "batch_0001/field_sample.npz"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="Input hash mismatch"):
        run(audit_case, tmp_path / "bad.json")


@pytest.mark.parametrize("fault", ["fluid_nan", "masked_finite", "mask_drift", "time_drift"])
def test_rehashed_invalid_npz_contract_still_rejects(audit_case, tmp_path, fault):
    directory = audit_case[2]
    manifest = audit.load_json(directory / "sequence_manifest.json")
    batch = manifest["batches"][1]
    path = directory / batch["path"] / "field_sample.npz"
    with np.load(path) as archive:
        data = {key: archive[key] for key in archive.files}
    if fault == "fluid_nan":
        data["values"][0, 0, 0, 0] = np.nan
    elif fault == "masked_finite":
        data["values"][0, 0, 2, 2] = 0.
    elif fault == "mask_drift":
        data["mask"][0, 0, 0] = True
        data["invalid_mask"][0, 0, 0] = True
        data["values"][0, :, 0, 0] = np.nan
    else:
        data["times"][0] += .01
    np.savez_compressed(path, **data)
    batch["npz_sha256"] = audit.sha(path)
    write(directory / "sequence_manifest.json", manifest)
    index = audit.load_json(audit_case[0])
    index["trajectories"][0]["manifest_sha256"] = audit.sha(directory / "sequence_manifest.json")
    rehash(index)
    write(audit_case[0], index)
    report = deepcopy(audit_case[3])
    report["identity_audit"]["index_payload_sha256"] = index["payload_sha256"]
    write(audit_case[1], report)
    with pytest.raises(ValueError, match="mask|Masked|times"):
        run(audit_case, tmp_path / "failed.json")


def test_output_cannot_modify_existing_artifacts_or_source_directories(audit_case, tmp_path):
    existing = tmp_path / "existing.json"
    existing.write_bytes(b"preserve")
    with pytest.raises(FileExistsError):
        run(audit_case, existing)
    assert existing.read_bytes() == b"preserve"
    output = audit_case[2] / "batch_audit" / "audit.json"
    with pytest.raises(ValueError, match="inside a source sequence"):
        run(audit_case, output)
    assert not output.parent.exists()


def test_rehashed_index_cannot_change_window_bounds(audit_case, tmp_path):
    index = audit.load_json(audit_case[0])
    index["windows"][0]["history_stop"] += 1
    rehash(index)
    write(audit_case[0], index)
    report = deepcopy(audit_case[3])
    report["identity_audit"]["index_payload_sha256"] = index["payload_sha256"]
    write(audit_case[1], report)
    with pytest.raises(ValueError, match="window boundaries"):
        run(audit_case, tmp_path / "failed.json")


def test_changed_report_during_audit_prevents_publication(audit_case, tmp_path, monkeypatch):
    calculate = audit.independent_sums

    def mutate(*args):
        result = calculate(*args)
        audit_case[1].write_bytes(audit_case[1].read_bytes() + b"\n")
        return result

    monkeypatch.setattr(audit, "independent_sums", mutate)
    with pytest.raises(ValueError, match="Inputs changed"):
        run(audit_case, tmp_path / "failed.json")
    assert not (tmp_path / "failed.json").exists()


def test_independent_audit_agrees_with_production_report(audit_case, tmp_path):
    from hydro_disturb_bench.forecast_evaluation import evaluate_baselines
    report = evaluate_baselines(audit.load_json(audit_case[0]), split="train", mode="pipeline_smoke")
    write(audit_case[1], report)
    result = run(audit_case, tmp_path / "production_audit.json")
    assert result["status"] == "PASS" and result["overall"]["window_count"] == 2
