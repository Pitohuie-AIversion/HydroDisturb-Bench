"""NUMA-aware COMSOL backfill queue inspector and detached dispatcher for Swarm workers.

Reads ``E:\\cfd_3d\\01_calibration\\backfill_queue.json``, inspects live
``comsolbatch.exe`` processes per NUMA socket (``numafirst=0`` vs ``numafirst=1``),
enforces RAM/disk/idempotency safety gates, builds prerequisite mesh models if
specified (e.g. ``BuildMesh1``), and launches the next READY job into the freed
NUMA socket as a detached background runner.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SRC_DIR = REPO_ROOT / "src"
for _p in (SRC_DIR, SCRIPTS_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from hydro_disturb_bench.comsol_java.bridge import find_comsol_root, get_comsol_bin_dir

DEFAULT_QUEUE_PATH = Path(r"E:\cfd_3d\01_calibration\backfill_queue.json")
DEFAULT_SCRATCH = Path(r"E:\comsol_scratch")
DEFAULT_INI = Path(r"E:\comsol_scratch\comsolbatch_perf.ini")
CALIB_SCRIPT = REPO_ROOT / "scripts" / "calibrate_c04_3d.py"


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def get_system_health(drive: str = "E:\\") -> dict[str, float]:
    """Return free/total physical RAM and free disk GB on Windows."""
    if sys.platform == "win32":
        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        free_ram_gb = stat.ullAvailPhys / (1024**3)
        total_ram_gb = stat.ullTotalPhys / (1024**3)
    else:
        free_ram_gb = 64.0
        total_ram_gb = 128.0
    usage = shutil.disk_usage(drive)
    free_disk_gb = usage.free / (1024**3)
    return {
        "free_ram_gb": round(free_ram_gb, 2),
        "total_ram_gb": round(total_ram_gb, 2),
        "free_disk_e_gb": round(free_disk_gb, 2),
    }


def parse_comsol_cmdline(pid: int, cmdline: str) -> dict[str, Any]:
    m_bind = re.search(r"-np\s+(\d+)\s+-numasets\s+(\d+)\s+-numafirst\s+(\d+)", cmdline)
    m_out = re.search(r"([A-Za-z]:\\[^\s\"]+?\.mph)\b", cmdline, flags=re.IGNORECASE)
    # Specifically prefer the second .mph (output .mph) when both base and out .mph appear
    all_mph = re.findall(r"([A-Za-z]:\\[^\s\"]+?\.mph)", cmdline, flags=re.IGNORECASE)
    out_mph = all_mph[-1] if all_mph else (m_out.group(1) if m_out else "")
    return {
        "pid": int(pid),
        "np": int(m_bind.group(1)) if m_bind else None,
        "numasets": int(m_bind.group(2)) if m_bind else None,
        "numafirst": int(m_bind.group(3)) if m_bind else None,
        "out_mph": out_mph,
    }


def list_live_comsol_processes() -> list[dict[str, Any]]:
    """Query active comsolbatch.exe instances via PowerShell CIM."""
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
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [parse_comsol_cmdline(int(item["ProcessId"]), str(item.get("CommandLine") or "")) for item in data]


def summarize_slots(
    live_procs: list[dict[str, Any]],
    *,
    max_per_socket: int = 2,
) -> dict[str, Any]:
    by_socket: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
    unassigned: list[dict[str, Any]] = []
    for p in live_procs:
        nf = p.get("numafirst")
        if nf in (0, 1):
            # If numasets == 2 (-np 32 across both sockets), count on both sockets to avoid oversubscription
            if p.get("numasets") == 2:
                by_socket[0].append(p)
                by_socket[1].append(p)
            else:
                by_socket[nf].append(p)
        else:
            unassigned.append(p)

    free_sockets = [s for s in (0, 1) if len(by_socket[s]) < max_per_socket]
    return {
        "total_live": len(live_procs),
        "socket_0_count": len(by_socket[0]),
        "socket_1_count": len(by_socket[1]),
        "socket_0_pids": [p["pid"] for p in by_socket[0]],
        "socket_1_pids": [p["pid"] for p in by_socket[1]],
        "free_sockets": free_sockets,
        "unassigned": unassigned,
        "live": live_procs,
    }


def _target_mph_path(job: dict[str, Any]) -> Path:
    return Path(job["out_dir"]) / job["case_id"] / f"{job['case_id']}.mph"


def _summary_path(job: dict[str, Any]) -> Path:
    return Path(job["out_dir"]) / job["case_id"] / "calibration_case_summary.json"


def select_next_job(
    queue_doc: dict[str, Any],
    *,
    health: dict[str, float],
    live_procs: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Return the highest-priority eligible READY job and skip reasons for others."""
    active_outs = {str(Path(p["out_mph"]).resolve()).lower() for p in live_procs if p.get("out_mph")}
    skipped: list[dict[str, str]] = []
    jobs = sorted(queue_doc.get("queue", []), key=lambda j: int(j.get("priority", 999)))

    for job in jobs:
        jid = str(job.get("job_id"))
        st = str(job.get("status", "")).upper()
        if st not in {"READY", "READY_WITH_GATE"}:
            continue
        if _summary_path(job).is_file():
            skipped.append({"job_id": jid, "reason": "already_completed_summary_exists"})
            continue
        tgt = str(_target_mph_path(job).resolve()).lower()
        if tgt in active_outs:
            skipped.append({"job_id": jid, "reason": "already_running_live"})
            continue
        need_ram = float(job.get("min_free_ram_gb", 22.0))
        need_disk = float(job.get("min_free_disk_e_gb", 80.0))
        if health["free_ram_gb"] < need_ram:
            skipped.append({"job_id": jid, "reason": f"insufficient_ram({health['free_ram_gb']}<{need_ram})"})
            continue
        if health["free_disk_e_gb"] < need_disk:
            skipped.append({"job_id": jid, "reason": f"insufficient_disk({health['free_disk_e_gb']}<{need_disk})"})
            continue
        return job, skipped
    return None, skipped


def _run_pre_build_mesh(job: dict[str, Any], *, numafirst: int, ini_file: Path, scratch: Path) -> None:
    pb = job.get("pre_build_mesh")
    if not pb:
        return
    dst_mph = Path(pb["dst_mph"])
    if dst_mph.is_file():
        return
    src_mph = Path(pb["src_mph"])
    class_dir = Path(pb["class_dir"])
    class_file = class_dir / f"{pb['class_name']}.class"
    if not class_file.is_file():
        raise FileNotFoundError(f"Pre-build class file missing: {class_file}")
    log_path = Path(pb["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    dst_mph.parent.mkdir(parents=True, exist_ok=True)

    comsol_root = find_comsol_root()
    comsolbatch = get_comsol_bin_dir(comsol_root) / "comsolbatch.exe"
    w_tmp = scratch / "tmp" / f"prebuild_{job['job_id']}"
    w_rec = scratch / "recoveries" / f"prebuild_{job['job_id']}"
    w_tmp.mkdir(parents=True, exist_ok=True)
    w_rec.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["TEMP"] = str(w_tmp)
    env["TMP"] = str(w_tmp)

    cmd = [
        str(comsolbatch),
        "-nosave",
        "-comsolinifile",
        str(ini_file),
        "-tmpdir",
        str(w_tmp),
        "-recoverydir",
        str(w_rec),
        "-autosave",
        "off",
        "-np",
        "16",
        "-numasets",
        "1",
        "-numafirst",
        str(numafirst),
        "-mpmode",
        "throughput",
        "-alloc",
        "scalable",
        "-blas",
        "mkl",
        "-inputfile",
        str(class_file),
        str(src_mph),
        str(dst_mph),
    ]
    proc = subprocess.run(
        cmd,
        cwd=str(class_dir),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_path.write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
    if proc.returncode != 0 or not dst_mph.is_file() or '"status": "SUCCESS"' not in (proc.stdout or ""):
        raise RuntimeError(f"Pre-build mesh failed (rc={proc.returncode}): {log_path}")


def dispatch_one_job(
    *,
    queue_path: Path,
    requested_numafirst: int | None = None,
    dry_run: bool = False,
    ini_file: Path = DEFAULT_INI,
    scratch: Path = DEFAULT_SCRATCH,
    python_exe: Path | None = None,
) -> dict[str, Any]:
    queue_doc = json.loads(queue_path.read_text(encoding="utf-8"))
    policy = queue_doc.get("policy", {})
    max_per_socket = int(policy.get("max_slots_per_numa_socket", 2))

    health = get_system_health("E:\\")
    live_procs = list_live_comsol_processes()
    slots = summarize_slots(live_procs, max_per_socket=max_per_socket)

    if requested_numafirst is not None:
        if requested_numafirst not in slots["free_sockets"]:
            return {
                "status": "NO_FREE_SLOT",
                "requested_numafirst": requested_numafirst,
                "slots": slots,
                "health": health,
            }
        target_socket = requested_numafirst
    else:
        if not slots["free_sockets"]:
            return {"status": "ALL_SLOTS_FULL", "slots": slots, "health": health}
        target_socket = slots["free_sockets"][0]

    job, skipped = select_next_job(queue_doc, health=health, live_procs=live_procs)
    if job is None:
        return {
            "status": "NO_ELIGIBLE_JOB",
            "target_socket": target_socket,
            "skipped": skipped,
            "slots": slots,
            "health": health,
        }

    py = python_exe or Path(sys.executable)
    out_dir = Path(job["out_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    save_arg = job["case_id"] if job.get("save_mph", False) else "none"
    cmd = [
        str(py),
        str(CALIB_SCRIPT),
        "run",
        "--base-model",
        str(job["base_model"]),
        "--out-dir",
        str(out_dir),
        "--ini",
        str(ini_file),
        "--scratch",
        str(scratch),
        "--cases",
        str(job["case_id"]),
        "--t-end",
        str(job.get("t_end", 60.0)),
        "--dt-out",
        str(job.get("dt_out", 0.06)),
        "--init-step",
        "auto",
        "--consistent",
        "bweuler",
        "--save-mph-cases",
        save_arg,
        "--workers",
        "1",
        "--worker-specs",
        f"16:1:{target_socket}:throughput",
        "--alloc",
        "scalable",
        "--priority",
        "abovenormal",
    ]
    runner_log = out_dir / f"{job['case_id']}_runner.log"

    if dry_run:
        return {
            "status": "DRY_RUN_WOULD_LAUNCH",
            "job_id": job["job_id"],
            "target_socket": target_socket,
            "cmd": cmd,
            "runner_log": str(runner_log),
            "skipped": skipped,
            "slots": slots,
            "health": health,
        }

    # Mark LAUNCHING before running pre-build/spawn to prevent concurrent duplicate triggers
    job["status"] = "LAUNCHING"
    job["numafirst"] = target_socket
    job["updated_at"] = datetime.now().isoformat(timespec="seconds")
    queue_doc["updated_at"] = job["updated_at"]
    queue_path.write_text(json.dumps(queue_doc, indent=2, ensure_ascii=False), encoding="utf-8")

    try:
        _run_pre_build_mesh(job, numafirst=target_socket, ini_file=ini_file, scratch=scratch)
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

        # Wait up to 30s to confirm child comsolbatch.exe spawned with matching output .mph
        target_mph = str(_target_mph_path(job).resolve()).lower()
        comsol_pid = None
        for _ in range(15):
            time.sleep(2.0)
            if proc.poll() is not None:
                tail = runner_log.read_text(encoding="utf-8", errors="replace")[-1000:]
                raise RuntimeError(f"Runner exited prematurely (rc={proc.returncode}):\n{tail}")
            for lp in list_live_comsol_processes():
                if lp.get("out_mph") and str(Path(lp["out_mph"]).resolve()).lower() == target_mph:
                    comsol_pid = lp["pid"]
                    break
            if comsol_pid is not None:
                break

        now_iso = datetime.now().isoformat(timespec="seconds")
        job["status"] = "RUNNING"
        job["runner_pid"] = proc.pid
        job["comsol_pid"] = comsol_pid
        job["launched_at"] = now_iso
        job["runner_log"] = str(runner_log)

        # Update running_now snapshot cleanly
        live_after = list_live_comsol_processes()
        live_pids = {p["pid"] for p in live_after}
        running_now = queue_doc.setdefault("running_now", {})
        for slot_k, slot_v in list(running_now.items()):
            if slot_v.get("pid") not in live_pids:
                running_now.pop(slot_k, None)
        slot_prefix = f"N{target_socket}"
        slot_name = f"{slot_prefix}-A" if f"{slot_prefix}-A" not in running_now else f"{slot_prefix}-B"
        running_now[slot_name] = {
            "numafirst": target_socket,
            "label": job["job_id"],
            "out_dir": str(out_dir),
            "case_id": job["case_id"],
            "pid": comsol_pid,
            "runner_pid": proc.pid,
        }
        queue_doc["updated_at"] = now_iso
        queue_path.write_text(json.dumps(queue_doc, indent=2, ensure_ascii=False), encoding="utf-8")

        return {
            "status": "LAUNCHED",
            "job_id": job["job_id"],
            "target_socket": target_socket,
            "runner_pid": proc.pid,
            "comsol_pid": comsol_pid,
            "runner_log": str(runner_log),
            "health": health,
        }
    except Exception as exc:
        job["status"] = "LAUNCH_FAILED"
        job["error"] = str(exc)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        queue_path.write_text(json.dumps(queue_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Swarm COMSOL backfill queue controller")
    p.add_argument("--queue", default=str(DEFAULT_QUEUE_PATH))
    p.add_argument("--check-slots", action="store_true")
    p.add_argument("--dispatch", action="store_true")
    p.add_argument("--numafirst", type=int, default=None, choices=[0, 1])
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    q_path = Path(args.queue)
    if args.check_slots or not args.dispatch:
        q_doc = json.loads(q_path.read_text(encoding="utf-8"))
        health = get_system_health("E:\\")
        live = list_live_comsol_processes()
        slots = summarize_slots(live, max_per_socket=int(q_doc.get("policy", {}).get("max_slots_per_numa_socket", 2)))
        nxt, skipped = select_next_job(q_doc, health=health, live_procs=live)
        print(
            json.dumps(
                {
                    "health": health,
                    "slots": slots,
                    "next_eligible_job": nxt["job_id"] if nxt else None,
                    "skipped": skipped,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    res = dispatch_one_job(
        queue_path=q_path,
        requested_numafirst=args.numafirst,
        dry_run=args.dry_run,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
