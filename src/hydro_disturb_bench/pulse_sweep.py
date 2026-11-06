"""Pre-register small local-pulse parameter matrices without running COMSOL."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

from .comsol_java.errors import InvalidParameterError, ModelValidationError
from .pulse_parameters import validate_pulse_config

_TOP_KEYS = {"schema_version", "sweep_id", "base_config_path", "base_config_sha256", "cases"}
_CASE_KEYS = {"event_id", "config"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", re.ASCII)


def _input(condition, message):
    if not condition:
        raise InvalidParameterError(message)


def _require(condition, message):
    if not condition:
        raise ModelValidationError(message)


def _sha(path):
    # JSON configuration is tracked as text without a repository-wide EOL rule.
    # Hash its UTF-8 text representation after EOL normalization so a Windows
    # checkout cannot appear to be a different base configuration from Linux.
    data = Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _input(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidParameterError(f"Cannot read sweep JSON: {exc}") from exc


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def _physical_payload(config):
    """Canonicalize a case by physical settings, excluding its identity label."""
    payload = copy.deepcopy(config)
    payload.pop("event_id", None)
    return _canonical(payload)


def _validate_hash(value, label):
    _input(isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None,
           f"{label} must be a 64-character SHA-256 hexadecimal string")
    return value.lower()


def _case_config(base, case):
    _input(isinstance(case, dict) and set(case) == _CASE_KEYS,
           "Each sweep case must contain exactly event_id and config")
    event_id = case["event_id"]
    _input(isinstance(event_id, str) and _ID.fullmatch(event_id) is not None,
           "Sweep event_id must be an ASCII slug")
    config = validate_pulse_config(case["config"])
    _input(config["event_id"] == event_id, "Case event_id must match its pulse config")
    _require(config["mechanism"] == base["mechanism"], "Sweep mechanism differs from base mechanism")
    return config


def validate_sweep_config(config, *, root=None):
    """Validate and return a detached sweep manifest with resolved case configs.

    All cases remain in one caller-declared checkpoint family. This function
    creates no model, field tensor, or solver output and does not claim cases
    are independent validation families.
    """
    _input(isinstance(config, dict) and set(config) == _TOP_KEYS,
           "Sweep config contains missing or unknown keys")
    _input(type(config["schema_version"]) is int and config["schema_version"] == 1,
           "Sweep schema_version must be integer 1")
    _input(isinstance(config["sweep_id"], str) and _ID.fullmatch(config["sweep_id"]),
           "sweep_id must be an ASCII slug")
    relative = Path(config["base_config_path"])
    _input(not relative.is_absolute() and ".." not in relative.parts,
           "base_config_path must be repository-relative")
    root = Path.cwd().resolve() if root is None else Path(root).resolve()
    base_path = (root / relative).resolve()
    _input(base_path.is_relative_to(root) and base_path.is_file(), "Base config is missing or escapes root")
    base_sha = _validate_hash(config["base_config_sha256"], "base_config_sha256")
    _input(_sha(base_path) == base_sha, "Base config identity changed")
    base = _read(base_path)
    base = validate_pulse_config(base)
    cases = config["cases"]
    _input(isinstance(cases, list) and cases, "Sweep must contain at least one case")
    resolved, ids, payloads = [], set(), set()
    for case in cases:
        case_config = _case_config(base, case)
        event_id = case_config["event_id"]
        _input(event_id not in ids, "Sweep contains duplicate event_id")
        payload = _physical_payload(case_config)
        _input(payload not in payloads, "Sweep contains duplicate case configuration")
        ids.add(event_id)
        payloads.add(payload)
        resolved.append(case_config)
    result = {
        "schema_version": 1,
        "operation": "pre_register_local_pulse_sweep",
        "sweep_id": config["sweep_id"],
        "base_config_path": relative.as_posix(),
        "base_config_sha256": base_sha,
        "case_count": len(resolved),
        "cases": resolved,
        "family_policy": "All cases share the registered M3 checkpoint family; parameter changes do not create independent holdout families.",
        "solver_policy": "Each case must use the configurable M3 runner with a new output directory; this manifest does not run COMSOL.",
    }
    result["manifest_sha256"] = hashlib.sha256(_canonical(result)).hexdigest()
    return copy.deepcopy(result)


def load_sweep_config(path, *, root=None):
    path = Path(path).resolve()
    return validate_sweep_config(_read(path), root=root if root is not None else path.parents[1])


def save_sweep_manifest(manifest, path):
    """Save a validated manifest once; do not overwrite historical plans."""
    path = Path(path)
    _input(isinstance(manifest, dict) and manifest.get("operation") == "pre_register_local_pulse_sweep",
           "Expected a validated local pulse sweep manifest")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=True, indent=2, allow_nan=False)
        stream.write("\n")
    return path
