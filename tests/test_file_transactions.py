"""Offline compatibility and failure retention for shared file transactions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from hydro_disturb_bench import comsol_java
from hydro_disturb_bench.comsol_java import bridge, errors, file_transactions


def test_transaction_reexports_preserve_function_identity():
    public_names = (
        "is_task_active",
        "task_staging_session",
        "quarantine_staging_file",
        "quarantine_staging_dir",
        "file_publish_lock",
        "publish_staging_file",
        "list_quarantine_records",
        "recover_unclaimed_staging",
    )
    bridge_only_names = (
        "is_pid_alive",
        "_is_file_locked",
        "_safe_quarantine_move",
        "_safe_quarantine_move_dir",
        "_atomic_publish_new_file",
        "_posix_rename_noreplace",
    )
    for name in public_names + bridge_only_names:
        assert getattr(bridge, name) is getattr(file_transactions, name), name
    for name in public_names:
        assert getattr(comsol_java, name) is getattr(file_transactions, name), name


def test_error_reexports_preserve_exception_identity_and_mapping():
    classes = (
        "ComsolError",
        "ModelNotFoundError",
        "ApiUnsupportedError",
        "NodeNotFoundError",
        "InvalidParameterError",
        "InvalidUnitError",
        "ComsolConnectionError",
        "ModelValidationError",
        "SolverFailedError",
    )
    for name in classes:
        assert getattr(bridge, name) is getattr(errors, name), name
        assert getattr(comsol_java, name) is getattr(errors, name), name
    assert bridge.map_error is errors.map_error
    assert file_transactions.ModelValidationError is errors.ModelValidationError
    error = errors.map_error(errors.ERR_MODEL_VALIDATION_FAILED, "rejected", "raw details")
    assert type(error) is bridge.ModelValidationError
    assert error.error_code == comsol_java.ERR_MODEL_VALIDATION_FAILED
    assert error.details == "raw details"


def test_empty_stage_raises_legacy_error_and_preserves_quarantine(tmp_path):
    stage = tmp_path / ".staging" / "stage_empty_model.mph"
    stage.parent.mkdir()
    stage.write_bytes(b"")
    target = tmp_path / "model.mph"

    with pytest.raises(bridge.ModelValidationError, match="missing or empty"):
        file_transactions.publish_staging_file(stage, target, "empty")

    assert not target.exists()
    assert not stage.exists()
    quarantine = tmp_path / "quarantine" / "empty_empty_model.mph"
    assert quarantine.is_file()
    assert quarantine.read_bytes() == b""


def test_recovery_move_failure_preserves_stage_and_lease_for_retry(tmp_path, capsys):
    stage = tmp_path / ".staging" / "stage_dead_model.mph"
    stage.parent.mkdir()
    stage.write_bytes(b"completed but unpublished model")
    lease = stage.parent / ".lease_dead.lock"
    lease_bytes = json.dumps({"pid": -1, "stage_id": "dead"}).encode()
    lease.write_bytes(lease_bytes)

    with patch.object(
        file_transactions, "_safe_quarantine_move", side_effect=OSError("storage unavailable")
    ) as move:
        recovered, skipped = file_transactions.recover_unclaimed_staging(
            tmp_path, min_age_sec=0, return_skipped=True
        )

    move.assert_called_once()
    assert recovered == skipped == []
    assert "RECOVER_WARNING" in capsys.readouterr().err
    assert stage.read_bytes() == b"completed but unpublished model"
    assert lease.read_bytes() == lease_bytes
    assert list((tmp_path / "quarantine").iterdir()) == []

    recovered = file_transactions.recover_unclaimed_staging(tmp_path, min_age_sec=0)
    assert len(recovered) == 1
    assert recovered[0].read_bytes() == b"completed but unpublished model"
    assert not stage.exists()
    assert not lease.exists()


def test_directory_quarantine_move_failure_retains_all_files(tmp_path, capsys):
    stage = tmp_path / ".staging" / "diagnostics"
    nested = stage / "nested" / "data.bin"
    nested.parent.mkdir(parents=True)
    nested.write_bytes(b"\x00\xff\r\noriginal bytes")
    (stage / "readback.json").write_bytes(b'{"status":"failed"}\n')
    before = {p.relative_to(stage): p.read_bytes() for p in stage.rglob("*") if p.is_file()}

    with patch.object(
        file_transactions.shutil, "move", side_effect=PermissionError("destination locked")
    ) as move:
        retained = file_transactions.quarantine_staging_dir(
            stage, tmp_path / "final_diagnostics", "task"
        )

    move.assert_called_once()
    assert retained == stage
    assert "QUARANTINE_WARNING" in capsys.readouterr().err
    assert {p.relative_to(stage): p.read_bytes() for p in stage.rglob("*") if p.is_file()} == before
    assert list((tmp_path / "quarantine").iterdir()) == []


def test_cross_process_publish_lock_timeout_retains_stage_then_allows_publish(tmp_path):
    stage = tmp_path / ".staging" / "stage_child_model.mph"
    stage.parent.mkdir()
    stage.write_bytes(b"child output bytes")
    target = tmp_path / "model.mph"
    worker = """
import sys
from pathlib import Path
from hydro_disturb_bench.comsol_java.bridge import publish_staging_file

try:
    publish_staging_file(Path(sys.argv[1]), Path(sys.argv[2]), "child", lock_timeout_sec=0.15)
except TimeoutError:
    print("publish-lock-timeout")
else:
    raise AssertionError("Child published while the parent held the lock")
"""
    source_dir = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (source_dir, env.get("PYTHONPATH"))))

    with file_transactions.file_publish_lock(tmp_path / ".locks" / "model.mph.lock"):
        result = subprocess.run(
            [sys.executable, "-c", worker, str(stage), str(target)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "publish-lock-timeout"
        assert stage.read_bytes() == b"child output bytes"
        assert not target.exists()
        assert not (tmp_path / "quarantine").exists()

    file_transactions.publish_staging_file(stage, target, "child")
    assert not stage.exists()
    assert target.read_bytes() == b"child output bytes"


def test_cross_process_recovery_skips_live_lease_then_recovers_released_stage(tmp_path):
    stage = tmp_path / ".staging" / "stage_live_model.mph"
    stage.parent.mkdir()
    stage.write_bytes(b"active model bytes")
    worker = """
import json
import sys
from hydro_disturb_bench.comsol_java.bridge import recover_unclaimed_staging

recovered, skipped = recover_unclaimed_staging(sys.argv[1], min_age_sec=0, return_skipped=True)
print(json.dumps({"recovered": [str(path) for path in recovered], "skipped": skipped}))
"""
    source_dir = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (source_dir, env.get("PYTHONPATH"))))

    def recover_in_child():
        result = subprocess.run(
            [sys.executable, "-c", worker, str(tmp_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    with file_transactions.task_staging_session(stage, "live"):
        result = recover_in_child()
        assert result["recovered"] == []
        assert len(result["skipped"]) == 1
        assert result["skipped"][0]["reason"] == "active_task_lease_held"
        assert stage.read_bytes() == b"active model bytes"

    result = recover_in_child()
    assert result["skipped"] == []
    assert len(result["recovered"]) == 1
    assert Path(result["recovered"][0]).read_bytes() == b"active model bytes"
    assert not stage.exists()
