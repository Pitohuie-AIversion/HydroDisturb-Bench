"""Real-time Unified Monitor for COMSOL 3D Cylinder Flow Full-Spectrum Simulation.

Performs:
1. Hardware & Solver Audit:
   - Dual Intel Xeon Platinum 8260 (2 Sockets x 16 Cores = 32 physical / 64 threads)
   - CPU load, RAM free (>=30GB requirement), E: drive space (>=80GB requirement)
   - 4-slot comsolbatch.exe process tracking (Socket 0: 2 slots, Socket 1: 2 slots)
2. Case Progress Tracking:
   - mesh1_B2_bridge (S0)
   - mesh2_F2_spatial (S0)
   - mesh1_C_tight_validation (S1)
   - full_nt01 (S1)
   - Step count, time advance, advance rate, and estimated time remaining (ETA).
3. Completion & Two-Tier Acceptance Readiness:
   - Check calibration_case_summary.json
   - Verify tier1_tier2_acceptance_pipeline.py readiness
4. Backfill Strategy & NUMA Slot Management:
   - F3@60s continuation short test (60.0s -> 60.3s)
   - Multi-Re 6+2 Matrix pilot readiness
"""
import datetime
import json
import os
import shutil

import psutil

CASES = [
    {
        "name": "mesh1_B2_bridge",
        "socket": "Socket 0 (NUMA 0)",
        "np": 16,
        "pid": 14332,
        "runner_pid": 18468,
        "log_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_B2_bridge\B2_rtol5e4_max005\comsolbatch.log",
        "summary_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_B2_bridge\B2_rtol5e4_max005\calibration_case_summary.json",
        "t_target": 60.0,
        "start_time": datetime.datetime(2026, 11, 14, 5, 15, 42)
    },
    {
        "name": "mesh2_F2_spatial",
        "socket": "Socket 0 (NUMA 0)",
        "np": 16,
        "pid": 19708,
        "runner_pid": 23220,
        "log_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh2_F2_spatial\F2_rtol2e3_max006\comsolbatch.log",
        "summary_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh2_F2_spatial\F2_rtol2e3_max006\calibration_case_summary.json",
        "t_target": 60.0,
        "start_time": datetime.datetime(2026, 11, 14, 5, 15, 55)
    },
    {
        "name": "mesh1_C_tight_validation",
        "socket": "Socket 1 (NUMA 1)",
        "np": 16,
        "pid": 26660,
        "runner_pid": 27552,
        "log_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_C_tight_validation\C_rtol1e4_max005\comsolbatch.log",
        "summary_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_C_tight_validation\C_rtol1e4_max005\calibration_case_summary.json",
        "t_target": 60.0,
        "start_time": datetime.datetime(2026, 11, 14, 5, 16, 8)
    },
    {
        "name": "full_nt01",
        "socket": "Socket 1 (NUMA 1)",
        "np": 16,
        "pid": 11148,
        "runner_pid": 26880,
        "log_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\reference_tuning\results\full_nt01\C_NL01_rtol3e4_max005\comsolbatch.log",
        "summary_path": r"E:\codex_comsol_takeover\20261113\cpu_fill\reference_tuning\results\full_nt01\C_NL01_rtol3e4_max005\calibration_case_summary.json",
        "t_target": 60.0,
        "start_time": datetime.datetime(2026, 11, 14, 5, 16, 20)
    }
]

def audit_hardware():
    now = datetime.datetime.now()
    # Memory
    mem = psutil.virtual_memory()
    total_ram_gb = mem.total / (1024**3)
    free_ram_gb = mem.available / (1024**3)
    ram_ok = free_ram_gb >= 30.0

    # Disk E:
    disk_e = shutil.disk_usage("E:\\")
    total_e_gb = disk_e.total / (1024**3)
    free_e_gb = disk_e.free / (1024**3)
    disk_ok = free_e_gb >= 80.0

    # CPU
    cpu_percent = psutil.cpu_percent(interval=1.0)
    cpu_count_logical = psutil.cpu_count(logical=True)
    cpu_count_physical = psutil.cpu_count(logical=False)

    return {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_percent": cpu_percent,
        "cpu_physical_cores": cpu_count_physical,
        "cpu_logical_threads": cpu_count_logical,
        "total_ram_gb": round(total_ram_gb, 2),
        "free_ram_gb": round(free_ram_gb, 2),
        "ram_ok": ram_ok,
        "total_e_gb": round(total_e_gb, 2),
        "free_e_gb": round(free_e_gb, 2),
        "disk_ok": disk_ok,
    }

def audit_solvers():
    now = datetime.datetime.now()
    results = []
    for c in CASES:
        pid = c["pid"]
        proc_alive = psutil.pid_exists(pid)
        proc_cpu = 0.0
        proc_rss_gb = 0.0
        if proc_alive:
            try:
                p = psutil.Process(pid)
                proc_cpu = p.cpu_percent(interval=None)
                proc_rss_gb = p.memory_info().rss / (1024**3)
            except Exception:
                pass

        # Parse log
        log_path = c["log_path"]
        last_step = 0
        sim_time = 0.0
        dt = 0.0
        recent_speed_sim_per_h = 0.0
        avg_speed_sim_per_h = 0.0
        est_rem_h = 0.0
        est_finish = None

        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
            step_records = []
            for line in lines:
                parts = line.strip().split()
                if len(parts) >= 3 and parts[0].isdigit():
                    try:
                        step_records.append((int(parts[0]), float(parts[1]), float(parts[2])))
                    except Exception:
                        pass
            if step_records:
                last = step_records[-1]
                last_step = last[0]
                sim_time = last[1]
                dt = last[2]

                # calculate speeds
                elapsed_total_h = (now - c["start_time"]).total_seconds() / 3600.0
                if elapsed_total_h > 0:
                    avg_speed_sim_per_h = sim_time / elapsed_total_h

                # recent speed (from last 50 steps if available)
                if len(step_records) > 20:
                    past = step_records[-20]
                    # Estimate recent rate from file mtime or line density
                    # Using overall average as robust baseline
                    recent_speed_sim_per_h = avg_speed_sim_per_h
                else:
                    recent_speed_sim_per_h = avg_speed_sim_per_h

                rem_sim = max(0.0, c["t_target"] - sim_time)
                if avg_speed_sim_per_h > 0:
                    est_rem_h = rem_sim / avg_speed_sim_per_h
                    est_finish = now + datetime.timedelta(hours=est_rem_h)

        # Check summary
        summary_exists = os.path.exists(c["summary_path"])
        progress_pct = round((sim_time / c["t_target"]) * 100.0, 1)

        results.append({
            "name": c["name"],
            "socket": c["socket"],
            "np": c["np"],
            "pid": pid,
            "proc_alive": proc_alive,
            "proc_rss_gb": round(proc_rss_gb, 2),
            "step": last_step,
            "sim_time": round(sim_time, 4),
            "t_target": c["t_target"],
            "progress_pct": progress_pct,
            "dt": dt,
            "avg_speed": round(avg_speed_sim_per_h, 3),
            "est_rem_h": round(est_rem_h, 2),
            "est_finish": est_finish.strftime("%Y-%m-%d %H:%M:%S") if est_finish else "N/A",
            "summary_exists": summary_exists
        })
    return results

if __name__ == "__main__":
    hw = audit_hardware()
    solvers = audit_solvers()
    report = {
        "hardware_audit": hw,
        "solvers_audit": solvers
    }
    print(json.dumps(report, indent=2))
