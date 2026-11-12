"""Execution, completion and sampling services shared by branch CLIs.

Frozen experiment policy remains in the caller. This module preserves the
existing native readback, source identity and no-overwrite admission checks.
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path

from hydro_disturb_bench import field_sampling, field_sequence
from hydro_disturb_bench.comsol_java import bridge
from hydro_disturb_bench.field_sequence import export_full_field_sequence, load_full_field_sequence

from .evidence_io import file_sha256, persist_vectors, read_json, require, write_json
from .process_recording import ProcessRecorder


def snapshot_sources(entrypoint, additional_sources=()):
    """Bind the entrypoint, shared implementation and native launch sources."""
    package = Path(__file__).resolve().parent
    bridge_path = Path(bridge.__file__).resolve()
    paths = [Path(entrypoint).resolve(), bridge_path,
             bridge_path.with_name("errors.py"),
             bridge_path.with_name("file_transactions.py"),
             bridge_path.with_name("ComsolBridge.java"),
             bridge_path.with_name("ComsolStandalone.java")]
    paths.extend(package / name for name in ("__init__.py", "branch_execution.py",
                 "evidence_io.py", "process_recording.py", "solver_steps.py"))
    paths.extend(Path(path).resolve() for path in additional_sources)
    return {str(path): file_sha256(path) for path in paths}


def implementation_evidence(before):
    files = {path: {"sha256_before": expected,
                    "sha256_after": file_sha256(Path(path)) if Path(path).is_file() else "MISSING"}
             for path, expected in before.items()}
    java_class = Path(bridge.__file__).resolve().with_name("ComsolBridge.class")
    return {"source_files": files,
            "all_source_files_unchanged": all(item["sha256_before"] == item["sha256_after"] for item in files.values()),
            "compiled_class_path": str(java_class),
            "compiled_class_sha256_after": file_sha256(java_class) if java_class.is_file() else None,
            "compiled_class_note": "End-of-operation bytes; compilation may occur during the operation"}


def execute_branch(directory, command, source, target, operation, *, entrypoint,
                   additional_sources=(), process_runner=None):
    directory.mkdir(exist_ok=False)
    before = file_sha256(source)
    implementation_before = snapshot_sources(entrypoint, additional_sources)
    started = time.time()

    try:
        require(not target.exists() and not target.is_symlink(), "Output model already exists")
        with ProcessRecorder(directory, process_runner) as recorder:
            response = bridge.run_command(command, process_runner=recorder)
        write_json(directory / "readback.json", response)
        if "time_solver_log" in response:
            (directory / "time_solver.log").write_bytes(response["time_solver_log"].encode("utf-8"))
        require(response.get("status") == "SUCCESS" and response.get("operation") == operation,
                 "Unexpected branch operation response")
        require(Path(response["source_model"]).resolve() == source.resolve()
                 and Path(response["output_model"]).resolve() == target.resolve(),
                 "Actual model paths do not match requested branch paths")
        require(target.is_file(), "COMSOL did not save the branch model")
        require(file_sha256(source) == before, "Input model changed during COMSOL operation")
        persist_vectors(directory, response["native_vectors"])
        if "native_derivatives" in response:
            persist_vectors(directory, response["native_derivatives"], prefix="native_derivative")
        implementation = implementation_evidence(implementation_before)
        require(implementation["all_source_files_unchanged"],
                 "Execution source files changed during the operation")
        record = {"status": "COMPLETE", "operation": operation, "command": command,
                  "source_model": str(source), "source_sha256_before": before,
                  "source_sha256_after": file_sha256(source), "output_model": str(target),
                  "output_sha256": file_sha256(target), "readback_sha256": file_sha256(directory / "readback.json"),
                  "implementation": implementation,
                  "elapsed_seconds": time.time() - started}
        write_json(directory / "execution.json", record)
        return response, target, record
    except Exception as exc:
        write_json(directory / "failure.json", {
            "status": "FAIL", "error": str(exc), "details": getattr(exc, "details", ""),
            "traceback": traceback.format_exc(), "source_sha256_before": before,
            "source_sha256_after": file_sha256(source) if source.is_file() else "MISSING",
            "implementation": implementation_evidence(implementation_before),
            "partial_output_model": str(target), "elapsed_seconds": time.time() - started,
        })
        raise



def completed_stage(directory):
    record = read_json(directory / "execution.json")
    require(record.get("status") == "COMPLETE", "Stage is not complete")
    target = Path(record["output_model"])
    require(file_sha256(target) == record["output_sha256"], "Completed model bytes changed")
    require(file_sha256(directory / "readback.json") == record["readback_sha256"],
             "Completed stage readback changed")
    return read_json(directory / "readback.json"), target, record



def sample_branch(output, name, plan, *, entrypoint, additional_sources=(), process_runner=None):
    _, model, solved = completed_stage(output / name)
    directory = output / f"sample_{name}"
    if directory.exists():
        record = read_json(directory / "execution.json")
        require(record["source_sha256"] == solved["output_sha256"], "Sample model identity changed")
        require(file_sha256(directory / "fields/sequence_manifest.json") == record["manifest_sha256"],
                 "Completed sample manifest changed")
        load_full_field_sequence(directory / "fields")
        return
    directory.mkdir()
    started = time.time()
    before = snapshot_sources(entrypoint, (*additional_sources, field_sequence.__file__, field_sampling.__file__))
    try:
        with ProcessRecorder(directory, process_runner) as recorder:
            report = export_full_field_sequence(
                model, directory / "fields", start=plan["branch_time_s"], stop=plan["stop_s"],
                step=plan["output_step_s"], grid_shape=tuple(plan["grid_shape_nx_ny"]),
                batch_size=32, overlap_reference=None, process_runner=recorder,
            )
        require(file_sha256(model) == solved["output_sha256"], "Branch model changed during field export")
        implementation = implementation_evidence(before)
        require(implementation["all_source_files_unchanged"], "Sampling source files changed during export")
        write_json(directory / "execution.json", {"status": "COMPLETE", "source_model": str(model),
            "source_sha256": file_sha256(model), "manifest_sha256": file_sha256(directory / "fields/sequence_manifest.json"),
            "shape": report["shape"], "implementation": implementation, "elapsed_seconds": time.time() - started})
    except Exception as exc:
        write_json(directory / "failure.json", {"status": "FAIL", "error": str(exc),
            "details": getattr(exc, "details", ""), "traceback": traceback.format_exc(),
            "source_sha256_after": file_sha256(model), "implementation": implementation_evidence(before), "elapsed_seconds": time.time() - started})
        raise
