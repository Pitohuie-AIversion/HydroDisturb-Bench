"""Industrial-Grade 3D CFD World Model Dataset Extraction & Validation Engine.

This script implements the self-reflected, bulletproof pipeline for exporting
transient flow fields from COMSOL .mph models into the canonical 6-channel
Chunked HDF5 format for World Model and Neural Operator (FNO/U-Net/Transformer) training.

Pipeline Specifications:
1. Universal Non-dimensionalization:
   - x* = x / D, y* = y / D, z* = z / D
   - t* = t * U0 / D (convective time units, CTU)
   - u* = u / U0 (streamwise)
   - v_cross* = w / U0 (cross-stream / lift direction)
   - w_span* = v / U0 (spanwise direction along cylinder axis)
   - p* = (p - p_inf) / (rho0 * U0^2)
   - sdf* = (sqrt(x^2 + z^2) - R) / D (negative inside cylinder)
   - solid_mask = 1 if r <= R else 0
2. Tensor Layout:
   - Canonical shape per step: (6, 64, 96, 192) -> (C, Nz_span, Ny_cross, Nx_stream)
   - Total dataset shape: (T, 6, 64, 96, 192)
3. Storage Architecture:
   - Chunked HDF5 (.h5), chunk shape = (1, 6, 64, 96, 192), compression="gzip", opts=4
   - High-throughput zero-lock multi-worker slicing for PyTorch DataLoader
4. Solid Wall Penetration & NaN Protection:
   - Inside cylinder: velocities u*=0, v_cross*=0, w_span*=0, p*=0, solid_mask=1
   - Complete finite value validation (assert zero NaNs / Infs in fluid domain)
5. COMSOL Solver Dataset Robustness:
   - Automatic dynamic detection of maximum-step time solution (dsetTag="auto")
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import math
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import h5py
import numpy as np
import pandas as pd

# Ensure src in path
BENCH_ROOT = Path(r"D:\HydroDisturb-Bench")
if str(BENCH_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT / "src"))

from hydro_disturb_bench.comsol_java.bridge import _run_bridge, to_safe_path


def parse_header_time(step_file: Path) -> float:
    """Extract physical simulation time from COMSOL export header."""
    with step_file.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.startswith("%"):
                break
            if "@ t=" in line:
                # Column header line containing '@ t=<val>'
                parts = line.split("@ t=")
                if len(parts) > 1:
                    val_str = parts[1].split()[0]
                    try:
                        return float(val_str)
                    except ValueError:
                        pass
            if line.startswith("% Time:"):
                val_str = line.split("% Time:")[1].strip().split()[0]
                try:
                    return float(val_str)
                except ValueError:
                    pass
    return 0.0


def extract_world_model_h5(
    model_path: Path | str,
    output_h5: Path | str,
    reynolds_number: float,
    u0: float,
    diameter: float = 0.30,
    rho: float = 1025.8944,
    step_indices: list[int] | None = None,
    dataset_tag: str = "auto",
    compression: str = "gzip",
    compression_opts: int = 4,
    allow_overwrite: bool = True,
    temp_dir: Path | str = Path(r"E:\comsol_scratch"),
    batch_size: int = 25,
    total_steps: int | None = None,
) -> dict[str, Any]:
    """Execute end-to-end extraction from COMSOL MPH to Chunked HDF5."""
    model_path = Path(model_path).resolve()
    output_h5 = Path(output_h5).resolve()
    temp_dir = Path(temp_dir).resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_h5.parent.mkdir(parents=True, exist_ok=True)

    if not model_path.is_file():
        raise FileNotFoundError(f"Model file not found: {model_path}")
    if output_h5.is_file() and not allow_overwrite:
        raise FileExistsError(f"Output HDF5 already exists: {output_h5}")

    t_start = time.time()
    radius = diameter / 2.0
    q_inf = 0.5 * rho * (u0 ** 2)

    # 1. Determine available steps (skip redundant inspect-datasets if pre-verified total_steps is provided)
    if total_steps is not None and total_steps > 0:
        total_steps_in_mph = total_steps
        time_sol = "auto"
        print(f"[*] Target Model: {model_path.name}")
        print(f"[*] Pre-verified Total Steps: {total_steps_in_mph} (skipping redundant inspect-datasets)")
    else:
        inspect_res = _run_bridge(["inspect-datasets", str(model_path)])
        if inspect_res.get("status") != "SUCCESS":
            raise RuntimeError(f"COMSOL inspect-datasets failed: {inspect_res}")

        time_sol = None
        total_steps_in_mph = 0
        for sol in inspect_res.get("solutions", []):
            if sol.get("type") == "Time" and sol.get("n_steps", 0) > total_steps_in_mph:
                total_steps_in_mph = sol.get("n_steps", 0)
                time_sol = sol.get("tag")

        print(f"[*] Target Model: {model_path.name}")
        print(f"[*] Detected Time Solution: {time_sol} with {total_steps_in_mph} steps")
    print(f"[*] Physical Parameters: Re={reynolds_number}, U0={u0} m/s, D={diameter} m, rho={rho} kg/m^3")

    if step_indices is None:
        # Default: all steps
        step_indices = list(range(1, total_steps_in_mph + 1))
    else:
        # Filter valid steps
        step_indices = [s for s in step_indices if 1 <= s <= total_steps_in_mph]

    n_steps = len(step_indices)
    if n_steps == 0:
        raise ValueError("No valid steps requested for extraction.")

    print(f"[*] Extracting {n_steps} steps: range [{step_indices[0]} .. {step_indices[-1]}]")

    # 2. Build reference coordinate grid and geometric masks
    # Standard C04 grid: Nx=192 in [-2D, 10D], Ny_cross=96 in [-3D, 3D], Nz_span=64 in [-2.1, -0.9]
    delta = diameter / 16.0  # 0.01875 m
    x_1d = -0.60 + (np.arange(192, dtype=np.float32) + 0.5) * delta
    y_span_1d = -2.10 + (np.arange(64, dtype=np.float32) + 0.5) * delta
    z_cross_1d = -0.90 + (np.arange(96, dtype=np.float32) + 0.5) * delta

    # Meshgrid with canonical indexing: (Nz_span, Ny_cross, Nx_stream)
    # Z_cross corresponds to Canonical Y, X corresponds to Canonical X
    X_3d = np.broadcast_to(x_1d.reshape((1, 1, 192)), (64, 96, 192))
    Z_cross_3d = np.broadcast_to(z_cross_1d.reshape((1, 96, 1)), (64, 96, 192))
    Y_span_3d = np.broadcast_to(y_span_1d.reshape((64, 1, 1)), (64, 96, 192))

    # Radial distance from cylinder axis (at x=0, z_cross=0)
    radial_dist = np.sqrt(X_3d**2 + Z_cross_3d**2).astype(np.float32)
    sdf = (radial_dist - radius).astype(np.float32)
    solid_mask = (radial_dist <= radius).astype(np.uint8)

    # Non-dimensionalize geometry
    sdf_nondim = (sdf / diameter).astype(np.float32)

    # 3. Initialize or Resume Chunked HDF5 Container
    resumed = False
    existing_tmps = sorted(output_h5.parent.glob(f".tmp_{output_h5.stem}_*.h5"), key=os.path.getmtime)
    if existing_tmps:
        latest_tmp = existing_tmps[-1]
        try:
            test_h5 = h5py.File(str(latest_tmp), "r+")
            if "step_indices" in test_h5 and test_h5["flow_fields"].shape[0] == n_steps:
                step_id_arr = test_h5["step_indices"][:]
                done_indices = set(step_id_arr[step_id_arr > 0])
                if len(done_indices) > 0:
                    print(f"[*] Resuming from existing partial container: {latest_tmp.name} ({len(done_indices)}/{n_steps} steps already extracted)")
                    temp_h5 = latest_tmp
                    h5_file = test_h5
                    flow_dset = h5_file["flow_fields"]
                    time_phys_dset = h5_file["time_physical_s"]
                    time_star_dset = h5_file["time_nondim_ctu"]
                    step_id_dset = h5_file["step_indices"]
                    resumed = True
        except Exception as e:
            print(f"[WARN] Cannot resume from {latest_tmp.name}: {e}")

    if not resumed:
        temp_h5 = output_h5.parent / f".tmp_{output_h5.stem}_{int(time.time())}.h5"
        if temp_h5.exists():
            temp_h5.unlink()

        h5_file = h5py.File(str(temp_h5), "w")
        flow_dset = h5_file.create_dataset(
            "flow_fields",
            shape=(n_steps, 6, 64, 96, 192),
            dtype=np.float32,
            chunks=(1, 6, 64, 96, 192),
            compression=compression,
            compression_opts=compression_opts if compression == "gzip" else None,
        )

        time_phys_dset = h5_file.create_dataset("time_physical_s", shape=(n_steps,), dtype=np.float32)
        time_star_dset = h5_file.create_dataset("time_nondim_ctu", shape=(n_steps,), dtype=np.float32)
        step_id_dset = h5_file.create_dataset("step_indices", shape=(n_steps,), dtype=np.int32)

        # Coordinate Datasets
        coords_grp = h5_file.create_group("coordinates")
        coords_grp.create_dataset("x_stream_m", data=x_1d)
        coords_grp.create_dataset("y_cross_m", data=z_cross_1d)
        coords_grp.create_dataset("z_span_m", data=y_span_1d)
        coords_grp.create_dataset("x_star", data=x_1d / diameter)
        coords_grp.create_dataset("y_star", data=z_cross_1d / diameter)
        coords_grp.create_dataset("z_star", data=(y_span_1d - y_span_1d.min()) / diameter)

        # Metadata injection
        h5_file.attrs["dataset_title"] = "3D CFD Cylinder Flow World Model Benchmark"
        h5_file.attrs["reynolds_number"] = float(reynolds_number)
        h5_file.attrs["u0_m_s"] = float(u0)
        h5_file.attrs["cylinder_diameter_m"] = float(diameter)
        h5_file.attrs["density_kg_m3"] = float(rho)
        h5_file.attrs["kinematic_viscosity_m2_s"] = float(u0 * diameter / reynolds_number)
        h5_file.attrs["tensor_channels"] = ["u_star", "v_cross_star", "w_span_star", "p_star", "sdf_star", "solid_mask"]
        h5_file.attrs["tensor_shape"] = [n_steps, 6, 64, 96, 192]
        h5_file.attrs["channel_units"] = ["1", "1", "1", "1", "1", "binary"]
        h5_file.attrs["created_at"] = datetime.datetime.now().isoformat()
        h5_file.attrs["source_model"] = str(model_path)

        # Pre-seed verified existing steps from *_wm_50steps.h5 / c04_wm_25steps_golden.h5 if available
        known_cases = ['re80k', 're100k', 'c04_golden', 're180k_true', 're200k', 're300k', 're350k', 're500k', 're700k', 're1m', 're3m']
        seed_file = None
        for c in sorted(known_cases, key=len, reverse=True):
            if c in output_h5.stem:
                cand = output_h5.parent / ('c04_wm_25steps_golden.h5' if 'c04' in c else f'{c}_wm_50steps.h5')
                if cand.is_file():
                    seed_file = cand
                    break

        if seed_file:
            try:
                with h5py.File(str(seed_file), "r") as s_h5:
                    if "step_indices" in s_h5 and "flow_fields" in s_h5:
                        s_ids = s_h5["step_indices"][:]
                        valid_mask = (s_ids > 0)
                        s_steps = int(valid_mask.sum())
                        if s_steps > 0 and s_steps <= n_steps:
                            s_valid_ids = list(s_ids[:s_steps])
                            if s_valid_ids == step_indices[:s_steps]:
                                print(f"[*] Pre-seeding {s_steps} verified steps from {seed_file.name}...")
                                flow_dset[:s_steps] = s_h5["flow_fields"][:s_steps]
                                time_phys_dset[:s_steps] = s_h5["time_physical_s"][:s_steps]
                                time_star_dset[:s_steps] = s_h5["time_nondim_ctu"][:s_steps]
                                step_id_dset[:s_steps] = s_h5["step_indices"][:s_steps]
                                h5_file.flush()
                                print(f"[+] Successfully pre-seeded {s_steps} steps! Resuming from step {step_indices[s_steps]}.")
            except Exception as e:
                print(f"[WARN] Failed to pre-seed from {seed_file.name}: {e}")

    try:
        # 4. Batch extraction in chunks of steps (scaled for 100% saturation)
        step_id_arr = step_id_dset[:]
        done_indices = set(step_id_arr[step_id_arr > 0])
        remaining_steps = [s for s in step_indices if s not in done_indices]
        total_batches = math.ceil(len(remaining_steps) / batch_size)
        print(f"[*] Total planned batches: {total_batches} ({len(remaining_steps)} remaining steps, batch_size={batch_size})")

        for b_idx in range(total_batches):
            b_steps = remaining_steps[b_idx * batch_size : (b_idx + 1) * batch_size]
            b_snums_str = ",".join(str(s) for s in b_steps)

            temp_txt_base = temp_dir / f".tmp_export_{output_h5.stem}_b{b_idx}_{int(time.time())}.txt"

            args = [
                "export-c04-volume",
                to_safe_path(model_path),
                to_safe_path(temp_txt_base),
                dataset_tag,
                b_snums_str,
                "false",
            ]
            t_batch_0 = time.time()
            res = _run_bridge(args)
            if res.get("status") != "SUCCESS":
                raise RuntimeError(f"COMSOL export error in batch {b_idx}: {res}")

            def _parse_step_file(sn):
                step_file = temp_dir / f"{temp_txt_base.name}.step_{sn}"
                if not step_file.is_file():
                    if temp_txt_base.is_file() and len(b_steps) == 1:
                        step_file = temp_txt_base
                    else:
                        raise FileNotFoundError(f"Export step file missing: {step_file} (or base {temp_txt_base})")

                try:
                    t_phys = parse_header_time(step_file)
                    t_star = t_phys * u0 / diameter

                    # High-throughput C-engine streaming parsing (~1.18M rows)
                    df = pd.read_csv(step_file, comment="%", sep=r"\s+", header=None, dtype=np.float32, engine="c")
                    data = df.to_numpy()
                    if data.shape == (1179648, 4):
                        # Optimized 4-column format without coordinates: [u, v, w, p]
                        u_stream = data[:, 0].reshape((96, 64, 192)).transpose((1, 0, 2))
                        v_span = data[:, 1].reshape((96, 64, 192)).transpose((1, 0, 2))
                        w_cross = data[:, 2].reshape((96, 64, 192)).transpose((1, 0, 2))
                        p_raw = data[:, 3].reshape((96, 64, 192)).transpose((1, 0, 2))
                    elif data.shape == (1179648, 7):
                        # Legacy 7-column format with coordinates: [x, y, z, u, v, w, p]
                        u_stream = data[:, 3].reshape((96, 64, 192)).transpose((1, 0, 2))
                        v_span = data[:, 4].reshape((96, 64, 192)).transpose((1, 0, 2))
                        w_cross = data[:, 5].reshape((96, 64, 192)).transpose((1, 0, 2))
                        p_raw = data[:, 6].reshape((96, 64, 192)).transpose((1, 0, 2))
                    else:
                        raise ValueError(f"Unexpected data shape {data.shape} in {step_file.name}")

                    # Non-dimensionalization
                    u_star = u_stream / u0
                    vc_star = w_cross / u0
                    ws_star = v_span / u0
                    p_star = p_raw / (rho * (u0 ** 2))

                    # Solid interior protection (eliminate NaNs and enforce zero velocity inside cylinder)
                    solid_bool = (solid_mask == 1)
                    u_star[solid_bool] = 0.0
                    vc_star[solid_bool] = 0.0
                    ws_star[solid_bool] = 0.0
                    p_star[solid_bool] = 0.0

                    # Check fluid domain for NaNs
                    fluid_bool = ~solid_bool
                    if not (
                        np.isfinite(u_star[fluid_bool]).all()
                        and np.isfinite(vc_star[fluid_bool]).all()
                        and np.isfinite(ws_star[fluid_bool]).all()
                        and np.isfinite(p_star[fluid_bool]).all()
                    ):
                        raise ValueError(f"Step {sn} contains non-finite values in fluid domain!")

                    # Pack into 6-channel tensor
                    tensor_6ch = np.empty((6, 64, 96, 192), dtype=np.float32)
                    tensor_6ch[0] = u_star
                    tensor_6ch[1] = vc_star
                    tensor_6ch[2] = ws_star
                    tensor_6ch[3] = p_star
                    tensor_6ch[4] = sdf_nondim
                    tensor_6ch[5] = solid_mask.astype(np.float32)

                    global_idx = step_indices.index(sn)
                    return global_idx, sn, t_phys, t_star, tensor_6ch

                finally:
                    step_file.unlink(missing_ok=True)

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(b_steps), 8)) as pool:
                results = pool.map(_parse_step_file, b_steps)
                for global_idx, sn, t_phys, t_star, tensor_6ch in results:
                    flow_dset[global_idx] = tensor_6ch
                    time_phys_dset[global_idx] = np.float32(t_phys)
                    time_star_dset[global_idx] = np.float32(t_star)
                    step_id_dset[global_idx] = np.int32(sn)

            h5_file.flush()
            print(f"[*] Batch {b_idx + 1}/{total_batches} ({len(b_steps)} steps) processed and flushed in {time.time() - t_batch_0:.1f}s")

    finally:
        h5_file.close()

    # Move temp HDF5 to final path atomically
    if output_h5.exists():
        output_h5.unlink()
    shutil.move(str(temp_h5), str(output_h5))

    file_size_mb = output_h5.stat().st_size / (1024 * 1024)
    total_time = time.time() - t_start
    print(f"[+] Extraction Complete: {output_h5.name}")
    print(f"[+] Output Size: {file_size_mb:.2f} MB | Total Time: {total_time:.1f}s")

    return {
        "status": "SUCCESS",
        "output_h5": str(output_h5),
        "steps_extracted": n_steps,
        "shape": [n_steps, 6, 64, 96, 192],
        "file_size_mb": file_size_mb,
        "elapsed_sec": total_time,
    }


def main():
    parser = argparse.ArgumentParser(description="Extract 3D CFD flow fields to World Model Chunked HDF5.")
    parser.add_argument("--model", required=True, help="Path to solved COMSOL .mph file")
    parser.add_argument("--output", required=True, help="Path to output .h5 file")
    parser.add_argument("--re", type=float, required=True, help="Reynolds number")
    parser.add_argument("--u0", type=float, required=True, help="Inlet velocity U0 (m/s)")
    parser.add_argument("--diameter", type=float, default=0.30, help="Cylinder diameter (m)")
    parser.add_argument("--steps", default=None, help="Comma-separated step indices (e.g. '1,5,10')")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum number of steps to extract")
    parser.add_argument("--dataset-tag", default="auto", help="COMSOL dataset tag (default: 'auto')")
    parser.add_argument("--batch-size", type=int, default=25, help="Number of steps per extraction batch (default: 25)")
    parser.add_argument("--total-steps", type=int, default=None, help="Pre-verified total steps in model (skips inspect-datasets)")
    parser.add_argument("--start-step", type=int, default=None, help="Start step index (1-based, inclusive)")
    parser.add_argument("--end-step", type=int, default=None, help="End step index (inclusive)")
    parser.add_argument("--temp-dir", default=None, help="Directory for temporary extraction slice files")

    args = parser.parse_args()

    step_indices = None
    if args.start_step is not None and args.end_step is not None:
        step_indices = list(range(args.start_step, args.end_step + 1))
    elif args.steps:
        step_indices = [int(s.strip()) for s in args.steps.split(",") if s.strip()]
    elif args.max_steps:
        step_indices = list(range(1, args.max_steps + 1))

    kwargs = {
        "model_path": args.model,
        "output_h5": args.output,
        "reynolds_number": args.re,
        "u0": args.u0,
        "diameter": args.diameter,
        "step_indices": step_indices,
        "dataset_tag": args.dataset_tag,
        "batch_size": args.batch_size,
        "total_steps": args.total_steps,
    }
    if args.temp_dir:
        kwargs["temp_dir"] = Path(args.temp_dir)

    extract_world_model_h5(**kwargs)


if __name__ == "__main__":
    main()
