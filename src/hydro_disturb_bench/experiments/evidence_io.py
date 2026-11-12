"""Exclusive evidence writes and native-vector byte validation."""
from __future__ import annotations

import base64
import hashlib
import json
import re

import numpy as np

from hydro_disturb_bench.comsol_java import bridge


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path, data):
    with path.open("xb") as handle:
        handle.write((json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


def file_sha256(path):
    return bridge.compute_file_hash(path).lower()


def require(condition, message):
    if not condition:
        raise bridge.ModelValidationError(message)


def persist_vectors(directory, vectors, prefix="native"):
    records = {}
    for name, item in vectors.items():
        if item is None:
            continue
        require(re.fullmatch(r"[A-Za-z0-9_-]+", name), "Unsafe native vector name")
        require(item.get("dtype") == "float64" and item.get("byte_order") == "little",
                "Native vectors must declare float64 little-endian")
        raw = base64.b64decode(item["base64"], validate=True)
        count = item["count"]
        require(isinstance(count, int) and not isinstance(count, bool) and count > 0,
                "Native vector count must be a positive integer")
        require(len(raw) == count * 8 and np.isfinite(np.frombuffer(raw, dtype="<f8")).all(),
                "Native vector length/finiteness mismatch")
        require(hashlib.sha256(raw).hexdigest() == item["sha256"].lower(),
                "Native vector bytes do not match actual readback SHA-256")
        filename = f"{prefix}_{name}.bin"
        with (directory / filename).open("xb") as handle:
            handle.write(raw)
        records[name] = {**{key: value for key, value in item.items() if key != "base64"},
                         "path": filename}
    write_json(directory / f"{prefix}_vectors.json", records)
    return records
