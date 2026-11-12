"""Evaluate history-only forecasts over pinned windows without saving tensors.

PASS describes execution and identity checks. Pipeline smoke results describe
the selected training families, not held-out accuracy or CFD discretization error.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

import numpy as np

from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .data_windows import read_window, validate_window_index
from .field_sequence import FieldSequenceReader
from .forecast_baselines import ForecastMetricAccumulator, forecast_from_history

METHOD_DEFINITIONS = {
    "persistence": "q_hat(t) = q_last",
    "last_two_linear": "q_hat(t) = q_last + (q_last-q_previous)*(t-t_last)/(t_last-t_previous)",
}
WINDOW_FIELDS = {"history", "future", "history_mask", "future_mask", "x", "y",
                 "history_times", "future_times"}


def _require(condition, message):
    if not condition:
        raise ModelValidationError(message)


def _input(condition, message):
    if not condition:
        raise InvalidParameterError(message)


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _selection(index, split, mode):
    _input(mode in ("pipeline_smoke", "heldout"), "Unknown forecast evaluation mode")
    _input(split in ("train", "validation", "test"), "Unknown evaluation split")
    _input((mode == "pipeline_smoke" and split == "train")
           or (mode == "heldout" and split in ("validation", "test")),
           "pipeline_smoke requires train; heldout requires validation or test")
    checked = validate_window_index(index)
    trajectories = [t for t in checked["trajectories"] if t["split"] == split]
    windows = [w for w in checked["windows"] if w["split"] == split]
    _input(bool(trajectories) and bool(windows), "Selected evaluation split is empty; no fallback")
    if mode == "heldout":
        training = [t for t in checked["trajectories"] if t["split"] == "train"]
        train_families = {t["family_id"] for t in training}
        selected_families = {t["family_id"] for t in trajectories}
        _input(bool(training) and bool(train_families), "Heldout evaluation requires training families")
        _require(train_families.isdisjoint(selected_families),
                 "Heldout evaluation requires independent indexed families")
    return checked, trajectories, windows


def _input_files(trajectories, windows):
    """Snapshot selected manifests and only batches accessed by selected windows."""
    files = []

    def bind(path, expected, kind, name):
        actual = _sha(path)
        _require(actual == expected.lower(), f"Evaluation input identity differs: {path}")
        files.append({"path": str(path), "sha256": actual, "kind": kind, "trajectory_id": name})

    for trajectory in trajectories:
        name, directory = trajectory["trajectory_id"], Path(trajectory["sequence_dir"])
        reader = FieldSequenceReader(directory)
        bind(directory / "sequence_manifest.json", trajectory["manifest_sha256"], "manifest", name)
        ranges = [w for w in windows if w["trajectory_id"] == name]
        for batch in reader.manifest["batches"]:
            if not any(batch["start_index"] < w["future_stop"]
                       and batch["stop_index"] > w["history_start"] for w in ranges):
                continue
            for filename, key in (("field_sample.npz", "npz_sha256"),
                                  ("metadata.json", "metadata_sha256"),
                                  ("bridge_response.json", "response_sha256")):
                bind(directory / batch["path"] / filename, batch[key], filename, name)
    return files


def _check_window(data, spec):
    _require(isinstance(data, dict) and set(data) == WINDOW_FIELDS,
             "Window must expose exactly the eight array fields")
    _require(all(isinstance(v, np.ndarray) for v in data.values()), "Window fields must be arrays")
    history, future = data["history"], data["future"]
    _require(history.ndim == future.ndim == 4 and history.shape[1] == future.shape[1] == 3
             and history.shape[0] == spec["history_frames"]
             and future.shape[0] == spec["forecast_frames"]
             and history.shape[2:] == future.shape[2:], "Window field shapes differ from index")
    ny, nx = history.shape[2:]
    _require(data["x"].shape == (nx,) and data["y"].shape == (ny,), "Window coordinate shape differs")
    for part, frames in (("history", len(history)), ("future", len(future))):
        mask, times = data[part + "_mask"], data[part + "_times"]
        _require(mask.dtype.kind == "b" and mask.shape == (frames, ny, nx)
                 and times.shape == (frames,), "Window mask/time shape differs")
        _require(np.array_equal(mask, np.broadcast_to(data["history_mask"][0], mask.shape)),
                 "Evaluation requires one static history/future mask")
    leads = data["future_times"] - data["history_times"][-1]
    _require(np.isfinite(leads).all() and np.all(leads > 0), "Invalid forecast leads")
    return leads, hashlib.sha256(data["history_mask"][0].tobytes()).hexdigest()


def _counts(windows):
    targets = Counter((w["trajectory_id"], frame) for w in windows
                      for frame in range(w["future_start"], w["future_stop"]))
    assignments = sum(targets.values())
    return {"window_count": len(windows), "forecast_frame_assignments": assignments,
            "unique_target_frames": len(targets),
            "repeated_future_assignments": assignments - len(targets),
            "target_frames_seen_more_than_once": sum(count > 1 for count in targets.values())}


def evaluate_baselines(index, *, split, mode, methods=("persistence", "last_two_linear")):
    """Return raw sampled-node micro metrics by lead/channel for one explicit split.

    Only history fields/masks/times and known prediction times reach a forecaster.
    Each window uses new observed history; this is not a free-running rollout.
    Arrays are discarded after each window. Repeated target assignments, if any,
    remain separate forecasts and are counted explicitly rather than deduplicated.
    """
    _input(isinstance(methods, (tuple, list)) and bool(methods)
           and all(isinstance(m, str) and m in METHOD_DEFINITIONS for m in methods)
           and len(set(methods)) == len(methods), "Methods must be distinct registered method names")
    checked, trajectories, windows = _selection(index, split, mode)
    before = _input_files(trajectories, windows)
    overall = {method: ForecastMetricAccumulator() for method in methods}
    per_trajectory = {t["trajectory_id"]: {m: ForecastMetricAccumulator() for m in methods}
                      for t in trajectories}
    mask_hashes, lead_seconds = {}, None
    for window in windows:
        name = window["trajectory_id"]
        data = read_window(checked, window["window_id"])
        leads, mask_sha = _check_window(data, checked["spec"])
        if lead_seconds is None:
            lead_seconds = leads.copy()
        _require(np.allclose(leads, lead_seconds, rtol=0., atol=1e-12),
                 "Forecast lead seconds differ across windows")
        _require(mask_hashes.setdefault(name, mask_sha) == mask_sha,
                 "Trajectory spatial mask changed between windows")
        for method in methods:
            prediction = forecast_from_history(data["history"], data["history_mask"],
                                               data["history_times"], data["future_times"], method=method)
            overall[method].add(prediction, data["future"], data["future_mask"])
            per_trajectory[name][method].add(prediction, data["future"], data["future_mask"])
            del prediction
        del data
    after = _input_files(trajectories, windows)
    _require(before == after, "Evaluation input files changed")
    _require(validate_window_index(index) == checked, "Window index changed during evaluation")
    summaries = []
    for trajectory in trajectories:
        name = trajectory["trajectory_id"]
        own_windows = [w for w in windows if w["trajectory_id"] == name]
        summaries.append({"trajectory_id": name, "family_id": trajectory["family_id"],
                          **_counts(own_windows), "static_mask_sha256": mask_hashes[name],
                          "methods": {m: a.result() for m, a in per_trajectory[name].items()}})
    return {"schema_version": 1, "status": "PASS",
            "status_meaning": "Execution, data-contract and identity checks passed; no accuracy threshold",
            "mode": mode, "split": split, "heldout_evaluation": mode == "heldout",
            "training_performed": False, "units": ["m/s", "m/s", "Pa"],
            "metric_units": {"mae": ["m/s", "m/s", "Pa"], "rmse": ["m/s", "m/s", "Pa"],
                             "sae": ["m/s", "m/s", "Pa"], "sse": ["(m/s)^2", "(m/s)^2", "Pa^2"],
                             "count": ["1", "1", "1"]},
            "channels": ["u", "v", "p"], "lead_seconds": lead_seconds.tolist(),
            "method_definitions": {m: METHOD_DEFINITIONS[m] for m in methods},
            "overall": {**_counts(windows), "methods": {m: a.result() for m, a in overall.items()}},
            "per_trajectory": summaries,
            "weighting": {"kind": "unweighted_sampled_fluid_nodes",
                          "aggregation": "Sum SAE/SSE/count, then MAE=SAE/count and RMSE=sqrt(SSE/count)",
                          "trajectory_window_counts": {s["trajectory_id"]: s["window_count"] for s in summaries},
                          "repeated_targets": "Count every forecast assignment; no independence claim"},
            "scientific_scope": {"spatial_integration": False, "normalization": False,
                                 "pressure_mean_removed": False, "rolling_observed_history": True,
                                 "free_running_rollout": False,
                                 "evaluation_population": "independent indexed heldout families" if mode == "heldout"
                                 else "selected training families only; not heldout generalization"},
            "identity_audit": {"index_payload_sha256": checked["payload_sha256"],
                               "scope": "Selected manifests and accessed batches; no MPH/native-bin read",
                               "inputs_unchanged": True, "files_checked": len(before),
                               "files": before}}
