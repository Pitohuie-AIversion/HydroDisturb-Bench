"""Self-contained benchmark dataset packaging, serialization, and cryptographic verification.

This module provides standardized, self-contained packaging for HydroDisturb-Bench
world model benchmarks, bundling:
- Continuous spatiotemporal flow fields (u, v, w, p)
- Coordinate vectors (x, y, z) and monotonic timestamps
- Geometric boundaries (solid_mask, signed distance field sdf)
- Sparse sensor topologies and noisy observation series
- Exact ground truth disturbance parameters (hidden from learners)
- SHA-256 cryptographic verification manifests ensuring bit-for-bit reproducibility
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .comsol_java.errors import ModelValidationError


def _compute_array_sha256(arr: np.ndarray) -> str:
    """Compute deterministic SHA-256 checksum of numpy array buffer."""
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()


@dataclass
class BundleManifest:
    """Cryptographic manifest documenting all arrays and metadata in a benchmark bundle."""

    benchmark_name: str
    dim: int
    num_frames: int
    num_channels: int
    channels: list[str]
    has_disturbance: bool
    has_sparse_observations: bool
    array_shapes: dict[str, list[int]]
    array_dtypes: dict[str, str]
    array_sha256: dict[str, str]
    custom_metadata: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> BundleManifest:
        data = json.loads(json_str)
        return cls(
            benchmark_name=str(data["benchmark_name"]),
            dim=int(data["dim"]),
            num_frames=int(data["num_frames"]),
            num_channels=int(data["num_channels"]),
            channels=list(data["channels"]),
            has_disturbance=bool(data["has_disturbance"]),
            has_sparse_observations=bool(data["has_sparse_observations"]),
            array_shapes={k: list(v) for k, v in data["array_shapes"].items()},
            array_dtypes={k: str(v) for k, v in data["array_dtypes"].items()},
            array_sha256={k: str(v) for k, v in data["array_sha256"].items()},
            custom_metadata=dict(data.get("custom_metadata", {})),
        )


def create_benchmark_bundle(
    output_path: str | Path,
    times: np.ndarray,
    coords: dict[str, np.ndarray],
    values: np.ndarray,
    channels: list[str] | None = None,
    solid_mask: np.ndarray | None = None,
    sdf: np.ndarray | None = None,
    layout_json: str | None = None,
    observations: np.ndarray | None = None,
    disturbance_json: str | None = None,
    benchmark_name: str = "HydroDisturb-Bench",
    metadata: dict[str, Any] | None = None,
) -> Path:
    """Package a self-contained benchmark dataset into a verified NPZ bundle.

    Parameters
    ----------
    output_path : str or Path
        Destination .npz file path.
    times : np.ndarray
        1D array of strictly increasing physical timestamps.
    coords : dict[str, np.ndarray]
        Coordinate dictionary, e.g. {"x": x, "y": y} for 2D or {"x": x, "y": y, "z": z} for 3D.
    values : np.ndarray
        Flow tensor of shape (T, C, Ny, Nx) or (T, C, Nz, Ny, Nx).
    channels : list[str], optional
        List of channel names, e.g. ["u", "v", "p"] or ["u", "v", "w", "p"].
    solid_mask : np.ndarray, optional
        Boolean/integer array marking solid obstacle.
    sdf : np.ndarray, optional
        Signed distance field.
    layout_json : str, optional
        Serialized JSON string of the sparse sensor layout.
    observations : np.ndarray, optional
        Sparse sensor observations of shape (T, K, C).
    disturbance_json : str, optional
        Serialized JSON string of ground truth disturbance parameters.
    benchmark_name : str
        Name tag for the benchmark suite.
    metadata : dict, optional
        User custom metadata.

    Returns
    -------
    Path
        Absolute path to the created .npz bundle.
    """
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    times_arr = np.asarray(times, dtype=np.float64)
    if times_arr.ndim != 1 or len(times_arr) == 0:
        raise ModelValidationError("times must be a non-empty 1D array")
    if not np.all(np.diff(times_arr) > 0):
        raise ModelValidationError("times must be strictly monotonically increasing")

    val_arr = np.asarray(values, dtype=np.float64)
    if val_arr.ndim not in (4, 5):
        raise ModelValidationError(
            f"values array must be 4D (T, C, Ny, Nx) or 5D (T, C, Nz, Ny, Nx), got {val_arr.ndim}D"
        )
    T, C = val_arr.shape[:2]
    if T != len(times_arr):
        raise ModelValidationError(
            f"Time dimension mismatch: values T={T} vs times={len(times_arr)}"
        )

    dim = 2 if val_arr.ndim == 4 else 3
    if channels is None:
        channels = ["u", "v", "p"] if dim == 2 else ["u", "v", "w", "p"]
    if len(channels) != C:
        raise ModelValidationError(f"channels length {len(channels)} != C {C}")

    bundle_dict: dict[str, np.ndarray] = {
        "times": times_arr,
        "values": val_arr,
    }

    for axis_name, axis_arr in coords.items():
        bundle_dict[f"coord_{axis_name}"] = np.asarray(axis_arr, dtype=np.float64)

    if solid_mask is not None:
        bundle_dict["solid_mask"] = np.asarray(solid_mask, dtype=np.int8)

    if sdf is not None:
        bundle_dict["sdf"] = np.asarray(sdf, dtype=np.float64)

    if observations is not None:
        bundle_dict["observations"] = np.asarray(observations, dtype=np.float64)

    # Compute array checksums
    shapes: dict[str, list[int]] = {}
    dtypes: dict[str, str] = {}
    checksums: dict[str, str] = {}

    for k, v in bundle_dict.items():
        shapes[k] = list(v.shape)
        dtypes[k] = str(v.dtype)
        checksums[k] = _compute_array_sha256(v)

    custom_meta = dict(metadata or {})
    if layout_json:
        custom_meta["sensor_layout"] = json.loads(layout_json)
    if disturbance_json:
        custom_meta["disturbance_ground_truth"] = json.loads(disturbance_json)

    manifest = BundleManifest(
        benchmark_name=benchmark_name,
        dim=dim,
        num_frames=T,
        num_channels=C,
        channels=channels,
        has_disturbance=disturbance_json is not None,
        has_sparse_observations=observations is not None,
        array_shapes=shapes,
        array_dtypes=dtypes,
        array_sha256=checksums,
        custom_metadata=custom_meta,
    )

    # Store manifest as 1D string array
    bundle_dict["bundle_manifest"] = np.array([manifest.to_json()], dtype=object)

    np.savez_compressed(out_file, **bundle_dict)
    return out_file


def verify_benchmark_bundle(bundle_path: str | Path) -> dict[str, Any]:
    """Verify cryptographic integrity, schema, and physical validity of a benchmark bundle.

    Raises
    ------
    ModelValidationError
        If verification fails due to checksum mismatch, missing manifest, or invalid data.
    """
    path = Path(bundle_path).resolve()
    if not path.is_file():
        raise ModelValidationError(f"Benchmark bundle file not found: {path}")

    with np.load(path, allow_pickle=True) as data:
        if "bundle_manifest" not in data:
            raise ModelValidationError(
                f"Missing bundle_manifest in benchmark bundle {path.name}"
            )

        manifest_str = str(data["bundle_manifest"][0])
        manifest = BundleManifest.from_json(manifest_str)

        # Check each array against manifest checksum
        for arr_name, expected_sha in manifest.array_sha256.items():
            if arr_name not in data:
                raise ModelValidationError(
                    f"Array '{arr_name}' recorded in manifest missing from bundle"
                )
            arr = data[arr_name]
            computed_sha = _compute_array_sha256(arr)
            if computed_sha != expected_sha:
                raise ModelValidationError(
                    f"Checksum mismatch for array '{arr_name}': expected {expected_sha[:8]}, got {computed_sha[:8]}"
                )

        # Check physical timestamps
        times = data["times"]
        if not np.all(np.diff(times) > 0):
            raise ModelValidationError("Timestamps are not strictly monotonically increasing")

        # Check fluid finiteness
        values = data["values"]
        solid_mask = data.get("solid_mask", None)
        if solid_mask is not None:
            mask_arr = np.asarray(solid_mask, dtype=bool)
            # Expand mask to match values shape
            fluid_slice = ~mask_arr
            # Check fluid cells for non-finite entries
            if values.ndim == 4:
                # (T, C, Ny, Nx)
                fluid_vals = values[:, :, fluid_slice]
            else:
                # (T, C, Nz, Ny, Nx)
                fluid_vals = values[:, :, fluid_slice]
            if not np.all(np.isfinite(fluid_vals)):
                raise ModelValidationError(
                    "Non-finite values (NaN/Inf) detected in fluid region"
                )
        else:
            if not np.all(np.isfinite(values)):
                raise ModelValidationError("Non-finite values (NaN/Inf) detected in values")

    return {
        "status": "VALID",
        "benchmark_name": manifest.benchmark_name,
        "dim": manifest.dim,
        "frames": manifest.num_frames,
        "channels": manifest.channels,
        "verified_arrays": len(manifest.array_sha256),
    }


def load_benchmark_bundle(
    bundle_path: str | Path, verify: bool = True
) -> dict[str, Any]:
    """Load an unpacked benchmark bundle into memory with optional integrity verification.

    Returns
    -------
    dict[str, Any]
        Dictionary with 'manifest', 'times', 'coords', 'values', 'channels',
        and optional 'solid_mask', 'sdf', 'observations', 'custom_metadata'.
    """
    path = Path(bundle_path).resolve()
    if verify:
        verify_benchmark_bundle(path)

    with np.load(path, allow_pickle=True) as data:
        manifest_str = str(data["bundle_manifest"][0])
        manifest = BundleManifest.from_json(manifest_str)

        coords = {
            k[len("coord_") :]: data[k]
            for k in data.files
            if k.startswith("coord_")
        }

        res: dict[str, Any] = {
            "manifest": manifest,
            "times": data["times"],
            "coords": coords,
            "values": data["values"],
            "channels": manifest.channels,
            "dim": manifest.dim,
            "custom_metadata": manifest.custom_metadata,
        }

        if "solid_mask" in data:
            res["solid_mask"] = data["solid_mask"]
        if "sdf" in data:
            res["sdf"] = data["sdf"]
        if "observations" in data:
            res["observations"] = data["observations"]

        return res
