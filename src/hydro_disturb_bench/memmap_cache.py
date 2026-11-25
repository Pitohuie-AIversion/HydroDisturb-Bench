"""Zero-copy 4-channel Memmap & IEEE-754 BFloat16 cache builder for 3D CFD World Models.

Unpacks compressed (gzip-4) 6-channel golden HDF5 flow trajectories into contiguous,
OS page-cache-friendly ``np.memmap`` binary arrays with two major I/O reductions:
1. Extracts the static cylinder geometry (``sdf_star``, ``solid_mask``) once across all cases
   into a single shared 3.0 MiB ``geometry_static.npy`` file (-33.3% per-frame bandwidth).
2. Quantizes the 4 dynamic flow channels (``[u*, v_cross*, w_span*, p*]``) into IEEE-754
   round-to-nearest-even ``bfloat16`` (9.0 MiB/frame vs 27.0 MiB/frame raw 6ch float32).
"""

from __future__ import annotations

import datetime
import json
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np

try:
    import h5py

    HAS_H5PY = True
except ImportError:  # pragma: no cover - optional in data-free CI environments
    h5py = None  # type: ignore
    HAS_H5PY = False

try:
    import torch

    HAS_TORCH = True
except ImportError:  # pragma: no cover - optional on non-GPU simulation hosts
    torch = None  # type: ignore
    HAS_TORCH = False


STATIC_SHAPE = (2, 64, 96, 192)
DYN_SHAPE_SPATIAL = (4, 64, 96, 192)


def float32_to_bf16_u16(arr: np.ndarray) -> np.ndarray:
    """Convert float32 array to IEEE-754 bfloat16 uint16 bit-patterns (round-to-nearest-even)."""
    a = np.ascontiguousarray(arr, dtype=np.float32)
    if HAS_TORCH:
        # View as int16 before .numpy() so PyTorch < 2.3 and >= 2.3 behave identically.
        return torch.from_numpy(a).to(torch.bfloat16).view(torch.int16).numpy().view(np.uint16)

    u32 = a.view(np.uint32)
    nan_mask = (u32 & np.uint32(0x7FFFFFFF)) > np.uint32(0x7F800000)
    lsb = (u32 >> np.uint32(16)) & np.uint32(1)
    rounded = u32 + np.uint32(0x7FFF) + lsb
    bf16 = (rounded >> np.uint32(16)).astype(np.uint16)
    if np.any(nan_mask):
        bf16[nan_mask] = np.uint16(0x7FC0)
    return bf16


def bf16_u16_to_float32(arr_u16: np.ndarray) -> np.ndarray:
    """Decode uint16 bfloat16 bit-patterns back into float32."""
    u32 = np.ascontiguousarray(arr_u16, dtype=np.uint16).astype(np.uint32) << np.uint32(16)
    return u32.view(np.float32)


def _decode_attr(val: Any) -> Any:
    """Convert NumPy / HDF5 scalar and array attributes into JSON-serializable Python objects."""
    if isinstance(val, np.ndarray):
        return [_decode_attr(x) for x in val.tolist()]
    if isinstance(val, (np.floating, float)):
        return float(val)
    if isinstance(val, (np.integer, int)):
        return int(val)
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return val


def extract_shared_geometry(h5_files: Sequence[Path], out_dir: Path) -> dict[str, Any]:
    """Verify static [sdf_star, solid_mask] consistency across all H5s and save one shared copy."""
    if not HAS_H5PY:
        raise ImportError("h5py is required to extract shared geometry from HDF5 files.")

    geom_path = out_dir / "geometry_static.npy"
    coords_path = out_dir / "coordinates.npz"

    ref_geom: np.ndarray | None = None
    ref_coords: dict[str, np.ndarray] = {}

    for idx, h5_path in enumerate(h5_files):
        with h5py.File(str(h5_path), "r") as f:
            geom = np.ascontiguousarray(f["flow_fields"][0, 4:6], dtype=np.float32)
            if idx == 0:
                ref_geom = geom
                for k in f["coordinates"].keys():
                    ref_coords[k] = np.ascontiguousarray(f["coordinates"][k][:], dtype=np.float32)
            elif not np.array_equal(ref_geom, geom):
                raise ValueError(
                    f"Geometry mismatch detected in {h5_path.name}! Expected identical cylinder mask/SDF."
                )

    assert ref_geom is not None
    np.save(str(geom_path), ref_geom)
    np.savez(str(coords_path), **ref_coords)

    dx = float(np.mean(np.diff(ref_coords["x_star"])))
    dy = float(np.mean(np.diff(ref_coords["y_star"])))
    dz = float(np.mean(np.diff(ref_coords["z_star"])))
    return {
        "geometry_file": geom_path.name,
        "geometry_shape": list(STATIC_SHAPE),
        "geometry_dtype": "float32",
        "coordinates_file": coords_path.name,
        "grid_spacing_star": [dz, dy, dx],
    }


def convert_single_h5(
    h5_path_str: str | Path,
    out_dir_str: str | Path,
    dtype_mode: str = "bf16",
    chunk_frames: int = 32,
    verify: bool = False,
) -> dict[str, Any]:
    """Convert one 6-channel HDF5 trajectory into a 4-channel memmap file plus aux metadata."""
    if not HAS_H5PY:
        raise ImportError("h5py is required to convert HDF5 datasets.")
    if dtype_mode not in ("bf16", "fp32"):
        raise ValueError(f"Unsupported dtype_mode '{dtype_mode}'; expected 'bf16' or 'fp32'.")

    t0 = time.time()
    h5_path = Path(h5_path_str)
    out_dir = Path(out_dir_str)
    case_stem = h5_path.stem.replace("_wm_full_trajectory", "")

    np_dtype = np.uint16 if dtype_mode == "bf16" else np.float32
    dat_name = f"{case_stem}_dyn4ch_{dtype_mode}.dat"
    aux_name = f"{case_stem}_aux.npz"
    dat_path = out_dir / dat_name
    tmp_dat_path = out_dir / f".tmp_{dat_name}"
    aux_path = out_dir / aux_name

    with h5py.File(str(h5_path), "r") as f:
        flow_dset = f["flow_fields"]
        n_steps, n_ch, nz, ny, nx = flow_dset.shape
        if (n_ch, nz, ny, nx) != (6, 64, 96, 192):
            raise ValueError(f"Unexpected flow_fields shape {flow_dset.shape} in {h5_path.name}")

        t_phys = np.ascontiguousarray(f["time_physical_s"][:], dtype=np.float32)
        t_ctu = np.ascontiguousarray(f["time_nondim_ctu"][:], dtype=np.float32)
        step_ids = np.ascontiguousarray(f["step_indices"][:], dtype=np.int32)

        aux_payload: dict[str, np.ndarray] = {
            "flow_time_physical_s": t_phys,
            "flow_time_nondim_ctu": t_ctu,
            "step_indices": step_ids,
        }

        aero_attrs: dict[str, Any] = {}
        if "aerodynamics" in f:
            aero_grp = f["aerodynamics"]
            for k in aero_grp.keys():
                aux_payload[f"aero_{k}"] = np.ascontiguousarray(aero_grp[k][:], dtype=np.float32)
            aero_t = aero_grp["time_physical_s"][:]
            aux_payload["flow_aligned_cd"] = np.interp(
                t_phys, aero_t, aero_grp["cd"][:]
            ).astype(np.float32)
            aux_payload["flow_aligned_cl"] = np.interp(
                t_phys, aero_t, aero_grp["cl"][:]
            ).astype(np.float32)
            for k, v in aero_grp.attrs.items():
                aero_attrs[k] = _decode_attr(v)

        root_attrs = {k: _decode_attr(v) for k, v in f.attrs.items()}
        stat_ctu = root_attrs.get("stationary_vortex_shedding_ctu", [30.0, float(t_ctu[-1])])
        stationary_start_idx = int(np.searchsorted(t_ctu, float(stat_ctu[0]), side="left"))
        stationary_start_idx = min(max(stationary_start_idx, 0), n_steps - 1)

        if tmp_dat_path.exists():
            tmp_dat_path.unlink()

        mmap_out = np.memmap(
            str(tmp_dat_path),
            dtype=np_dtype,
            mode="w+",
            shape=(n_steps, *DYN_SHAPE_SPATIAL),
        )
        try:
            for start in range(0, n_steps, chunk_frames):
                end = min(start + chunk_frames, n_steps)
                block_dyn = np.ascontiguousarray(flow_dset[start:end, :4], dtype=np.float32)
                mmap_out[start:end] = (
                    float32_to_bf16_u16(block_dyn) if dtype_mode == "bf16" else block_dyn
                )
            mmap_out.flush()
        finally:
            del mmap_out

        np.savez(str(aux_path), **aux_payload)

        max_rel_err = 0.0
        if verify:
            mmap_chk = np.memmap(
                str(tmp_dat_path),
                dtype=np_dtype,
                mode="r",
                shape=(n_steps, *DYN_SHAPE_SPATIAL),
            )
            try:
                probe_indices = sorted({0, n_steps // 2, n_steps - 1})
                for pi in probe_indices:
                    ref = flow_dset[pi, :4].astype(np.float32)
                    got = bf16_u16_to_float32(mmap_chk[pi]) if dtype_mode == "bf16" else mmap_chk[pi]
                    denom = max(float(np.max(np.abs(ref))), 1e-6)
                    rel = float(np.max(np.abs(ref - got)) / denom)
                    max_rel_err = max(max_rel_err, rel)
                    tol = 1.5e-2 if dtype_mode == "bf16" else 1e-6
                    if max_rel_err > tol or not np.isfinite(got).all():
                        raise RuntimeError(
                            f"Verification failed on {case_stem}[{pi}]: "
                            f"max_rel_err={max_rel_err:.4e} > tol={tol:.4e}"
                        )
            finally:
                del mmap_chk

    if dat_path.exists():
        dat_path.unlink()
    tmp_dat_path.replace(dat_path)

    elapsed = time.time() - t0
    size_gib = dat_path.stat().st_size / (1024**3)
    return {
        "case": case_stem,
        "source_h5": h5_path.name,
        "dat_file": dat_name,
        "aux_file": aux_name,
        "shape": [n_steps, *DYN_SHAPE_SPATIAL],
        "dtype": dtype_mode,
        "storage_dtype": "uint16_bf16" if dtype_mode == "bf16" else "float32",
        "size_gib": round(size_gib, 4),
        "reynolds_number": float(root_attrs.get("reynolds_number", 0.0)),
        "u0_m_s": float(root_attrs.get("u0_m_s", 0.0)),
        "cylinder_diameter_m": float(root_attrs.get("cylinder_diameter_m", 0.3)),
        "stationary_start_idx": stationary_start_idx,
        "stationary_ctu": stat_ctu,
        "dt_star_median": float(np.median(np.diff(t_ctu))) if n_steps > 1 else 0.0,
        "aerodynamics_attrs": aero_attrs,
        "verify_max_rel_err": max_rel_err,
        "elapsed_sec": round(elapsed, 2),
    }


def convert_h5_archive_to_memmap(
    input_dir: Path | str,
    output_dir: Path | str | None = None,
    dtype_mode: str = "bf16",
    workers: int = 4,
    chunk_frames: int = 32,
    cases: Sequence[str] | None = None,
    verify: bool = True,
) -> dict[str, Any]:
    """Convert an archive of *_wm_full_trajectory.h5 files into a Memmap cache directory."""
    if not HAS_H5PY:
        raise ImportError("h5py is required to convert HDF5 archives.")

    in_dir = Path(input_dir).resolve()
    out_dir = (
        Path(output_dir).resolve()
        if output_dir is not None
        else in_dir.parent / f"{in_dir.name}_memmap_{dtype_mode}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    h5_files = sorted(in_dir.glob("*_wm_full_trajectory.h5"))
    if cases:
        filters = [x.strip().lower() for x in cases if x.strip()]
        h5_files = [p for p in h5_files if any(f in p.name.lower() for f in filters)]

    if not h5_files:
        raise FileNotFoundError(f"No *_wm_full_trajectory.h5 files found in {in_dir}")

    shared_geom_meta = extract_shared_geometry(h5_files, out_dir)

    entries: list[dict[str, Any]] = []
    n_workers = max(1, min(int(workers), len(h5_files)))
    if n_workers == 1:
        for p in h5_files:
            entries.append(convert_single_h5(p, out_dir, dtype_mode, chunk_frames, verify))
    else:
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            futs = {
                pool.submit(
                    convert_single_h5, str(p), str(out_dir), dtype_mode, chunk_frames, verify
                ): p
                for p in h5_files
            }
            for fut in as_completed(futs):
                entries.append(fut.result())

    entries.sort(key=lambda x: x["reynolds_number"])
    index_data: dict[str, Any] = {
        "format_version": "WorldModel-Memmap-v1.0",
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dtype": dtype_mode,
        "dynamic_channels": ["u_star", "v_cross_star", "w_span_star", "p_star"],
        "static_channels": ["sdf_star", "solid_mask"],
        "shared_geometry": shared_geom_meta,
        "total_cases": len(entries),
        "total_frames": sum(r["shape"][0] for r in entries),
        "total_size_gib": round(sum(r["size_gib"] for r in entries), 4),
        "cases": entries,
    }
    index_path = out_dir / "memmap_index.json"
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index_data, f, indent=2)
    return index_data


def benchmark_memmap_read(
    out_dir: Path | str,
    entries: Sequence[dict[str, Any]] | None = None,
    num_reads: int = 64,
) -> dict[str, Any]:
    """Measure random-slice read throughput directly from generated memmap files."""
    cache_dir = Path(out_dir).resolve()
    if entries is None:
        index_path = cache_dir / "memmap_index.json"
        with open(index_path, encoding="utf-8") as f:
            entries = json.load(f)["cases"]
    if not entries:
        raise ValueError("No entries available to benchmark.")

    e = entries[0]
    dat_path = cache_dir / e["dat_file"]
    dtype = np.uint16 if e["dtype"] == "bf16" else np.float32
    shape = tuple(e["shape"])
    mm = np.memmap(str(dat_path), dtype=dtype, mode="r", shape=shape)
    try:
        rng = np.random.default_rng(42)
        idxs = rng.integers(0, shape[0], size=num_reads)
        frame_mib = float(np.prod(shape[1:]) * np.dtype(dtype).itemsize / (1024**2))

        _ = np.array(mm[0], copy=True)
        _ = np.array(mm[-1], copy=True)

        t0 = time.perf_counter()
        for i in idxs:
            _ = np.array(mm[int(i)], copy=True)
        dt = max(time.perf_counter() - t0, 1e-9)
        total_mib = num_reads * frame_mib
        return {
            "dat_file": e["dat_file"],
            "num_reads": num_reads,
            "frame_mib": round(frame_mib, 2),
            "throughput_mib_s": round(total_mib / dt, 1),
            "ms_per_frame": round(dt / num_reads * 1000.0, 3),
        }
    finally:
        del mm
