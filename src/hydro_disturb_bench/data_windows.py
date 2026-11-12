"""Family-separated window indexes over validated saved field sequences.

Ancestry hashes are supplied by the caller from its provenance records. This
module detects conflicting identities; it does not infer COMSOL genealogy.
Index digests detect changes, not authenticity: pin the saved file SHA-256 when
loading an index from an independently trusted catalog.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from numbers import Integral
from pathlib import Path

from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .field_sequence import FieldSequenceReader

SPLITS = ("train", "validation", "test")
_RECORD_FIELDS = (
    "trajectory_id", "family_id", "sequence_dir", "manifest_sha256",
    "root_model_sha256", "checkpoint_sha256", "native_state_sha256",
)
_SCOPE = {
    "at_build": "all_batches",
    "at_read": "accessed_batches",
    "ancestry": "caller_supplied_not_inferred",
}


def _require(condition, message):
    if not condition:
        raise ModelValidationError(message)


def _input(condition, message):
    if not condition:
        raise InvalidParameterError(message)


def _identifier(value, label):
    _input(isinstance(value, str) and bool(value) and value == value.strip(),
           f"{label} must be a nonempty string without surrounding whitespace")
    return value


def _sha(value, label, *, optional=False):
    if optional and value is None:
        return None
    _input(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None,
           f"{label} must be a 64-character SHA-256 hexadecimal string")
    return value.lower()


@dataclass(frozen=True)
class TrajectoryRecord:
    trajectory_id: str
    family_id: str
    sequence_dir: Path
    manifest_sha256: str
    root_model_sha256: str
    checkpoint_sha256: str | None = None
    native_state_sha256: str | None = None

    def __post_init__(self):
        _identifier(self.trajectory_id, "trajectory_id")
        _identifier(self.family_id, "family_id")
        _input(isinstance(self.sequence_dir, (str, Path)), "sequence_dir must be a path")
        try:
            directory = Path(self.sequence_dir).expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise InvalidParameterError(f"Invalid sequence directory: {self.sequence_dir}") from exc
        _input(directory.is_dir(), "sequence_dir must be an existing directory")
        object.__setattr__(self, "sequence_dir", directory)
        for name in _RECORD_FIELDS[3:]:
            object.__setattr__(self, name, _sha(getattr(self, name), name,
                               optional=name in ("checkpoint_sha256", "native_state_sha256")))


@dataclass(frozen=True)
class WindowSpec:
    history_frames: int
    forecast_frames: int
    stride: int = 1

    def __post_init__(self):
        for name in ("history_frames", "forecast_frames", "stride"):
            value = getattr(self, name)
            _input(isinstance(value, Integral) and not isinstance(value, bool) and value > 0,
                   f"{name} must be a positive integer, not bool")
            object.__setattr__(self, name, int(value))


def _catalog(records, assignments):
    records = tuple(records)
    _input(bool(records) and all(isinstance(r, TrajectoryRecord) for r in records),
           "At least one TrajectoryRecord is required")
    _input(isinstance(assignments, dict), "assignments must explicitly map family IDs to splits")
    families = {record.family_id for record in records}
    _input(set(assignments) == families, "Family assignments must be complete with no extra families")
    _input(all(split in SPLITS for split in assignments.values()), "Unknown family split")
    identities, trajectory_ids, directories = {}, set(), set()
    catalog, readers = [], []

    def bind(identity, family):
        if identity is None:
            return
        previous = identities.setdefault(identity, family)
        _require(previous == family, "Shared sequence/model/ancestry identity has conflicting family IDs")

    for record in sorted(records, key=lambda r: r.trajectory_id):
        _input(record.trajectory_id not in trajectory_ids, "Duplicate trajectory_id")
        _input(record.sequence_dir not in directories, "Duplicate resolved sequence directory")
        trajectory_ids.add(record.trajectory_id)
        directories.add(record.sequence_dir)
        reader = FieldSequenceReader(record.sequence_dir)
        _require(reader.manifest_sha256.lower() == record.manifest_sha256,
                 "Trajectory manifest SHA-256 differs from the supplied identity")
        manifest = reader.manifest
        source_sha = _sha(manifest.get("source_sha256"), "manifest source_sha256")
        source = manifest.get("source_model")
        _require(isinstance(source, str) and bool(source.strip()), "Missing sequence source model")
        source_path = str(Path(source).resolve())
        bind(("model_path", source_path), record.family_id)
        for sha in (record.manifest_sha256, source_sha, record.root_model_sha256,
                    record.checkpoint_sha256, record.native_state_sha256):
            bind(("sha256", sha) if sha is not None else None, record.family_id)
        for batch in manifest["batches"]:
            bind(("sha256", _sha(batch.get("npz_sha256"), "batch npz_sha256")), record.family_id)
        item = asdict(record)
        item["sequence_dir"] = str(record.sequence_dir)
        item.update(source_model=source_path, source_sha256=source_sha,
                    frame_count=len(reader), split=assignments[record.family_id])
        catalog.append(item)
        readers.append(reader)
    return catalog, readers


def _split_ids(catalog):
    return {split: [item["trajectory_id"] for item in catalog if item["split"] == split]
            for split in SPLITS}


def split_trajectory_families(records, assignments):
    """Return explicit split memberships after identity checks; read no NPZ arrays."""
    catalog, _ = _catalog(records, assignments)
    return _split_ids(catalog)


def _json_bytes(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ModelValidationError("Window index must contain finite JSON values only") from exc


def _assemble(catalog, assignments, spec):
    windows = []
    length = spec.history_frames + spec.forecast_frames
    for item in catalog:
        _input(item["frame_count"] >= length,
               f"Trajectory {item['trajectory_id']} is shorter than one complete window")
        for start in range(0, item["frame_count"] - length + 1, spec.stride):
            middle = start + spec.history_frames
            windows.append({
                "window_id": f"{item['trajectory_id']}:{start:08d}",
                "trajectory_id": item["trajectory_id"], "family_id": item["family_id"],
                "split": item["split"], "history_start": start, "history_stop": middle,
                "future_start": middle, "future_stop": start + length,
            })
    splits = _split_ids(catalog)
    result = {
        "schema_version": 1, "status": "PASS", "operation": "build_window_index",
        "spec": asdict(spec), "validation_scope": dict(_SCOPE),
        "family_assignments": dict(sorted(assignments.items())),
        "trajectories": catalog, "splits": splits,
        "counts": {"trajectories": len(catalog), "windows": len(windows),
                   "by_split": {split: {"trajectories": len(splits[split]),
                                        "windows": sum(w["split"] == split for w in windows)}
                                for split in SPLITS}},
        "windows": windows,
    }
    result["payload_sha256"] = hashlib.sha256(_json_bytes(result)).hexdigest()
    return result


def build_window_index(records, assignments, spec):
    """Verify every batch, then construct windows without storing field arrays."""
    _input(isinstance(spec, WindowSpec), "spec must be a WindowSpec")
    catalog, readers = _catalog(records, assignments)
    for reader in readers:
        summary = reader.verify_all()
        _require(summary.get("status") == "PASS", "Sequence batch verification did not pass")
    return _assemble(catalog, assignments, spec)


def validate_window_index(index):
    """Check digest, identities and regenerated windows without loading NPZ batches.

    The stored build-time validation scope is not a claim that all batches have
    been rechecked now. A subsequent read validates only its accessed batches.
    """
    _require(isinstance(index, dict), "Window index must be an object")
    raw = json.loads(_json_bytes(index))
    expected_sha = raw.pop("payload_sha256", None)
    _require(isinstance(expected_sha, str) and re.fullmatch(r"[0-9a-f]{64}", expected_sha) is not None,
             "Missing or invalid window index digest")
    _require(hashlib.sha256(_json_bytes(raw)).hexdigest() == expected_sha,
             "Window index digest mismatch")
    try:
        spec = WindowSpec(**raw["spec"])
        entries = raw["trajectories"]
        _require(isinstance(entries, list), "Invalid trajectory entries")
        records = [TrajectoryRecord(**{key: item[key] for key in _RECORD_FIELDS}) for item in entries]
        catalog, _ = _catalog(records, raw["family_assignments"])
        rebuilt = _assemble(catalog, raw["family_assignments"], spec)
    except (KeyError, TypeError, ValueError, InvalidParameterError) as exc:
        raise ModelValidationError("Invalid window index contract") from exc
    _require(rebuilt == {**raw, "payload_sha256": expected_sha},
             "Window index split, identity, count or window boundaries differ from its contract")
    return rebuilt


def read_window(index, window_id):
    """Load one bound range and return only the eight learner-visible array fields."""
    checked = validate_window_index(index)
    _identifier(window_id, "window_id")
    matches = [entry for entry in checked["windows"] if entry["window_id"] == window_id]
    _input(len(matches) == 1, "Unknown window_id")
    entry = matches[0]
    item = next(item for item in checked["trajectories"]
                if item["trajectory_id"] == entry["trajectory_id"])
    reader = FieldSequenceReader(item["sequence_dir"])
    _require(reader.manifest_sha256.lower() == item["manifest_sha256"], "Sequence manifest changed")
    data = reader.read_frames(entry["history_start"], entry["future_stop"])
    cut = checked["spec"]["history_frames"]
    return {
        "history": data["values"][:cut].copy(), "future": data["values"][cut:].copy(),
        "history_mask": data["mask"][:cut].copy(), "future_mask": data["mask"][cut:].copy(),
        "x": data["x"].copy(), "y": data["y"].copy(),
        "history_times": data["times"][:cut].copy(), "future_times": data["times"][cut:].copy(),
    }


def save_window_index(index, path):
    """Save a validated JSON index exclusively; never replace an existing file."""
    checked = validate_window_index(index)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(checked, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


def load_window_index(path, *, expected_sha256=None):
    """Load and validate an index; optionally bind the exact saved file bytes."""
    content = Path(path).read_bytes()
    if expected_sha256 is not None:
        expected = _sha(expected_sha256, "expected_sha256")
        _require(hashlib.sha256(content).hexdigest() == expected, "Saved window index SHA-256 mismatch")

    def unique_object(items):
        result = {}
        for key, value in items:
            _require(key not in result, "Duplicate JSON key in window index")
            result[key] = value
        return result

    try:
        index = json.loads(content, object_pairs_hook=unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ModelValidationError("Invalid window index JSON") from exc
    return validate_window_index(index)
