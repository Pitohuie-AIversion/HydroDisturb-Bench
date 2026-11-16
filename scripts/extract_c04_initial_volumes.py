#!/usr/bin/env python3
"""
extract_c04_initial_volumes.py - Background extraction of initial-state 3D volumes (t=0 to 5s).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

REPO_ROOT = Path(r"D:\HydroDisturb-Bench")
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from hydro_disturb_bench.comsol_java.bridge import _run_bridge

OUT_DIR = Path(r"E:\cfd_3d\05_reports_viz\video_c04_shedding")
MODEL_PATH = Path(r"E:\cfd_3d\01_calibration\c04_re126k_mesh1\B_rtol1e3\B_rtol1e3.mph")

INITIAL_STEPS = list(range(1, 89, 4))  # Steps 1, 5, 9, ..., 85 (t=0.0 to 5.04s, 22 steps)


def main():
    missing = [s for s in INITIAL_STEPS if not (OUT_DIR / f"c04_vol_step_{s}.npz").is_file()]
    if not missing:
        print(f"[INITIAL] All {len(INITIAL_STEPS)} initial volume files already exist.")
        return

    print(f"[INITIAL] Exporting {len(missing)} initial steps: {missing}")
    temp_base = OUT_DIR / ".tmp_vol_initial.txt"
    snums_str = ",".join(str(s) for s in missing)

    t0 = time.time()
    args = [
        "export-c04-volume",
        str(MODEL_PATH),
        str(temp_base),
        "dset3",
        snums_str
    ]
    res = _run_bridge(args)
    print(f"[INITIAL] Bridge finished in {time.time()-t0:.1f}s: {res.get('status')}")

    for s in missing:
        step_txt = OUT_DIR / f"{temp_base.name}.step_{s}"
        if not step_txt.is_file():
            print(f"[INITIAL] Warning: Missing {step_txt.name}")
            continue

        t_val = (s - 1) * 0.06
        data = np.loadtxt(step_txt, comments="%", dtype=np.float32)
        x = data[:, 0].reshape((96, 64, 192))[0, 0, :]
        y_span = data[:, 1].reshape((96, 64, 192))[0, :, 0]
        z_cross = data[:, 2].reshape((96, 64, 192))[:, 0, 0]
        u = data[:, 3].reshape((96, 64, 192))
        v_span = data[:, 4].reshape((96, 64, 192))
        w_cross = data[:, 5].reshape((96, 64, 192))
        p = data[:, 6].reshape((96, 64, 192))

        npz_path = OUT_DIR / f"c04_vol_step_{s}.npz"
        np.savez_compressed(
            npz_path,
            x=x, y_span=y_span, z_cross=z_cross,
            u=u, v_span=v_span, w_cross=w_cross, p=p,
            time_s=t_val, step=s
        )
        step_txt.unlink(missing_ok=True)
        print(f"[INITIAL] Saved {npz_path.name} ({npz_path.stat().st_size/1024/1024:.2f} MB)")

    print("[INITIAL] Initial volume batch completed successfully.")


if __name__ == "__main__":
    main()
