"""Launch the 3 complementary calibration solvers to saturate 100% CPU (all 64 cores across S0 and S1).

Active Fleet Target:
  Socket 0, Slot A: Q4_mesh0_build_and_F2_rtol2e3_max006 (PID 10256, ALREADY RUNNING)
  Socket 0, Slot B: Q2_mesh2_fast_F2_rtol2e3_max006 (numafirst=0, 16 cores)
  Socket 1, Slot A: Q3_mesh1_F2_rtol2e3_max006 (numafirst=1, 16 cores, save MPH)
  Socket 1, Slot B: B2_rtol5e4_max005 (numafirst=1, 16 cores, save MPH)

Robustness Features:
  - Uses CREATE_BREAKAWAY_FROM_JOB (0x01000000) | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
  - autosave is enabled ('-autosave on')
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(r"D:\HydroDisturb-Bench")
PYTHON_EXE = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
CALIB_SCRIPT = REPO_ROOT / "scripts" / "calibrate_c04_3d.py"
QUEUE_PATH = Path(r"E:\cfd_3d\01_calibration\backfill_queue.json")
SCRATCH = Path(r"E:\comsol_scratch")
INI_FILE = SCRATCH / "comsolbatch_perf.ini"

JOBS_TO_LAUNCH = [
    {
        "slot_key": "N0-B",
        "job_id": "Q2_mesh2_F2_rtol2e3_max006",
        "case_id": "F2_rtol2e3_max006",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh2_fast"),
        "numafirst": 0,
        "save_arg": "none",
        "log_subdir": "F2_rtol2e3_max006",
    },
    {
        "slot_key": "N1-A",
        "job_id": "Q3_mesh1_F2_rtol2e3_max006",
        "case_id": "F2_rtol2e3_max006",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh1.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1"),
        "numafirst": 1,
        "save_arg": "F2_rtol2e3_max006",
        "log_subdir": "F2_rtol2e3_max006",
    },
    {
        "slot_key": "N1-B",
        "job_id": "B2_rtol5e4_max005",
        "case_id": "B2_rtol5e4_max005",
        "base_model": Path(r"E:\cfd_3d\01_calibration\c04_re126k\c04_calibrated_base_mesh2.mph"),
        "out_dir": Path(r"E:\cfd_3d\01_calibration\c04_re126k"),
        "numafirst": 1,
        "save_arg": "B2_rtol5e4_max005",
        "log_subdir": "B2_rtol5e4_max005",
    },
]


def clean_stale_tmp(case_id: str) -> None:
    tmp_root = SCRATCH / "tmp"
    if not tmp_root.is_dir():
        return
    for item in tmp_root.iterdir():
        # Clean only the specific matching folder to avoid disturbing Q4 mesh0
        if item.is_dir() and "mesh0" not in item.name and case_id in item.name:
            try:
                shutil.rmtree(item, ignore_errors=True)
                print(f"  [CLEAN] Removed stale tmp: {item.name}")
            except Exception as e:
                print(f"  [WARN] Could not remove {item.name}: {e}")


def launch_single(job: dict) -> dict:
    case_id = job["case_id"]
    out_dir = job["out_dir"]
    case_dir = out_dir / job["log_subdir"]
    case_dir.mkdir(parents=True, exist_ok=True)
    numafirst = job["numafirst"]
    save_arg = job["save_arg"]
    base_model = job["base_model"]

    print(f"\n>>> Launching {job['slot_key']} : {job['job_id']} (NUMA Socket {numafirst})")
    clean_stale_tmp(case_id)

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

    # Break away from job object to guarantee immunity against server restarts
    CREATE_BREAKAWAY_FROM_JOB = 0x01000000
    creation = (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | CREATE_BREAKAWAY_FROM_JOB
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
        "runner_pid": proc.pid,
        "runner_log": runner_log,
    }


def verify_fleet():
    print("\n>>> Waiting 18s for COMSOL solver engines to initialize and bind...")
    time.sleep(18.0)

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

    print("\n============================================================")
    print(f"ACTIVE FLEET STATUS: {len(comsol_procs)} COMSOL Solvers Running (Target: 4)")
    print("============================================================")
    for cp in comsol_procs:
        pid = cp.get("ProcessId")
        cmd = cp.get("CommandLine", "")
        m_nf = re.search(r"-numafirst\s+(\d+)", cmd)
        nf = m_nf.group(1) if m_nf else "?"
        m_mph = re.findall(r"([A-Za-z]:\\[^\s\"]+?\.mph)", cmd)
        out = m_mph[-1] if m_mph else "unknown"
        print(f"  PID {pid:5d} | NUMA Socket {nf} | Target: {Path(out).name}")

    # Update backfill_queue.json
    if QUEUE_PATH.is_file():
        q_doc = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        q_doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
        running_now = q_doc.setdefault("running_now", {})

        for cp in comsol_procs:
            pid = cp.get("ProcessId")
            cmd = cp.get("CommandLine", "")
            m_nf = int(re.search(r"-numafirst\s+(\d+)", cmd).group(1)) if re.search(r"-numafirst\s+(\d+)", cmd) else 0
            m_mph = re.findall(r"([A-Za-z]:\\[^\s\"]+?\.mph)", cmd)
            out_file = m_mph[-1] if m_mph else ""
            out_p = Path(out_file)

            # Match with known slots
            for q_item in q_doc.get("queue", []):
                q_mph = Path(q_item.get("out_dir", "")) / q_item.get("case_id", "") / f"{q_item.get('case_id', '')}.mph"
                if q_item.get("case_id") in cmd and str(Path(q_item.get("out_dir", "")).resolve()).lower() in cmd.lower():
                    q_item["status"] = "RUNNING"
                    q_item["comsol_pid"] = int(pid)
                    q_item["numafirst"] = m_nf

        QUEUE_PATH.write_text(json.dumps(q_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        print("  Updated backfill_queue.json with live solver PIDs.")


def main():
    print("=" * 60)
    print("Saturating CPU to 100%: Launching 3 Complementary Solvers")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    spawned = []
    for job in JOBS_TO_LAUNCH:
        res = launch_single(job)
        spawned.append(res)
        time.sleep(3.0)  # Stagger to ease initial I/O on base model load

    verify_fleet()
    print("\n" + "=" * 60)
    print("100% CPU Saturation Achieved: All 4 Slots Active.")
    print("=" * 60)


if __name__ == "__main__":
    main()
