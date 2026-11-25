"""Offline unit and CLI tests for the Memmap BFloat16 training pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.memmap_cache import (
    HAS_H5PY,
    benchmark_memmap_read,
    bf16_u16_to_float32,
    convert_h5_archive_to_memmap,
    float32_to_bf16_u16,
)
from hydro_disturb_bench.world_model_dataloader import (
    HAS_TORCH,
    WorldModelMemmapDataset,
    attach_static_geometry_gpu,
)


def _write_synthetic_memmap_cache(cache_dir: Path, n_steps: int = 8, dtype: str = "bf16") -> None:
    """Create a synthetic minimal Memmap cache directory matching the v2.0 layout."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    geom = np.zeros((2, 64, 96, 192), dtype=np.float32)
    geom[0] = 0.25
    geom[1, :, 40:56, 40:56] = 1.0
    np.save(str(cache_dir / "geometry_static.npy"), geom)

    rng = np.random.default_rng(7)
    dyn = rng.normal(loc=0.5, scale=0.2, size=(n_steps, 4, 64, 96, 192)).astype(np.float32)

    dat_name = f"synth_dyn4ch_{dtype}.dat"
    np_dtype = np.uint16 if dtype == "bf16" else np.float32
    mm = np.memmap(str(cache_dir / dat_name), dtype=np_dtype, mode="w+", shape=(n_steps, 4, 64, 96, 192))
    mm[:] = float32_to_bf16_u16(dyn) if dtype == "bf16" else dyn
    mm.flush()
    del mm

    t_phys = np.linspace(0.0, 0.7, n_steps, dtype=np.float32)
    t_ctu = t_phys * 0.5 / 0.3
    np.savez(
        str(cache_dir / "synth_aux.npz"),
        flow_time_physical_s=t_phys,
        flow_time_nondim_ctu=t_ctu,
        step_indices=np.arange(1, n_steps + 1, dtype=np.int32),
        flow_aligned_cd=np.linspace(0.35, 0.42, n_steps, dtype=np.float32),
        flow_aligned_cl=np.sin(np.linspace(0.0, 2.0, n_steps, dtype=np.float32)),
    )

    index_payload = {
        "format_version": "WorldModel-Memmap-v1.0",
        "dtype": dtype,
        "shared_geometry": {
            "geometry_file": "geometry_static.npy",
            "geometry_shape": [2, 64, 96, 192],
            "grid_spacing_star": [0.0625, 0.0625, 0.0625],
        },
        "cases": [
            {
                "case": "synth",
                "dat_file": dat_name,
                "aux_file": "synth_aux.npz",
                "shape": [n_steps, 4, 64, 96, 192],
                "dtype": dtype,
                "reynolds_number": 126167.0,
                "u0_m_s": 0.5,
                "cylinder_diameter_m": 0.3,
                "stationary_start_idx": 3,
                "stationary_ctu": [float(t_ctu[3]), float(t_ctu[-1])],
            }
        ],
    }
    (cache_dir / "memmap_index.json").write_text(json.dumps(index_payload), encoding="utf-8")


def test_bfloat16_roundtrip_rne_accuracy_and_special_values():
    """Verify IEEE-754 round-to-nearest-even conversion preserves NaN/Inf and mantissa bounds."""
    vals = np.array(
        [0.0, -0.0, 1.0, -3.1415927, 256.75, 1e-12, 1e12, np.nan, np.inf, -np.inf],
        dtype=np.float32,
    )
    bits = float32_to_bf16_u16(vals)
    assert bits.dtype == np.uint16
    decoded = bf16_u16_to_float32(bits)
    assert decoded.dtype == np.float32
    assert np.isnan(decoded[7])
    assert np.isinf(decoded[8]) and decoded[8] > 0
    assert np.isinf(decoded[9]) and decoded[9] < 0
    rel_err = np.max(np.abs(vals[:7] - decoded[:7]) / np.maximum(np.abs(vals[:7]), 1e-30))
    assert rel_err < 4e-3


def test_memmap_dataset_regimes_attach_and_benchmark(tmp_path):
    """Test WorldModelMemmapDataset regime slicing, static attach, and raw read benchmark."""
    cache_dir = tmp_path / "cache"
    _write_synthetic_memmap_cache(cache_dir, n_steps=8, dtype="bf16")

    # Stationary starts at idx 3 -> steps [3..8) = 5 steps, window 2+2=4 -> 2 samples (t0=3, 4)
    ds_stat = WorldModelMemmapDataset(
        cache_dir=cache_dir,
        context_len=2,
        pred_len=2,
        stride=1,
        regime="stationary",
        return_bf16=True,
        return_torch=False,
    )
    assert len(ds_stat) == 2
    sample = ds_stat[0]
    assert sample["x_ctx"].shape == (2, 4, 64, 96, 192)
    assert sample["y_fut"].shape == (2, 4, 64, 96, 192)
    assert sample["cond"].shape == (3,)
    assert sample["cond"][0] == pytest.approx(np.log10(126167.0), rel=1e-5)
    assert sample["cond"][1] == pytest.approx(0.5)
    assert sample["cond"][2] == pytest.approx(0.3)
    assert sample["cd"].shape == (4,)
    assert sample["cl"].shape == (4,)
    assert np.isfinite(sample["x_ctx"]).all()

    # Worker mmap handle should be cached on second access
    mm_first = ds_stat._get_mmap(0)
    assert ds_stat._get_mmap(0) is mm_first

    # Static geometry attachment in batched numpy mode (1, T, 4, Z, Y, X) -> (1, T, 6, Z, Y, X)
    batched = sample["x_ctx"][None, ...]
    attached = attach_static_geometry_gpu(batched, ds_stat.static_geometry_np)
    assert attached.shape == (1, 2, 6, 64, 96, 192)
    assert np.allclose(attached[0, 0, 4:], ds_stat.static_geometry_np)

    # Full regime across 8 steps with window 4 -> 5 samples
    ds_full = WorldModelMemmapDataset(
        cache_dir=cache_dir, context_len=2, pred_len=2, regime="full", return_torch=False
    )
    assert len(ds_full) == 5

    # Benchmark CLI and helper
    bench = benchmark_memmap_read(cache_dir, num_reads=4)
    assert bench["num_reads"] == 4
    assert bench["throughput_gib_s"] if "throughput_gib_s" in bench else bench["throughput_mib_s"] > 0

    rc = cli_main(["benchmark-memmap", "--cache-dir", str(cache_dir), "--num-reads", "4"])
    assert rc == 0


@pytest.mark.skipif(not HAS_H5PY, reason="h5py not installed in environment")
def test_h5_to_memmap_end_to_end_and_geometry_mismatch(tmp_path):
    """Verify full HDF5 -> Memmap bf16/fp32 conversion, aerodynamics alignment, and CLI."""
    import h5py

    h5_dir = tmp_path / "h5"
    h5_dir.mkdir()
    out_bf16 = tmp_path / "mm_bf16"

    rng = np.random.default_rng(12)
    flow = rng.normal(0.0, 1.0, size=(6, 6, 64, 96, 192)).astype(np.float32)
    flow[:, 4] = 0.1
    flow[:, 5] = 0.0
    flow[:, :, :, 0:4, 0:4] = 0.0
    flow[:, 5, :, 0:4, 0:4] = 1.0

    def _make_h5(path: Path, mask_offset: float = 0.0) -> None:
        arr = flow.copy()
        if mask_offset != 0.0:
            arr[:, 5] = mask_offset
        with h5py.File(str(path), "w") as f:
            f.create_dataset("flow_fields", data=arr)
            t_phys = np.linspace(0.0, 5.0, 6, dtype=np.float32)
            t_ctu = t_phys * 0.5 / 0.3
            f.create_dataset("time_physical_s", data=t_phys)
            f.create_dataset("time_nondim_ctu", data=t_ctu)
            f.create_dataset("step_indices", data=np.arange(1, 7, dtype=np.int32))
            cg = f.create_group("coordinates")
            cg.create_dataset("x_star", data=np.arange(192, dtype=np.float32) * 0.0625)
            cg.create_dataset("y_star", data=np.arange(96, dtype=np.float32) * 0.0625)
            cg.create_dataset("z_star", data=np.arange(64, dtype=np.float32) * 0.0625)
            ag = f.create_group("aerodynamics")
            ag.create_dataset("time_physical_s", data=np.linspace(0.0, 5.0, 12, dtype=np.float32))
            ag.create_dataset("time_nondim_ctu", data=np.linspace(0.0, 5.0, 12, dtype=np.float32))
            ag.create_dataset("cd", data=np.full(12, 0.38, dtype=np.float32))
            ag.create_dataset("cl", data=np.linspace(-0.02, 0.02, 12, dtype=np.float32))
            ag.attrs["strouhal_number"] = 0.24
            f.attrs["reynolds_number"] = 126167.0
            f.attrs["u0_m_s"] = 0.5
            f.attrs["cylinder_diameter_m"] = 0.3
            f.attrs["stationary_vortex_shedding_ctu"] = [3.0, float(t_ctu[-1])]

    _make_h5(h5_dir / "c04_golden_wm_full_trajectory.h5")

    rc = cli_main(
        [
            "convert-memmap",
            "--input-dir",
            str(h5_dir),
            "--output-dir",
            str(out_bf16),
            "--dtype",
            "bf16",
            "--workers",
            "1",
            "--verify",
            "--benchmark",
        ]
    )
    assert rc == 0
    idx_data = json.loads((out_bf16 / "memmap_index.json").read_text(encoding="utf-8"))
    assert idx_data["total_frames"] == 6
    assert idx_data["cases"][0]["stationary_start_idx"] == 2
    assert idx_data["cases"][0]["aerodynamics_attrs"]["strouhal_number"] == pytest.approx(0.24)

    # Second HDF5 with mismatched geometry mask must fail fast
    _make_h5(h5_dir / "bad_case_wm_full_trajectory.h5", mask_offset=1.0)
    with pytest.raises(ValueError, match="Geometry mismatch"):
        convert_h5_archive_to_memmap(h5_dir, tmp_path / "mm_bad", workers=1)


@pytest.mark.skipif(not HAS_TORCH, reason="PyTorch not installed in environment")
def test_torch_memmap_dataloader_and_gpu_vorticity(tmp_path):
    """Verify PyTorch Tensor bfloat16 zero-copy view, attach, and 3D vorticity shapes."""
    import torch

    from hydro_disturb_bench.world_model_dataloader import (
        compute_vorticity_3d_gpu,
        create_memmap_dataloader,
    )

    cache_dir = tmp_path / "cache_torch"
    _write_synthetic_memmap_cache(cache_dir, n_steps=8, dtype="bf16")
    ds, loader = create_memmap_dataloader(
        cache_dir=cache_dir,
        batch_size=2,
        context_len=2,
        pred_len=2,
        regime="stationary",
        num_workers=0,
        shuffle=False,
        return_bf16=True,
    )
    static_cpu = ds.get_static_geometry_tensor(device="cpu", dtype=torch.bfloat16)
    batch = next(iter(loader))
    assert batch["x_ctx"].dtype == torch.bfloat16
    x6 = attach_static_geometry_gpu(batch["x_ctx"], static_cpu)
    assert x6.shape == (2, 2, 6, 64, 96, 192)
    omega = compute_vorticity_3d_gpu(x6[:, :, :3].float(), ds.grid_spacing_star)
    assert omega.shape == (2, 2, 3, 64, 96, 192)
    assert torch.isfinite(omega).all()
