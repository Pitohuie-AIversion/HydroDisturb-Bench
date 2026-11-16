import datetime
from pathlib import Path

cases = [
    {
        'name': 'full_C_from_F3_60',
        'slot': 'S0-Slot1 (Socket 0)',
        'pid': 27472,
        'start_iso': '2026-11-14T21:29:44',
        'start_sim_t': 60.0,
        'target_sim_t': 120.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\continuation_20261114\full_C_from_F3_60\results\C_rtol3e4_max005\comsolbatch.log'
    },
    {
        'name': 're40k_mesh1_pilot',
        'slot': 'S0-Slot2 (Socket 0)',
        'pid': 22680,
        'start_iso': '2026-11-15T16:36:27',
        'start_sim_t': 0.0,
        'target_sim_t': 45.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\results\re40k_mesh1_pilot\B2_rtol5e4_max005\comsolbatch.log'
    },
    {
        'name': 'mesh1_C_tight_validation',
        'slot': 'S1-Slot1 (Socket 1)',
        'pid': 26660,
        'start_iso': '2026-11-14T05:16:08',
        'start_sim_t': 0.0,
        'target_sim_t': 60.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\results\mesh1_C_tight_validation\C_rtol1e4_max005\comsolbatch.log'
    },
    {
        'name': 'full_nt01',
        'slot': 'S1-Slot2 (Socket 1)',
        'pid': 11148,
        'start_iso': '2026-11-14T05:16:20',
        'start_sim_t': 0.0,
        'target_sim_t': 60.0,
        'log': r'E:\codex_comsol_takeover\20261113\cpu_fill\reference_tuning\results\full_nt01\C_NL01_rtol3e4_max005\comsolbatch.log'
    }
]

now = datetime.datetime.now()

print("="*80)
print(f"COMSOL 4 SLOTS STATUS AT {now.strftime('%Y-%m-%d %H:%M:%S')}")
print("="*80)

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
        eta_str = "Initializing"

    print(f"[{c['slot']}] {c['name']} (PID {c['pid']})")
    print(f"  Progress: {step_num} steps | sim_t={sim_t:.4f}s / {c['target_sim_t']}s ({pct:.2f}%)")
    print(f"  Step size: dt={dt:.6f}s | Elapsed: {elapsed_h:.2f}h | Speed: {sim_speed:.3f} sim-s/h | ETA: {eta_str}")
    print("-" * 80)
