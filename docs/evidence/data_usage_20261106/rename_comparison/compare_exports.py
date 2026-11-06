"""Offline alternating old/current export observations; no publication retries."""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
import platform
import subprocess
import sys
import time
import traceback
import types
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from hydro_disturb_bench.comsol_java import bridge
from test_field_sequence import AnalyticComsol

BASE = "4b7f74c658739908532b15277bb46a6495b02aad"
RELATIVE = "src/hydro_disturb_bench/field_sequence.py"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def handles():
    value = ctypes.c_ulong()
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    if not kernel.GetProcessHandleCount(kernel.GetCurrentProcess(), ctypes.byref(value)):
        raise ctypes.WinError()
    return value.value


def module_from_bytes(label, source):
    path = HERE / (label + "_field_sequence.py")
    with path.open("xb") as stream:
        stream.write(source)
    name = "hydro_disturb_bench._rename_ab_" + label
    module = types.ModuleType(name)
    module.__package__ = "hydro_disturb_bench"
    module.__file__ = str(path)
    sys.modules[name] = module
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def main():
    old = subprocess.run(["git", "show", BASE + ":" + RELATIVE], cwd=ROOT,
                         capture_output=True, check=True).stdout
    current = (ROOT / RELATIVE).read_bytes()
    modules = {"old": module_from_bytes("old", old),
               "current": module_from_bytes("current", current)}
    description = {
        "base_commit": BASE, "old_source_sha256": digest(old),
        "current_source_sha256": digest(current), "python": sys.version,
        "platform": platform.platform(), "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "parameters": {"start": 170., "stop": 171.8, "step": .2,
                       "grid_shape": [17, 9], "batch_size": 4, "frames": 10},
        "repetitions_each": 10, "ordering": "old,current repeated ten times",
        "substitution": "only bridge._run_bridge = AnalyticComsol(model)",
        "no_comsol": True, "no_retry": True,
        "shared_source_sha256": {name: digest((ROOT / name).read_bytes()) for name in (
            "src/hydro_disturb_bench/field_sampling.py",
            "src/hydro_disturb_bench/comsol_java/bridge.py",
            "src/hydro_disturb_bench/comsol_java/file_transactions.py",
            "tests/test_field_sequence.py")},
    }
    save(HERE / "execution_plan.json", description)
    rows = []
    original = bridge._run_bridge
    try:
        for repeat in range(10):
            for label in ("old", "current"):
                directory = HERE / "trials" / f"{repeat + 1:02d}_{label}"
                directory.mkdir(parents=True, exist_ok=False)
                model = directory / "source.mph"
                model.write_bytes(b"immutable analytic saved-model fixture")
                fake = AnalyticComsol(model)
                bridge._run_bridge = fake
                row = {"repeat": repeat + 1, "implementation": label,
                       "trial_directory": str(directory), "handle_count_before": handles()}
                started = time.perf_counter()
                try:
                    report = modules[label].export_full_field_sequence(
                        model, directory / "sequence", start=170., stop=171.8,
                        step=.2, grid_shape=(17, 9), batch_size=4)
                    row.update(status="PASS", report_status=report["status"],
                               shape=report["shape"], published=True)
                except Exception as exc:
                    row.update(status="FAIL", exception_type=type(exc).__name__,
                               error=str(exc), errno=getattr(exc, "errno", None),
                               winerror=getattr(exc, "winerror", None),
                               traceback=traceback.format_exc(),
                               published=(directory / "sequence").exists())
                row.update(elapsed_s=time.perf_counter() - started,
                           handle_count_after=handles(), external_calls=len(fake.calls),
                           source_sha256=digest(model.read_bytes()),
                           preserved_stages=[str(p) for p in directory.glob(".staging_field_sequence_*")])
                save(directory / "observation.json", row)
                rows.append(row)
                print(json.dumps({k: row[k] for k in ("repeat", "implementation", "status",
                    "elapsed_s", "handle_count_before", "handle_count_after")}
                    | {"error": row.get("error")}), flush=True)
    finally:
        bridge._run_bridge = original
    summary = {"plan": description, "observations": rows, "totals": {
        label: {status: sum(row["implementation"] == label and row["status"] == status for row in rows)
                for status in ("PASS", "FAIL")} for label in modules},
        "finished_at": datetime.now(timezone.utc).isoformat()}
    save(HERE / "results.json", summary)
    print(json.dumps({"totals": summary["totals"]}), flush=True)


if __name__ == "__main__":
    main()
