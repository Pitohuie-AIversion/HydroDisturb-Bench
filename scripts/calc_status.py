import datetime
import shutil
from pathlib import Path

import psutil

now = datetime.datetime.now()

# 1. Telemetry
mem = psutil.virtual_memory()
disk = shutil.disk_usage("E:\\")
per_cpu = psutil.cpu_percent(interval=0.5, percpu=True)
s0_load = sum(per_cpu[:32]) / 32
s1_load = sum(per_cpu[32:]) / 32

print("=" * 80)
print(f"COMSOL 4 SLOTS STATUS & HARDWARE AUDIT AT {now.strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 80)
print("CPU Topology : Dual Intel Xeon Platinum 8260 (2 Sockets, 32 Cores, 64 Threads)")
print(f"CPU Load     : Socket 0 (NUMA 0) = {s0_load:.1f}% | Socket 1 (NUMA 1) = {s1_load:.1f}% | Total = {sum(per_cpu)/len(per_cpu):.1f}%")
print(f"RAM Margin   : Total={mem.total/1024**3:.2f} GB, Free={mem.available/1024**3:.2f} GB (Gate >= 30 GB: {'PASS' if mem.available/1024**3 >= 30.0 else 'WARN'})")
print(f"Disk E: Free : Total={disk.total/1024**3:.2f} GB, Free={disk.free/1024**3:.2f} GB (Gate >= 80 GB: {'PASS' if disk.free/1024**3 >= 80.0 else 'WARN'})")
print("-" * 80)

cases = [
    {
        'slot': 'S0-Slot 1 (Socket 0, NUMA 0)',
        'name': 're3m_mesh1_pilot',
        'pid': 8520,
        'start_iso': '2026-11-16T13:22:46',
        'start_sim_t': 0.0,
        'target_sim_t': 10.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\results\re3m_mesh1_pilot\B2_rtol5e4_max005\comsolbatch.log'
    },
    {
        'slot': 'S0-Slot 2 (Socket 0, NUMA 0)',
        'name': 're200k_mesh1_pilot',
        'pid': 20080,
        'start_iso': '2026-11-17T11:24:08',
        'start_sim_t': 0.0,
        'target_sim_t': 27.7,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\results\re200k_mesh1_pilot\B2_rtol5e4_max005\comsolbatch.log'
    },
    {
        'slot': 'S1-Slot 1 (Socket 1, NUMA 1)',
        'name': 'mesh1_C_tight_validation',
        'pid': 26660,
        'start_iso': '2026-11-14T05:16:08',
        'start_sim_t': 0.0,
        'target_sim_t': 60.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_C_tight_validation\C_rtol1e4_max005\comsolbatch.log'
    },
    {
        'slot': 'S1-Slot 2 (Socket 1, NUMA 1)',
        'name': 'full_nt01',
        'pid': 11148,
        'start_iso': '2026-11-14T05:16:20',
        'start_sim_t': 0.0,
        'target_sim_t': 60.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\reference_tuning\results\full_nt01\C_NL01_rtol3e4_max005\comsolbatch.log'
    }
]

for c in cases:
    p = Path(c['log'])
    steps = []
    if p.exists():
        for line_str in p.read_text(encoding='utf-8', errors='ignore').splitlines():
            parts = line_str.strip().split()
            if len(parts) >= 3 and parts[0].isdigit():
                try:
                    steps.append((int(parts[0]), float(parts[1]), float(parts[2])))
                except ValueError:
                    pass
    start_dt = datetime.datetime.fromisoformat(c['start_iso'])
    elapsed_h = (now - start_dt).total_seconds() / 3600.0
    if steps:
        last = steps[-1]
        step_num = last[0]
        sim_t = last[1]
        dt = last[2]
        done_span = max(0.0, sim_t - c['start_sim_t'])
        total_span = c['target_sim_t'] - c['start_sim_t']
        pct = (done_span / total_span) * 100.0
        rem_sim_t = max(0.0, c['target_sim_t'] - sim_t)
        sim_speed = (done_span / elapsed_h) if elapsed_h > 0 else 0.0
        if sim_speed > 0:
            rem_wall_h = rem_sim_t / sim_speed
            eta = now + datetime.timedelta(hours=rem_wall_h)
            eta_str = f"{rem_wall_h:.2f} h (~{eta.strftime('%Y-%m-%d %H:%M')})"
        else:
            eta_str = "N/A"
    else:
        step_num = 0
        sim_t = 0.0
        dt = 0.0
        pct = 0.0
        sim_speed = 0.0
        rem_sim_t = c['target_sim_t']
        eta_str = "Initializing"

    alive = psutil.pid_exists(c['pid'])
    rss_gb = 0.0
    if alive:
        try:
            rss_gb = psutil.Process(c['pid']).memory_info().rss / (1024**3)
        except Exception:
            pass

    print(f"[{c['slot']}] Case: {c['name']} (PID: {c['pid']}, Alive: {alive}, RSS: {rss_gb:.2f} GB)")
    print(f"  Step Count : {step_num} | Time Advanced: {sim_t:.4f} s / {c['target_sim_t']:.1f} s ({pct:.2f}%)")
    print(f"  Current dt : {dt:.6f} s | Advancement Rate: {sim_speed:.3f} sim-s/wall-h")
    print(f"  Elapsed    : {elapsed_h:.2f} h | Remaining Physical: {rem_sim_t:.4f} s | ETA: {eta_str}")
    print("-" * 80)
