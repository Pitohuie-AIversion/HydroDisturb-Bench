"""Contract tests for pre-registered pulse matrices; no COMSOL or tensors."""
from __future__ import annotations

import hashlib
import json

import pytest

from hydro_disturb_bench.comsol_java.errors import InvalidParameterError
from hydro_disturb_bench.pulse_sweep import (
    load_sweep_config,
    save_sweep_manifest,
    validate_sweep_config,
)


def _base(tmp_path):
    path = tmp_path / "base.json"
    config = {"schema_version": 1, "event_id": "base", "mechanism": "gaussian_body_acceleration",
              "pulse": {"center_m": [0.15, 0.12], "direction_deg": 90,
                         "acceleration_m_s2": 0.005, "sigma_m": 0.01, "on_s": 181, "off_s": 183}}
    path.write_text(json.dumps(config), encoding="utf-8", newline="\n")
    return path, config


def _sweep(base, base_config, cases):
    return {"schema_version": 1, "sweep_id": "matrix", "base_config_path": base.name,
            "base_config_sha256": hashlib.sha256(base.read_bytes()).hexdigest(), "cases": cases}


def _case(event_id, direction=90):
    return {"event_id": event_id, "config": {"schema_version": 1, "event_id": event_id,
            "mechanism": "gaussian_body_acceleration", "pulse": {"center_m": [0.15, 0.12],
            "direction_deg": direction, "acceleration_m_s2": 0.005, "sigma_m": 0.01,
            "on_s": 181, "off_s": 183}}}


def test_loads_registered_repository_matrix():
    path = "configs/m3_local_pulse_sweep.json"
    result = load_sweep_config(path, root=".")
    assert result["case_count"] == 3
    assert [case["event_id"] for case in result["cases"]] == [
        "m3-local-pulse-default", "m3-local-pulse-diagonal-debug", "m3-local-pulse-downward"]
    assert result["manifest_sha256"] == hashlib.sha256(
        json.dumps({k: v for k, v in result.items() if k != "manifest_sha256"},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def test_rejects_duplicate_ids_and_payloads(tmp_path):
    base, config = _base(tmp_path)
    with pytest.raises(InvalidParameterError, match="duplicate event_id"):
        validate_sweep_config(_sweep(base, config, [_case("one"), _case("one", -90)]), root=tmp_path)
    with pytest.raises(InvalidParameterError, match="duplicate case"):
        validate_sweep_config(_sweep(base, config, [_case("one"), _case("two")]), root=tmp_path)


def test_rejects_changed_base_and_unknown_case_keys(tmp_path):
    base, config = _base(tmp_path)
    sweep = _sweep(base, config, [_case("one")])
    base.write_text(base.read_text(encoding="utf-8").replace("0.005", "0.006"), encoding="utf-8")
    with pytest.raises(InvalidParameterError, match="identity changed"):
        validate_sweep_config(sweep, root=tmp_path)
    base, config = _base(tmp_path)
    bad = _case("one")
    bad["extra"] = True
    with pytest.raises(InvalidParameterError, match="exactly event_id and config"):
        validate_sweep_config(_sweep(base, config, [bad]), root=tmp_path)


def test_rejects_case_that_is_not_a_valid_pulse(tmp_path):
    base, config = _base(tmp_path)
    bad = _case("one")
    bad["config"]["pulse"]["on_s"] = 181.1
    with pytest.raises(InvalidParameterError):
        validate_sweep_config(_sweep(base, config, [bad]), root=tmp_path)


def test_save_is_exclusive_and_manifest_is_detached(tmp_path):
    base, config = _base(tmp_path)
    source = _sweep(base, config, [_case("one")])
    manifest = validate_sweep_config(source, root=tmp_path)
    source["cases"][0]["config"]["pulse"]["direction_deg"] = -90
    assert manifest["cases"][0]["pulse"]["direction_deg"] == 90
    target = tmp_path / "manifest.json"
    save_sweep_manifest(manifest, target)
    assert target.is_file()
    with pytest.raises(FileExistsError):
        save_sweep_manifest(manifest, target)
