#!/usr/bin/env python3
"""Build a unified metadata manifest for 3D CFD World Model HDF5 & RAM-Pool datasets.

Enforces the Physical Ground-Truth Reverse-Lookup Gate:
1. Audits all completed COMSOL solver summaries (calibration_case_summary.json /
   run_result.json) and reads TRUE COMSOL parameter readbacks (ReD, U0, D) —
   NEVER trusting directory names alone.
2. Detects nominal-vs-actual Reynolds/velocity mismatches (specifically flagging
   ``re180k_mesh1_pilot`` whose COMSOL readback is U0=0.50 m/s, ReD=126,167 as
   ``quarantined_relabel_126k_yplus0727`` and blocking it from Re=180k training).
3. Scans chunked HDF5 trajectories under E:\\cfd_3d\\03_world_model_dataset\\*.h5,
   cross-checks against COMSOL ground truth, inspects the active zero-copy RAM pool
   (cfd_ram_pool_v1), and atomically writes manifest.json for WorldModelFlowDataset.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import mmap
import os
import re
import struct
from pathlib import Path
from typing import Any

import h5py
import numpy as np

DEFAULT_DATASET_DIR = Path(r"E:\cfd_3d\03_world_model_dataset")
DEFAULT_RESULTS_ROOT = Path(r"E:\codex_comsol_takeover\20261113\cpu_fill\results")
DEFAULT_CONTINUATION_ROOT = Path(r"E:\codex_comsol_takeover\20261113\cpu_fill\continuation_20261114")
DEFAULT_POOL_NAME = "cfd_ram_pool_v1"
POOL_MAGIC = b"WM48"
BYTES_PER_STEP = 6 * 64 * 96 * 192 * 4  # 28,311,552 bytes (~27.0 MiB)
META_TABLE_SIZE = 16 * 1024 * 1024      # 16 MiB header region

KNOWN_QUARANTINE_RULES = {
    "re180k_mesh1_pilot": "quarantined_relabel_126k_yplus0727",
}


def _decode_attr(val: Any) -> Any:
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    if isinstance(val, np.ndarray):
        return [_decode_attr(x) for x in val.tolist()]
    if isinstance(val, (np.floating, np.integer)):
        return val.item()
    return val


def _parse_nominal_re_from_name(name: str) -> float | None:
    """Parse nominal Re implied by a case folder name ONLY for discrepancy detection."""
    m = re.search(r"re(\d+(?:\.\d+)?)([km])", name.lower())
    if not m:
        return None
    val = float(m.group(1))
    unit = m.group(2)
    return val * (1e3 if unit == "k" else 1e6)


def read_comsol_ground_truth(case_dir: Path) -> dict[str, Any] | None:
    """Read true COMSOL ReD, U0, D from calibration_case_summary.json or run_result.json."""
    summary_path = case_dir / "calibration_case_summary.json"
    result_path = case_dir / "run_result.json"

    params = None
    source_file = None
    status = None
    t_max = None

    if summary_path.is_file():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8-sig"))
            cr = data.get("comsol_result", {})
            if isinstance(cr, dict) and isinstance(cr.get("parameters"), dict):
                params = cr["parameters"]
                status = cr.get("status")
                t_max = cr.get("t_max")
                source_file = str(summary_path)
        except Exception:
            pass

    if params is None and result_path.is_file():
        try:
            data = json.loads(result_path.read_text(encoding="utf-8-sig"))
            if isinstance(data.get("parameters"), dict):
                params = data["parameters"]
                status = data.get("status")
                t_max = data.get("t_max")
                source_file = str(result_path)
        except Exception:
            pass

    if not params or "ReD" not in params or "U0" not in params:
        return None

    return {
        "reynolds_number": float(params["ReD"]),
        "u0_m_s": float(params["U0"]),
        "cylinder_diameter_m": float(params.get("D", 0.3)),
        "density_kg_m3": float(params.get("rho0", 1025.8944)),
        "l_span_m": float(params.get("L_span", 3.0)),
        "status": status,
        "t_max_s": t_max,
        "ground_truth_file": source_file,
    }


def audit_completed_solvers(
    results_root: Path = DEFAULT_RESULTS_ROOT,
    continuation_root: Path = DEFAULT_CONTINUATION_ROOT,
) -> dict[str, dict[str, Any]]:
    """Audit all completed solver outputs and flag any nominal-vs-COMSOL Re/U0 mismatch."""
    audit: dict[str, dict[str, Any]] = {}
    search_roots = [p for p in (results_root, continuation_root) if p.exists()]

    for root in search_roots:
        for summary_path in sorted(root.rglob("calibration_case_summary.json")):
            case_dir = summary_path.parent
            gt = read_comsol_ground_truth(case_dir)
            if gt is None:
                continue
            # Determine logical job name (parent of B2_rtol5e4_max005 or results dir)
            job_name = case_dir.parent.name
            if job_name == "results":
                job_name = case_dir.parent.parent.name

            nominal_re = _parse_nominal_re_from_name(job_name)
            actual_re = gt["reynolds_number"]
            actual_u0 = gt["u0_m_s"]

            rel_err = None
            mismatch = False
            if nominal_re is not None and nominal_re > 0:
                rel_err = abs(actual_re - nominal_re) / nominal_re
                mismatch = rel_err > 0.02

            quarantine_status = None
            if job_name in KNOWN_QUARANTINE_RULES and mismatch:
                quarantine_status = KNOWN_QUARANTINE_RULES[job_name]
            elif mismatch:
                quarantine_status = f"quarantined_re_mismatch_actual_{int(round(actual_re))}"

            audit[job_name] = {
                "job_name": job_name,
                "case_dir": str(case_dir),
                "nominal_re_from_name": nominal_re,
                "actual_comsol_re": round(actual_re, 2),
                "actual_comsol_u0_m_s": actual_u0,
                "cylinder_diameter_m": gt["cylinder_diameter_m"],
                "t_max_s": gt["t_max_s"],
                "relative_re_discrepancy": round(rel_err, 6) if rel_err is not None else 0.0,
                "ground_truth_file": gt["ground_truth_file"],
                "quarantine_status": quarantine_status,
                "training_eligible": (quarantine_status is None),
            }

    return audit


def probe_ram_pool(pool_name: str = DEFAULT_POOL_NAME) -> dict[str, Any]:
    """Read header metadata from an active Windows/POSIX shared memory RAM pool.

    Uses a 12-byte read-only mmap on Windows so probing never incurs 40 GB
    pagefile commit charge (avoiding WinError 1455 under heavy solver load).
    """
    header_bytes = None
    total_bytes = 0
    try:
        mm = mmap.mmap(-1, 12, tagname=pool_name, access=mmap.ACCESS_READ)
        try:
            header_bytes = mm[:12]
        finally:
            mm.close()
    except OSError:
        try:
            from multiprocessing import shared_memory
            shm = shared_memory.SharedMemory(name=pool_name, create=False)
            try:
                header_bytes = bytes(shm.buf[:12])
                total_bytes = shm.size
            finally:
                shm.close()
        except (ImportError, FileNotFoundError, OSError):
            return {"active": False, "name": pool_name}

    magic, capacity, current = struct.unpack("<4sII", header_bytes)
    if magic != POOL_MAGIC:
        return {"active": False, "name": pool_name, "error": f"invalid_magic:{magic!r}"}
    if total_bytes == 0:
        total_bytes = META_TABLE_SIZE + capacity * BYTES_PER_STEP
    return {
        "active": True,
        "name": pool_name,
        "capacity_steps": int(capacity),
        "current_steps": int(current),
        "bytes_per_step": BYTES_PER_STEP,
        "meta_table_bytes": META_TABLE_SIZE,
        "pool_size_gb": round(total_bytes / (1024 ** 3), 2),
        "used_mb": round((META_TABLE_SIZE + current * BYTES_PER_STEP) / (1024 ** 2), 2),
    }


def inspect_h5_trajectory(
    h5_path: Path,
    solver_audit: dict[str, dict[str, Any]],
    verify_finite: bool = True,
) -> dict[str, Any]:
    """Extract metadata, COMSOL ground-truth cross-check, and integrity stats for one HDF5."""
    with h5py.File(str(h5_path), "r") as f:
        if "flow_fields" not in f:
            raise ValueError(f"Missing 'flow_fields' dataset in {h5_path}")
        dset = f["flow_fields"]
        shape = [int(x) for x in dset.shape]
        num_steps = shape[0]
        num_channels = shape[1]
        spatial_shape = shape[2:]

        h5_re = float(_decode_attr(f.attrs.get("reynolds_number", 126167.0)))
        h5_u0 = float(_decode_attr(f.attrs.get("u0_m_s", 0.5)))
        h5_d = float(_decode_attr(f.attrs.get("cylinder_diameter_m", 0.3)))
        rho_val = float(_decode_attr(f.attrs.get("density_kg_m3", 1025.8944)))
        nu_val = float(_decode_attr(f.attrs.get("kinematic_viscosity_m2_s", 1.1889e-6)))
        source_model = str(_decode_attr(f.attrs.get("source_model", "")))
        channels = _decode_attr(
            f.attrs.get("tensor_channels", ["u_star", "v_cross_star", "w_span_star", "p_star", "sdf_star", "solid_mask"])
        )

        # Physical ground-truth reverse lookup via calibration_case_summary.json
        re_val = h5_re
        u0_val = h5_u0
        d_val = h5_d
        gt_verified = False
        gt_source = "hdf5_attrs_only"
        quarantine_status: str | None = None
        gt_warning: str | None = None

        # 1. Check if filename or source_model references a quarantined solver (e.g., re180k_mesh1_pilot)
        lower_ident = f"{h5_path.name} {source_model}".lower()
        for q_job, q_tag in KNOWN_QUARANTINE_RULES.items():
            if q_job in lower_ident or ("re180k" in h5_path.name.lower() and "true" not in h5_path.name.lower()):
                q_rec = solver_audit.get(q_job)
                if q_rec and q_rec.get("quarantine_status"):
                    quarantine_status = q_rec["quarantine_status"]
                    re_val = float(q_rec["actual_comsol_re"])
                    u0_val = float(q_rec["actual_comsol_u0_m_s"])
                    d_val = float(q_rec["cylinder_diameter_m"])
                    gt_source = q_rec["ground_truth_file"]

        # 2. Reverse-lookup sibling calibration_case_summary.json next to source_model
        if quarantine_status is None and source_model:
            src_dir = Path(source_model).parent
            gt = read_comsol_ground_truth(src_dir)
            if gt is not None:
                gt_re = gt["reynolds_number"]
                gt_u0 = gt["u0_m_s"]
                re_rel = abs(gt_re - h5_re) / max(gt_re, 1.0)
                u0_rel = abs(gt_u0 - h5_u0) / max(gt_u0, 1e-9)
                if re_rel <= 0.02 and u0_rel <= 0.02:
                    # Prefer exact COMSOL ground truth readback
                    re_val = gt_re
                    u0_val = gt_u0
                    d_val = gt["cylinder_diameter_m"]
                    gt_verified = True
                    gt_source = gt["ground_truth_file"]
                else:
                    # Check if this is a known mismatched solver folder
                    parent_job = src_dir.parent.name
                    if parent_job in solver_audit and solver_audit[parent_job].get("quarantine_status"):
                        quarantine_status = solver_audit[parent_job]["quarantine_status"]
                        re_val = gt_re
                        u0_val = gt_u0
                        gt_source = gt["ground_truth_file"]
                    else:
                        gt_warning = (
                            f"h5_attrs(Re={h5_re},U0={h5_u0}) != source_model summary "
                            f"(Re={gt_re:.1f},U0={gt_u0}) at {src_dir}"
                        )

        t_phys = f["time_physical_s"][:].astype(np.float64) if "time_physical_s" in f else np.arange(num_steps, dtype=np.float64)
        t_ctu = f["time_nondim_ctu"][:].astype(np.float64) if "time_nondim_ctu" in f else t_phys * u0_val / max(d_val, 1e-9)
        dt_phys_mean = float(np.mean(np.diff(t_phys))) if len(t_phys) >= 2 else 0.0
        dt_ctu_mean = float(np.mean(np.diff(t_ctu))) if len(t_ctu) >= 2 else 0.0

        finite_ok = True
        if verify_finite and num_steps > 0:
            sample_indices = sorted(set([0, num_steps // 2, num_steps - 1]))
            for idx in sample_indices:
                frame = dset[idx]
                if not bool(np.isfinite(frame).all()):
                    finite_ok = False
                    break

        cond_vec = [round(math.log10(max(re_val, 1.0)), 6), round(u0_val, 6), round(d_val, 6)]

        return {
            "case_id": h5_path.stem,
            "path": str(h5_path.resolve()),
            "file_name": h5_path.name,
            "file_size_bytes": int(h5_path.stat().st_size),
            "file_size_mb": round(h5_path.stat().st_size / (1024 ** 2), 2),
            "reynolds_number": re_val,
            "u0_m_s": u0_val,
            "cylinder_diameter_m": d_val,
            "density_kg_m3": rho_val,
            "kinematic_viscosity_m2_s": nu_val,
            "cond_vector": cond_vec,
            "cond_fields": ["log10_Re", "U0_m_s", "D_m"],
            "num_steps": num_steps,
            "num_pairs": max(0, num_steps - 1),
            "num_channels": num_channels,
            "channels": channels,
            "tensor_shape": shape,
            "spatial_shape_dhw": spatial_shape,
            "chunks": [int(x) for x in dset.chunks] if dset.chunks else None,
            "dtype": str(dset.dtype),
            "t_phys_min_s": float(t_phys[0]) if len(t_phys) else 0.0,
            "t_phys_max_s": float(t_phys[-1]) if len(t_phys) else 0.0,
            "dt_phys_mean_s": round(dt_phys_mean, 6),
            "t_ctu_min": float(t_ctu[0]) if len(t_ctu) else 0.0,
            "t_ctu_max": float(t_ctu[-1]) if len(t_ctu) else 0.0,
            "dt_ctu_mean": round(dt_ctu_mean, 6),
            "finite_verified": finite_ok,
            "ground_truth_verified": gt_verified,
            "ground_truth_source": gt_source,
            "ground_truth_warning": gt_warning,
            "quarantine_status": quarantine_status,
            "training_eligible": (quarantine_status is None and finite_ok and num_steps >= 2),
            "source_model": source_model,
            "created_at": str(_decode_attr(f.attrs.get("created_at", ""))),
            "is_production_trajectory": (
                quarantine_status is None and num_steps >= 2 and not h5_path.name.startswith("test_")
            ),
        }


def map_ram_pool_segments_by_content(
    pool_info: dict[str, Any],
    trajectories: list[dict[str, Any]],
    dataset_dir: Path,
    solver_audit: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map occupied RAM pool slots to trajectories via direct content fingerprinting.

    Prevents alphabetical-sort label scrambling (e.g. re1m vs re200k) by comparing
    live float32 slices in the shared memory pool against each candidate HDF5's
    first frame (``np.allclose(..., atol=1e-6)``).
    """
    if not pool_info.get("active"):
        return []
    cur_steps = int(pool_info.get("current_steps", 0))
    if cur_steps < 2:
        return []

    # Build candidate pool including optional test_re5k_pilot_sample prefix
    candidates: list[dict[str, Any]] = []
    test_prefix = dataset_dir / "test_re5k_pilot_sample.h5"
    if test_prefix.exists():
        try:
            candidates.append(inspect_h5_trajectory(test_prefix, solver_audit=solver_audit, verify_finite=False))
        except Exception:
            pass
    # Sort production trajectories chronologically by mtime as tie-breaker
    candidates.extend(sorted(trajectories, key=lambda t: Path(t["path"]).stat().st_mtime))

    # Pre-load compact 64-float fingerprint from frame 0 and frame -1 of each candidate
    fingerprints: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for cand in candidates:
        try:
            with h5py.File(cand["path"], "r") as f:
                ds = f["flow_fields"]
                fp0 = np.asarray(ds[0, :, 32, 48, :16], dtype=np.float32)
                fpl = np.asarray(ds[-1, :, 32, 48, :16], dtype=np.float32)
                fingerprints[cand["case_id"]] = (fp0, fpl)
        except Exception:
            pass

    segments: list[dict[str, Any]] = []
    map_bytes = META_TABLE_SIZE + cur_steps * BYTES_PER_STEP
    mm = mmap.mmap(-1, map_bytes, tagname=pool_info["name"], access=mmap.ACCESS_READ)
    try:
        arr = np.ndarray((cur_steps, 6, 64, 96, 192), dtype=np.float32, buffer=mm, offset=META_TABLE_SIZE)
        offset = 0
        used_cases = set()
        while offset < cur_steps:
            pool_fp0 = arr[offset, :, 32, 48, :16]
            matched_cand = None
            for cand in candidates:
                cid = cand["case_id"]
                n_s = int(cand["num_steps"])
                if cid in used_cases or offset + n_s > cur_steps or cid not in fingerprints:
                    continue
                fp0, fpl = fingerprints[cid]
                pool_fpl = arr[offset + n_s - 1, :, 32, 48, :16]
                if np.allclose(pool_fp0, fp0, atol=1e-6) and np.allclose(pool_fpl, fpl, atol=1e-6):
                    matched_cand = cand
                    break
            if matched_cand is None:
                break
            n_s = int(matched_cand["num_steps"])
            used_cases.add(matched_cand["case_id"])
            segments.append({
                "case_id": matched_cand["case_id"],
                "slot_start": offset,
                "slot_end": offset + n_s - 1,
                "num_steps": n_s,
                "reynolds_number": matched_cand["reynolds_number"],
                "u0_m_s": matched_cand["u0_m_s"],
                "cylinder_diameter_m": matched_cand["cylinder_diameter_m"],
                "cond_vector": matched_cand["cond_vector"],
                "fingerprint_verified": True,
            })
            offset += n_s
        del arr
    finally:
        mm.close()
    return segments


def build_manifest(
    dataset_dir: Path = DEFAULT_DATASET_DIR,
    output_path: Path | None = None,
    pool_name: str = DEFAULT_POOL_NAME,
    results_root: Path = DEFAULT_RESULTS_ROOT,
    continuation_root: Path = DEFAULT_CONTINUATION_ROOT,
    include_tests: bool = False,
    verify_finite: bool = True,
    ram_pool_segments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Scan solver ground truth + dataset_dir and emit unified manifest.json."""
    dataset_dir = Path(dataset_dir).resolve()
    if output_path is None:
        output_path = dataset_dir / "manifest.json"
    else:
        output_path = Path(output_path).resolve()

    solver_audit = audit_completed_solvers(
        results_root=results_root,
        continuation_root=continuation_root,
    )
    quarantined_solvers = {
        k: v for k, v in solver_audit.items() if v.get("quarantine_status") is not None
    }

    trajectories: list[dict[str, Any]] = []
    quarantined_trajectories: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    if dataset_dir.exists():
        for h5_path in sorted(dataset_dir.glob("*.h5")):
            if h5_path.name.startswith(".") or h5_path.name.startswith(".tmp_"):
                skipped.append({"file": h5_path.name, "reason": "temporary_file"})
                continue
            if not include_tests and h5_path.name.startswith("test_"):
                skipped.append({"file": h5_path.name, "reason": "test_sample_excluded"})
                continue
            try:
                entry = inspect_h5_trajectory(
                    h5_path, solver_audit=solver_audit, verify_finite=verify_finite
                )
                if entry.get("quarantine_status") is not None:
                    quarantined_trajectories.append(entry)
                    continue
                if entry["num_steps"] < 2:
                    skipped.append({"file": h5_path.name, "reason": f"insufficient_steps({entry['num_steps']})"})
                    continue
                trajectories.append(entry)
            except Exception as exc:
                skipped.append({"file": h5_path.name, "reason": f"read_error:{exc}"})

    pool_info = probe_ram_pool(pool_name=pool_name)

    if ram_pool_segments is None:
        ram_pool_segments = map_ram_pool_segments_by_content(
            pool_info=pool_info,
            trajectories=trajectories,
            dataset_dir=dataset_dir,
            solver_audit=solver_audit,
        )

    pool_info["segments"] = ram_pool_segments

    total_steps = sum(t["num_steps"] for t in trajectories)
    total_pairs = sum(t["num_pairs"] for t in trajectories)
    re_list = sorted({t["reynolds_number"] for t in trajectories})

    manifest = {
        "schema_version": 2,
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "dataset_dir": str(dataset_dir),
        "ground_truth_gate_policy": (
            "Reverse-lookup COMSOL calibration_case_summary.json / run_result.json "
            "for true ReD and U0; reject/quarantine nominal-vs-actual mismatches "
            "(never infer Re from directory names)."
        ),
        "summary": {
            "num_trajectories": len(trajectories),
            "num_quarantined_trajectories": len(quarantined_trajectories),
            "num_quarantined_solvers": len(quarantined_solvers),
            "total_steps": total_steps,
            "total_transition_pairs": total_pairs,
            "reynolds_numbers": re_list,
            "cond_definition": ["log10(Re)", "U0_m_s", "D_m"],
            "native_tensor_layout": "(C, D_z=64, H_y=96, W_x=192)",
            "channels": ["u_star", "v_cross_star", "w_span_star", "p_star", "sdf_star", "solid_mask"],
        },
        "solver_ground_truth_audit": solver_audit,
        "quarantined_solvers": quarantined_solvers,
        "ram_pool": pool_info,
        "trajectories": trajectories,
        "quarantined_trajectories": quarantined_trajectories,
        "skipped": skipped,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_suffix(f".json.tmp.{os.getpid()}")
    tmp_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(str(tmp_path), str(output_path))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate World Model dataset manifest.json with ground-truth gate")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR, help="Directory containing *.h5 trajectories")
    parser.add_argument("--output", type=Path, default=None, help="Output manifest.json path")
    parser.add_argument("--pool-name", type=str, default=DEFAULT_POOL_NAME, help="Active shared memory pool name")
    parser.add_argument("--include-tests", action="store_true", help="Include test_*.h5 files in trajectories")
    parser.add_argument("--no-finite-check", action="store_true", help="Skip finite/NaN sampling check")
    args = parser.parse_args()

    manifest = build_manifest(
        dataset_dir=args.dataset_dir,
        output_path=args.output,
        pool_name=args.pool_name,
        include_tests=args.include_tests,
        verify_finite=not args.no_finite_check,
    )
    out_file = args.output or (args.dataset_dir.resolve() / "manifest.json")
    print(f"[+] Wrote manifest: {out_file}")
    print("[+] Summary:", json.dumps(manifest["summary"], indent=2))
    print("[+] Quarantined solvers:", json.dumps(manifest["quarantined_solvers"], indent=2))
    print("[+] RAM Pool segments:", len(manifest["ram_pool"].get("segments", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
