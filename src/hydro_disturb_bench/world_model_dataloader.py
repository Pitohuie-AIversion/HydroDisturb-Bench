"""Production PyTorch Zero-Copy World Model DataLoader for 3D CFD Flow Fields.

Supports two interchangeable high-throughput backends:
1. ``mode="ram_pool"``: Direct zero-copy attach to the resident Shared Memory RAM
   pool (``cfd_ram_pool_v1``) via ``multiprocessing.shared_memory`` (Python 3.8+)
   or Windows named ``mmap`` (Python 3.7+ compatibility). Eliminates disk I/O
   and yields microsecond-level tensor access for FNO / U-Net / Transformer training.
2. ``mode="hdf5"``: Lazy per-worker chunked HDF5 streaming from
   ``E:\\cfd_3d\\03_world_model_dataset\\*.h5`` or a unified ``manifest.json``.

Sample contract:
    Each ``__getitem__(idx)`` returns a 3-tuple ``(x_t, x_next, cond)``:
    - ``x_t``:    float32 tensor of shape ``(C, 64, 96, 192)`` (native DHW) or
                  ``(C, 192, 96, 64)`` (``spatial_order="whd"``), where ``C`` is
                  6 (``[u*, v*, w*, p*, sdf*, solid_mask]``) or 4 (``[u*, v*, w*, p*]``).
    - ``x_next``: float32 tensor of identical shape at ``t + step_stride``.
    - ``cond``:   float32 tensor of shape ``(3,)`` = ``[log10(Re), U0, D]``.
"""

from __future__ import annotations

import argparse
import json
import math
import mmap
import struct
import time
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
    from torch.utils.data import DataLoader, Dataset
    HAS_TORCH = True
except ImportError:  # pragma: no cover - allows non-torch inspection environments
    torch = None  # type: ignore
    DataLoader = None  # type: ignore
    Dataset = object  # type: ignore
    HAS_TORCH = False

try:
    import h5py
    HAS_H5PY = True
except ImportError:  # pragma: no cover - e.g. Pytorch-gpu env without h5py
    h5py = None  # type: ignore
    HAS_H5PY = False

try:
    from multiprocessing import shared_memory as _mp_shm
    HAS_SHARED_MEMORY = True
except ImportError:  # pragma: no cover - Python 3.7 fallback via Windows named mmap
    _mp_shm = None  # type: ignore
    HAS_SHARED_MEMORY = False


DEFAULT_MANIFEST_PATH = Path(r"E:\cfd_3d\03_world_model_dataset\manifest.json")
DEFAULT_POOL_NAME = "cfd_ram_pool_v1"
POOL_MAGIC = b"WM48"
POOL_CHANNELS = 6
POOL_SHAPE_DHW = (64, 96, 192)
BYTES_PER_STEP = POOL_CHANNELS * 64 * 96 * 192 * 4  # 28,311,552 bytes (~27.0 MiB)
META_TABLE_SIZE = 16 * 1024 * 1024                  # 16 MiB header table


def build_condition_vector(re_val: float, u0_val: float, d_val: float) -> np.ndarray:
    """Return physical conditioning vector c = [log10(Re), U0, D] as float32[3]."""
    return np.array(
        [math.log10(max(float(re_val), 1.0)), float(u0_val), float(d_val)],
        dtype=np.float32,
    )


class _SharedPoolHandle:
    """Cross-version (Python 3.7–3.12) zero-copy handle to WorldModelRAMPool.

    On Windows, attaches via ``mmap.mmap(..., access=mmap.ACCESS_READ)`` mapped
    strictly to the occupied step range (``META_TABLE_SIZE + current_steps *
    BYTES_PER_STEP``), avoiding 40 GB pagefile commit pressure (WinError 1455)
    while preserving true zero-copy pointer sharing into PyTorch tensors.
    """

    def __init__(self, pool_name: str = DEFAULT_POOL_NAME, read_only: bool = True) -> None:
        self.pool_name = pool_name
        self.read_only = read_only
        self._shm = None
        self._mmap = None

        # Prefer lightweight read-only named mmap on Windows (works Python 3.7–3.12, zero commit overhead)
        attached = False
        if read_only:
            try:
                hdr_mm = mmap.mmap(-1, 12, tagname=pool_name, access=mmap.ACCESS_READ)
                try:
                    magic, capacity, current = struct.unpack("<4sII", hdr_mm[:12])
                finally:
                    hdr_mm.close()
                if magic != POOL_MAGIC:
                    raise ValueError(f"Invalid RAM pool magic {magic!r} in '{pool_name}'")
                self.capacity_steps = int(capacity)
                self.current_steps = int(current)
                mapped_steps = max(1, min(self.current_steps, self.capacity_steps))
                map_bytes = META_TABLE_SIZE + mapped_steps * BYTES_PER_STEP
                self._mmap = mmap.mmap(-1, map_bytes, tagname=pool_name, access=mmap.ACCESS_READ)
                self.array = np.ndarray(
                    (mapped_steps, POOL_CHANNELS, *POOL_SHAPE_DHW),
                    dtype=np.float32,
                    buffer=self._mmap,
                    offset=META_TABLE_SIZE,
                )
                attached = True
            except OSError:
                attached = False

        if not attached:
            if HAS_SHARED_MEMORY:
                self._shm = _mp_shm.SharedMemory(name=pool_name, create=False)
                header_bytes = bytes(self._shm.buf[:12])
                magic, capacity, current = struct.unpack("<4sII", header_bytes)
                if magic != POOL_MAGIC:
                    self._shm.close()
                    raise ValueError(f"Invalid RAM pool magic {magic!r} in '{pool_name}'")
                self.capacity_steps = int(capacity)
                self.current_steps = int(current)
                self.array = np.ndarray(
                    (self.capacity_steps, POOL_CHANNELS, *POOL_SHAPE_DHW),
                    dtype=np.float32,
                    buffer=self._shm.buf,
                    offset=META_TABLE_SIZE,
                )
            else:
                hdr_mm = mmap.mmap(-1, 12, tagname=pool_name, access=mmap.ACCESS_READ)
                try:
                    magic, capacity, current = struct.unpack("<4sII", hdr_mm[:12])
                finally:
                    hdr_mm.close()
                if magic != POOL_MAGIC:
                    raise ValueError(f"Invalid RAM pool magic {magic!r} in '{pool_name}'")
                self.capacity_steps = int(capacity)
                self.current_steps = int(current)
                mapped_steps = max(1, min(self.current_steps, self.capacity_steps))
                map_bytes = META_TABLE_SIZE + mapped_steps * BYTES_PER_STEP
                self._mmap = mmap.mmap(-1, map_bytes, tagname=pool_name, access=mmap.ACCESS_WRITE)
                self.array = np.ndarray(
                    (mapped_steps, POOL_CHANNELS, *POOL_SHAPE_DHW),
                    dtype=np.float32,
                    buffer=self._mmap,
                    offset=META_TABLE_SIZE,
                )

    def refresh_current_steps(self) -> int:
        buf = self._shm.buf if self._shm is not None else self._mmap
        if buf is None:
            return self.current_steps
        magic, _, current = struct.unpack("<4sII", bytes(buf[:12]))
        if magic == POOL_MAGIC:
            self.current_steps = int(current)
        return self.current_steps

    def close(self) -> None:
        self.array = None  # type: ignore
        if self._shm is not None:
            try:
                self._shm.close()
            except Exception:
                pass
            self._shm = None
        if self._mmap is not None:
            try:
                self._mmap.close()
            except Exception:
                pass
            self._mmap = None


class WorldModelFlowDataset(Dataset):
    """PyTorch Dataset for 3D multi-Re cylinder flow World Model training.

    Returns ``(x_t, x_next, cond)`` with ``cond = [log10(Re), U0, D]``.
    """

    def __init__(
        self,
        mode: str = "ram_pool",
        manifest_path: str | Path | None = DEFAULT_MANIFEST_PATH,
        h5_paths: Sequence[str | Path] | None = None,
        pool_name: str = DEFAULT_POOL_NAME,
        pool_segments: Sequence[dict[str, Any]] | None = None,
        channels: int = 6,
        spatial_order: str = "dhw",
        step_stride: int = 1,
        read_only_pool: bool = True,
        return_torch: bool | None = None,
        default_re: float = 126167.0,
        default_u0: float = 0.5,
        default_d: float = 0.3,
    ) -> None:
        if mode not in ("ram_pool", "hdf5"):
            raise ValueError(f"Unsupported mode '{mode}'; expected 'ram_pool' or 'hdf5'")
        if channels not in (4, 6):
            raise ValueError(f"channels must be 4 or 6, got {channels}")
        if spatial_order not in ("dhw", "whd"):
            raise ValueError(f"spatial_order must be 'dhw' or 'whd', got '{spatial_order}'")
        if step_stride < 1:
            raise ValueError(f"step_stride must be >= 1, got {step_stride}")

        self.mode = mode
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.pool_name = pool_name
        self.channels = int(channels)
        self.spatial_order = spatial_order
        self.step_stride = int(step_stride)
        self.read_only_pool = bool(read_only_pool)
        self.return_torch = HAS_TORCH if return_torch is None else bool(return_torch)
        if self.return_torch and not HAS_TORCH:
            raise ImportError("PyTorch is not installed in this Python interpreter.")

        self._pool_handle: _SharedPoolHandle | None = None
        self._h5_handles: dict[str, Any] = {}
        self._samples: list[dict[str, Any]] = []
        self._trajectories: list[dict[str, Any]] = []

        manifest_data: dict[str, Any] | None = None
        if self.manifest_path is not None and self.manifest_path.is_file():
            manifest_data = json.loads(self.manifest_path.read_text(encoding="utf-8-sig"))

        if self.mode == "ram_pool":
            self._init_ram_pool(
                manifest_data=manifest_data,
                pool_segments=pool_segments,
                default_re=default_re,
                default_u0=default_u0,
                default_d=default_d,
            )
        else:
            self._init_hdf5(manifest_data=manifest_data, h5_paths=h5_paths)

    def _init_ram_pool(
        self,
        manifest_data: dict[str, Any] | None,
        pool_segments: Sequence[dict[str, Any]] | None,
        default_re: float,
        default_u0: float,
        default_d: float,
    ) -> None:
        self._pool_handle = _SharedPoolHandle(self.pool_name, read_only=self.read_only_pool)
        cur_steps = self._pool_handle.current_steps
        if cur_steps < 1 + self.step_stride:
            raise RuntimeError(
                f"RAM pool '{self.pool_name}' has only {cur_steps} loaded steps "
                f"(need >= {1 + self.step_stride})."
            )

        segments: list[dict[str, Any]] = []
        if pool_segments is not None:
            segments = [dict(s) for s in pool_segments]
        elif manifest_data and manifest_data.get("ram_pool", {}).get("segments"):
            segments = [dict(s) for s in manifest_data["ram_pool"]["segments"]]
        else:
            segments = [
                {
                    "case_id": f"{self.pool_name}_all",
                    "slot_start": 0,
                    "num_steps": cur_steps,
                    "reynolds_number": default_re,
                    "u0_m_s": default_u0,
                    "cylinder_diameter_m": default_d,
                }
            ]

        for seg in segments:
            start = int(seg["slot_start"])
            count = int(seg["num_steps"])
            end = min(start + count, cur_steps)
            effective_count = end - start
            if effective_count <= self.step_stride:
                continue
            re_val = float(seg["reynolds_number"])
            u0_val = float(seg["u0_m_s"])
            d_val = float(seg.get("cylinder_diameter_m", 0.3))
            cond = build_condition_vector(re_val, u0_val, d_val)
            case_id = str(seg.get("case_id", f"seg_{start}"))
            self._trajectories.append(
                {
                    "case_id": case_id,
                    "slot_start": start,
                    "num_steps": effective_count,
                    "reynolds_number": re_val,
                    "u0_m_s": u0_val,
                    "cylinder_diameter_m": d_val,
                }
            )
            for local_t in range(0, effective_count - self.step_stride):
                self._samples.append(
                    {
                        "case_id": case_id,
                        "idx_t": start + local_t,
                        "idx_next": start + local_t + self.step_stride,
                        "cond": cond,
                    }
                )

        if not self._samples:
            raise RuntimeError(
                f"No valid (t, t+{self.step_stride}) pairs constructed from RAM pool '{self.pool_name}'."
            )

    def _init_hdf5(
        self,
        manifest_data: dict[str, Any] | None,
        h5_paths: Sequence[str | Path] | None,
    ) -> None:
        if not HAS_H5PY:
            raise ImportError("h5py is required for mode='hdf5'.")

        entries: list[dict[str, Any]] = []
        if h5_paths is not None:
            for p in h5_paths:
                path = Path(p).resolve()
                with h5py.File(str(path), "r") as f:
                    n_steps = int(f["flow_fields"].shape[0])
                    re_val = float(f.attrs.get("reynolds_number", 126167.0))
                    u0_val = float(f.attrs.get("u0_m_s", 0.5))
                    d_val = float(f.attrs.get("cylinder_diameter_m", 0.3))
                entries.append(
                    {
                        "case_id": path.stem,
                        "path": str(path),
                        "num_steps": n_steps,
                        "reynolds_number": re_val,
                        "u0_m_s": u0_val,
                        "cylinder_diameter_m": d_val,
                    }
                )
        elif manifest_data and manifest_data.get("trajectories"):
            entries = list(manifest_data["trajectories"])
        else:
            raise ValueError("mode='hdf5' requires either h5_paths or a valid manifest.json with trajectories.")

        for entry in entries:
            n_steps = int(entry["num_steps"])
            if n_steps <= self.step_stride:
                continue
            re_val = float(entry["reynolds_number"])
            u0_val = float(entry["u0_m_s"])
            d_val = float(entry.get("cylinder_diameter_m", 0.3))
            cond = build_condition_vector(re_val, u0_val, d_val)
            path_str = str(Path(entry["path"]).resolve())
            case_id = str(entry.get("case_id", Path(path_str).stem))
            self._trajectories.append(
                {
                    "case_id": case_id,
                    "path": path_str,
                    "num_steps": n_steps,
                    "reynolds_number": re_val,
                    "u0_m_s": u0_val,
                    "cylinder_diameter_m": d_val,
                }
            )
            for local_t in range(0, n_steps - self.step_stride):
                self._samples.append(
                    {
                        "case_id": case_id,
                        "path": path_str,
                        "idx_t": local_t,
                        "idx_next": local_t + self.step_stride,
                        "cond": cond,
                    }
                )

        if not self._samples:
            raise RuntimeError(f"No valid (t, t+{self.step_stride}) pairs found in HDF5 trajectories.")

    @property
    def trajectories(self) -> list[dict[str, Any]]:
        return list(self._trajectories)

    def __len__(self) -> int:
        return len(self._samples)

    def _get_h5(self, path_str: str) -> Any:
        handle = self._h5_handles.get(path_str)
        if handle is None:
            handle = h5py.File(path_str, "r", swmr=False)
            self._h5_handles[path_str] = handle
        return handle

    def _to_tensor(self, arr: np.ndarray) -> Any:
        if not self.return_torch:
            if self.spatial_order == "whd":
                return np.transpose(arr, (0, 3, 2, 1))
            return arr
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="The given NumPy array is not writeable")
            tensor = torch.from_numpy(arr)
        if self.spatial_order == "whd":
            # Zero-copy stride view from (C, 64, 96, 192) -> (C, 192, 96, 64)
            tensor = tensor.permute(0, 3, 2, 1)
        return tensor

    def __getitem__(self, index: int) -> tuple[Any, Any, Any]:
        spec = self._samples[index]
        c_slice = slice(0, self.channels)

        if self.mode == "ram_pool":
            if self._pool_handle is None:
                self._pool_handle = _SharedPoolHandle(self.pool_name, read_only=self.read_only_pool)
            arr_t = self._pool_handle.array[spec["idx_t"], c_slice]
            arr_next = self._pool_handle.array[spec["idx_next"], c_slice]
        else:
            f = self._get_h5(spec["path"])
            dset = f["flow_fields"]
            arr_t = np.asarray(dset[spec["idx_t"], c_slice], dtype=np.float32)
            arr_next = np.asarray(dset[spec["idx_next"], c_slice], dtype=np.float32)

        x_t = self._to_tensor(arr_t)
        x_next = self._to_tensor(arr_next)
        cond = torch.from_numpy(spec["cond"]) if self.return_torch else spec["cond"]
        return x_t, x_next, cond

    def verify_zero_copy(self, index: int = 0) -> bool:
        """Return True iff ``x_t`` shares the exact memory address with the RAM pool."""
        if self.mode != "ram_pool" or self._pool_handle is None:
            return False
        spec = self._samples[index]
        expected_ptr = int(self._pool_handle.array[spec["idx_t"], 0:self.channels].ctypes.data)
        x_t, _, _ = self[index]
        actual_ptr = int(x_t.data_ptr()) if self.return_torch else int(x_t.ctypes.data)
        return actual_ptr == expected_ptr

    def close(self) -> None:
        if self._pool_handle is not None:
            self._pool_handle.close()
            self._pool_handle = None
        for handle in list(self._h5_handles.values()):
            try:
                handle.close()
            except Exception:
                pass
        self._h5_handles.clear()

    def __del__(self) -> None:
        self.close()


def create_world_model_dataloader(
    mode: str = "ram_pool",
    batch_size: int = 4,
    shuffle: bool = True,
    num_workers: int = 0,
    pin_memory: bool = False,
    **dataset_kwargs: Any,
) -> Any:
    """Instantiate a ready-to-train PyTorch DataLoader over WorldModelFlowDataset."""
    if not HAS_TORCH:
        raise ImportError("PyTorch is required for create_world_model_dataloader.")
    dataset = WorldModelFlowDataset(mode=mode, return_torch=True, **dataset_kwargs)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=num_workers,
        pin_memory=pin_memory,
    )


def run_self_test(
    mode: str = "ram_pool",
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    pool_name: str = DEFAULT_POOL_NAME,
    channels: int = 6,
    spatial_order: str = "dhw",
    bench_iters: int = 200,
) -> dict[str, Any]:
    """Run unit verification (shapes, multi-Re cond, zero-NaN, zero-copy, latency)."""
    ds = WorldModelFlowDataset(
        mode=mode,
        manifest_path=manifest_path,
        pool_name=pool_name,
        channels=channels,
        spatial_order=spatial_order,
    )
    try:
        n_pairs = len(ds)
        assert n_pairs > 0, "Dataset has 0 transition pairs"

        expected_spatial = (64, 96, 192) if spatial_order == "dhw" else (192, 96, 64)
        expected_shape = (channels, *expected_spatial)

        # Verify distinct conditions, zero NaNs, and exact shapes across trajectories
        seen_conds = set()
        check_indices = sorted(set([0, n_pairs // 2, n_pairs - 1]))
        for idx in check_indices:
            x_t, x_next, cond = ds[idx]
            assert tuple(x_t.shape) == expected_shape, f"x_t shape mismatch: {x_t.shape}"
            assert tuple(x_next.shape) == expected_shape, f"x_next shape mismatch: {x_next.shape}"
            assert tuple(cond.shape) == (3,), f"cond shape mismatch: {cond.shape}"
            if ds.return_torch:
                assert torch.isfinite(x_t).all().item(), f"Non-finite value in x_t[{idx}]"
                assert torch.isfinite(x_next).all().item(), f"Non-finite value in x_next[{idx}]"
                assert torch.isfinite(cond).all().item(), f"Non-finite value in cond[{idx}]"
                seen_conds.add(tuple(round(float(v), 5) for v in cond.tolist()))
            else:
                assert bool(np.isfinite(x_t).all()), f"Non-finite value in x_t[{idx}]"
                assert bool(np.isfinite(x_next).all()), f"Non-finite value in x_next[{idx}]"
                seen_conds.add(tuple(round(float(v), 5) for v in cond.tolist()))

        zero_copy_ok = ds.verify_zero_copy(0) if mode == "ram_pool" else False

        # Benchmark single-step access latency
        _ = ds[0]
        t0 = time.perf_counter()
        for i in range(bench_iters):
            x_t, x_next, cond = ds[i % n_pairs]
            _ = x_t[0, 0, 0, 0]
        elapsed_s = time.perf_counter() - t0
        latency_us = (elapsed_s / bench_iters) * 1e6

        batch_shape = None
        if HAS_TORCH:
            loader = DataLoader(ds, batch_size=min(4, n_pairs), shuffle=True, num_workers=0)
            bx_t, bx_next, bcond = next(iter(loader))
            batch_shape = {
                "x_t": list(bx_t.shape),
                "x_next": list(bx_next.shape),
                "cond": list(bcond.shape),
            }

        return {
            "status": "PASS",
            "mode": mode,
            "has_torch": HAS_TORCH,
            "num_trajectories": len(ds.trajectories),
            "num_pairs": n_pairs,
            "sample_shape": list(expected_shape),
            "distinct_cond_vectors_sampled": [list(c) for c in sorted(seen_conds)],
            "zero_nan_verified": True,
            "zero_copy_pointer_match": zero_copy_ok,
            "single_pair_latency_us": round(latency_us, 2),
            "bench_iters": bench_iters,
            "dataloader_batch_shapes": batch_shape,
        }
    finally:
        ds.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify & benchmark WorldModelFlowDataset")
    parser.add_argument("--mode", choices=["ram_pool", "hdf5"], default="ram_pool")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--pool-name", type=str, default=DEFAULT_POOL_NAME)
    parser.add_argument("--channels", type=int, choices=[4, 6], default=6)
    parser.add_argument("--spatial-order", choices=["dhw", "whd"], default="dhw")
    parser.add_argument("--bench-iters", type=int, default=200)
    args = parser.parse_args()

    report = run_self_test(
        mode=args.mode,
        manifest_path=args.manifest,
        pool_name=args.pool_name,
        channels=args.channels,
        spatial_order=args.spatial_order,
        bench_iters=args.bench_iters,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
