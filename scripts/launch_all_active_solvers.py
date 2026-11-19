"""Launch all 4 interrupted CFD calibration simulations as detached background processes.

Slots:
  Socket 0, Slot A: C_rtol3e4_max005 (mesh2, rtol=3e-4, maxstep=0.05, numafirst=0)
  Socket 0, Slot B: F2_rtol2e3_max006 (mesh2_fast, rtol=2e-3, maxstep=0.06, numafirst=0)
  Socket 1, Slot A: F2_rtol2e3_max006 (mesh1, rtol=2e-3, maxstep=0.06, numafirst=1)
  Socket 1, Slot B: B2_rtol5e4_max005 (mesh2, rtol=5e-4, maxstep=0.05, numafirst=1)
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(r"D:\HydroDisturb-Bench")
PYTHON_EXE = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
CALIB_SCRIPT = REPO_ROOT / "scripts" / "calibrate_c04_3d.py"
QUEUE_PATH = Path(r"E:\cfd_3d\01_calibration\backfill_queue.json")
SCRATCH = Path(r"E:\comsol_scratch")
INI_FILE = SCRATCH / "comsolbatch_perf.ini"

JOBS = [
    {
        "slot_key": "N0-A",
        "case_id": "C_rtol3e4_max005",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k"),
        "numafirst": 0,
        "save_mph": False,
        "save_arg": "none",
        "log_subdir": "C_rtol3e4_max005",
    },
    {
        "slot_key": "N0-B",
        "case_id": "F2_rtol2e3_max006",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh2_fast"),
        "numafirst": 0,
        "save_mph": False,
        "save_arg": "none",
        "log_subdir": "F2_rtol2e3_max006",
    },
    {
        "slot_key": "N1-A",
        "case_id": "F2_rtol2e3_max006",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh1.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1"),
        "numafirst": 1,
        "save_mph": True,
        "save_arg": "F2_rtol2e3_max006",
        "log_subdir": "F2_rtol2e3_max006",
    },
    {
        "slot_key": "N1-B",
        "case_id": "B2_rtol5e4_max005",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k"),
        "numafirst": 1,
        "save_mph": True,
        "save_arg": "B2_rtol5e4_max005",
        "log_subdir": "B2_rtol5e4_max005",
    },
]


def backup_interrupted_logs(case_dir: Path, case_id: str) -> None:
    c_log = case_dir / "comsolbatch.log"
    r_log = case_dir.parent / f"{case_id}_runner.log"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if c_log.is_file():
        dest = case_dir / f"comsolbatch_interrupted_{ts}.log"
        shutil.copy2(c_log, dest)
        print(f"  [BACKUP] {c_log.name} -> {dest.name} ({c_log.stat().st_size} bytes)")
    if r_log.is_file():
        dest_r = case_dir.parent / f"{case_id}_runner_interrupted_{ts}.log"
        shutil.copy2(r_log, dest_r)
        print(f"  [BACKUP] {r_log.name} -> {dest_r.name}")


def clean_tmp_dir(case_id: str, out_dir: Path) -> None:
    # Identify matching tmpdirs in scratch/tmp
    tmp_root = SCRATCH / "tmp"
    if not tmp_root.is_dir():
        return
    for item in tmp_root.iterdir():
        if item.is_dir() and case_id in item.name:
            try:
                shutil.rmtree(item, ignore_errors=True)
                print(f"  [CLEAN TMP] Removed stale tmpdir: {item.name}")
            except Exception as e:
                print(f"  [WARN] Could not remove {item.name}: {e}")


def launch_job(job: dict) -> dict:
    case_id = job["case_id"]
    out_dir = job["out_dir"]
    case_dir = out_dir / job["log_subdir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    numafirst = job["numafirst"]
    save_arg = job["save_arg"]
    base_model = job["base_model"]

    print(f"\n>>> Preparing launch for {job['slot_key']} : {case_id} (NUMA socket {numafirst})")
    backup_interrupted_logs(case_dir, case_id)
    clean_tmp_dir(case_id, out_dir)

    cmd = [
        str(PYTHON_EXE),
        str(CALIB_SCRIPT),
        "run",
        "--base-model",
        str(base_model),
        "--out-dir",
        str(out_dir),
        "--ini",
        str(INI_FILE),
        "--scratch",
        str(SCRATCH),
        "--cases",
        case_id,
        "--t-end",
        "60.0",
        "--dt-out",
        "0.06",
        "--init-step",
        "auto",
        "--consistent",
        "bweuler",
        "--save-mph-cases",
        save_arg,
        "--workers",
        "1",
        "--worker-specs",
        f"16:1:{numafirst}:throughput",
        "--alloc",
        "scalable",
        "--priority",
        "abovenormal",
    ]

    runner_log = out_dir / f"{case_id}_runner.log"

    creation = 0
    if sys.platform == "win32":
        creation = int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008)) | int(
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )

    with runner_log.open("w", encoding="utf-8") as lf:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=lf,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )

    print(f"  [SPAWNED] Runner PID: {proc.pid}, Log: {runner_log}")
    return {
        "job": job,
        "runner_proc": proc,
        "runner_pid": proc.pid,
        "runner_log": runner_log,
        "case_dir": case_dir,
    }


def verify_and_update_queue(active_runners: list[dict]) -> None:
    print("\n>>> Waiting 15s to confirm COMSOL batch processes spawn and bind...")
    time.sleep(15.0)

    # Read live comsol processes
    ps_cmd = (
        "Get-CimInstance Win32_Process -Filter \"Name='comsolbatch.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    raw = (proc.stdout or "").strip()
    comsol_procs = []
    if raw:
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        comsol_procs = data

    print(f"  Found {len(comsol_procs)} live comsolbatch.exe processes.")
    for cp in comsol_procs:
        pid = cp.get("ProcessId")
        cmd = cp.get("CommandLine", "")
        m_nf = re.search(r"-numafirst\s+(\d+)", cmd)
        nf = m_nf.group(1) if m_nf else "?"
        m_mph = re.findall(r"([A-Za-z]:\\[^\s\"]+?\.mph)", cmd)
        out = m_mph[-1] if m_mph else "unknown"
        print(f"    comsolbatch PID {pid} (numafirst={nf}): {Path(out).name}")

    # Update backfill_queue.json
    if QUEUE_PATH.is_file():
        q_doc = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        q_doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        running_now = q_doc.setdefault("running_now", {})

        for item in active_runners:
            j = item["job"]
            key = j["slot_key"]
            r_pid = item["runner_pid"]
            # Find matching comsol PID
            matched_c_pid = None
            for cp in comsol_procs:
                cmd = cp.get("CommandLine", "")
                if j["case_id"] in cmd and str(j["out_dir"]) in cmd:
                    matched_c_pid = int(cp.get("ProcessId"))
                    break

            running_now[key] = {
                "numafirst": j["numafirst"],
                "label": f"{j['out_dir'].name}/{j['case_id']}",
                "out_dir": str(j["out_dir"]),
                "case_id": j["case_id"],
                "pid": matched_c_pid or "launching",
                "runner_pid": r_pid,
            }

            # Update queue entry if present
            for q_item in q_doc.get("queue", []):
                if q_item.get("case_id") == j["case_id"] and Path(q_item.get("out_dir", "")).resolve() == j["out_dir"].resolve():
                    q_item["status"] = "RUNNING"
                    q_item["numafirst"] = j["numafirst"]
                    q_item["runner_pid"] = r_pid
                    q_item["comsol_pid"] = matched_c_pid
                    q_item["launched_at"] = datetime.now().isoformat(timespec="seconds")

        QUEUE_PATH.write_text(json.dumps(q_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        print("\n  Updated backfill_queue.json with active solver telemetry.")


def main():
    print("=" * 60)
    print("Launching All 4 Interrupted COMSOL Calibration Solvers")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    spawned = []
    for job in JOBS:
        res = launch_job(job)
        spawned.append(res)
        time.sleep(2.0)  # Brief stagger to prevent disk I/O collision on model load

    verify_and_update_queue(spawned)
    print("\n" + "=" * 60)
    print("All 4 solvers successfully launched and verified.")
    print("=" * 60)


if __name__ == "__main__":
    main()
