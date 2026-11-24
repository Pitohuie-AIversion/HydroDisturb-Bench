"""Adversarial stress testing suite for Milestone 2 (Scripts Robustness & Environmental Hardening).

Authored by Challenger 1 (teamwork_challenger_m2_1).
Role: Empirical Challenger (critic, specialist).

Focus:
1. Feature F7: NUMA benchmark speedup computation under extreme edge cases:
   - 0.0s solve times, microsecond solves (1e-9), negative solve times, zero baseline throughput.
   - Missing keys, empty results, all-failed runs.
   - None values and non-numeric string values.
   - Markdown table formatting resilience.
2. Feature F6: Dynamic drive fallback in dispatch_backfill and calibrate_c04_3d:
   - Non-existent drives, invalid path characters, UNC paths, root paths, embedded null bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import benchmark_comsol_numa as bcn
import calibrate_c04_3d as c04cal
import dispatch_backfill as db


# ===========================================================================
# 1. Feature F7: NUMA Speedup Computation Stress Tests
# ===========================================================================
class TestNumaSpeedupAdversarial:
    """Stress tests for compute_speedups in scripts/benchmark_comsol_numa.py."""

    def test_speedup_microsecond_solves(self):
        """Microsecond and sub-microsecond solve times should not cause ZeroDivisionError."""
        results = [
            {"profile": "base", "ok": True, "wall_s": 10.0, "mean_solve_s": 1.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "micro", "ok": True, "wall_s": 0.001, "mean_solve_s": 1e-6, "throughput_jobs_per_hr": 1000.0},
            {"profile": "nano", "ok": True, "wall_s": 0.00001, "mean_solve_s": 1e-9, "throughput_jobs_per_hr": 50000.0},
        ]
        out = bcn.compute_speedups(results)
        assert out[0]["single_job_speedup"] == 1.0
        assert out[0]["throughput_speedup"] == 1.0
        assert out[1]["single_job_speedup"] == pytest.approx(1.0 / 1e-6)
        assert out[2]["single_job_speedup"] == pytest.approx(1.0 / 1e-9)

    def test_speedup_zero_solve_times_in_base_and_runs(self):
        """Solve times of 0.0s must safely yield speedup without division by zero."""
        # Case A: Non-base has 0.0 solve time
        results_a = [
            {"profile": "base", "ok": True, "wall_s": 10.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "zero_solve", "ok": True, "wall_s": 1.0, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 50.0},
        ]
        out_a = bcn.compute_speedups(results_a)
        assert out_a[0]["single_job_speedup"] == 1.0
        assert out_a[1]["single_job_speedup"] == 0.0

        # Case B: Base itself has 0.0 solve time
        results_b = [
            {"profile": "zero_base", "ok": True, "wall_s": 10.0, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "run2", "ok": True, "wall_s": 5.0, "mean_solve_s": 2.5, "throughput_jobs_per_hr": 20.0},
        ]
        out_b = bcn.compute_speedups(results_b)
        assert out_b[0]["single_job_speedup"] == 1.0
        assert out_b[1]["single_job_speedup"] == 0.0

    def test_speedup_negative_values(self):
        """Negative solve times, negative wall times, and negative throughput must be handled gracefully."""
        results = [
            {"profile": "base", "ok": True, "wall_s": 10.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "neg_solve", "ok": True, "wall_s": 10.0, "mean_solve_s": -5.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "neg_wall", "ok": True, "wall_s": -10.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "neg_tput", "ok": True, "wall_s": 10.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": -10.0},
        ]
        out = bcn.compute_speedups(results)
        assert out[0]["single_job_speedup"] == 1.0
        assert out[1]["single_job_speedup"] == 0.0
        assert out[2]["single_job_speedup"] == 0.0
        assert out[2]["throughput_speedup"] == 0.0
        assert out[3]["throughput_speedup"] == 0.0

    def test_speedup_zero_baseline_throughput(self):
        """Zero baseline throughput must not raise ZeroDivisionError for subsequent profiles."""
        results = [
            {"profile": "base", "ok": True, "wall_s": 10.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": 0.0},
            {"profile": "fast", "ok": True, "wall_s": 5.0, "mean_solve_s": 2.5, "throughput_jobs_per_hr": 20.0},
        ]
        out = bcn.compute_speedups(results)
        assert out[0]["throughput_speedup"] == 1.0
        assert out[1]["throughput_speedup"] == 0.0

    def test_speedup_empty_and_all_failed(self):
        """Empty list or all-failed benchmark runs must not crash."""
        assert bcn.compute_speedups([]) == []

        all_fail = [
            {"profile": "f1", "ok": False, "wall_s": 5.0, "mean_solve_s": 5.0, "throughput_jobs_per_hr": 0.0},
            {"profile": "f2", "ok": False, "wall_s": 0.0, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 0.0},
        ]
        out = bcn.compute_speedups(all_fail)
        for row in out:
            assert row["single_job_speedup"] == 0.0
            assert row["throughput_speedup"] == 0.0

    def test_speedup_missing_dictionary_keys(self):
        """Dictionaries missing some or all keys should default safely without crashing."""
        sparse = [
            {},
            {"ok": True},
            {"profile": "p1", "ok": True},
        ]
        out = bcn.compute_speedups(sparse)
        assert len(out) == 3
        for row in out:
            assert "single_job_speedup" in row
            assert "throughput_speedup" in row

    def test_speedup_none_wall_s_failure_mode(self):
        """ADVERSARIAL: Explicit wall_s=None must be safely handled without TypeError."""
        results = [
            {"profile": "p1", "ok": True, "wall_s": None, "mean_solve_s": 10.0, "throughput_jobs_per_hr": 1.0}
        ]
        out = bcn.compute_speedups(results)
        assert out[0]["single_job_speedup"] == 0.0
        assert out[0]["throughput_speedup"] == 0.0

    def test_speedup_non_numeric_strings_failure_mode(self):
        """ADVERSARIAL: Non-numeric strings in mean_solve_s or wall_s must not raise ValueError."""
        results = [
            {"profile": "p1", "ok": True, "wall_s": 10.0, "mean_solve_s": "N/A", "throughput_jobs_per_hr": 1.0}
        ]
        out = bcn.compute_speedups(results)
        assert out[0]["single_job_speedup"] == 1.0
        assert out[0]["throughput_speedup"] == 1.0


# ===========================================================================
# 2. Markdown Table Generation Robustness
# ===========================================================================
class TestMarkdownTableFormattingAdversarial:
    """Stress tests reproducing the Markdown table generation logic in benchmark_comsol_numa.py."""

    @staticmethod
    def _render_md_rows(results: list[dict[str, Any]]) -> list[str]:
        """Delegates directly to benchmark_comsol_numa.render_markdown_table_rows."""
        return bcn.render_markdown_table_rows(results)

    def test_markdown_with_empty_workers_and_missing_telemetry(self):
        """Empty workers list and None telemetry format without crashing."""
        row = {
            "profile": "np16_test",
            "n_workers": 1,
            "wall_s": 12.34,
            "mean_solve_s": 10.0,
            "throughput_jobs_per_hr": 2.5,
            "single_job_speedup": 1.0,
            "throughput_speedup": 1.0,
            "system_telemetry": None,
            "workers": [],
        }
        lines = self._render_md_rows([row])
        assert len(lines) == 1
        assert "| `np16_test` | 1 | 12.34 | 10.00 | 1.00x | 2.50 (1.00x) | -/- | - | 0.00 / 0.00 |" == lines[0]

    def test_markdown_with_none_speedup_values(self):
        """None speedup values coerce to 0.00x."""
        row = {
            "profile": "failed_run",
            "n_workers": 1,
            "wall_s": 5.0,
            "mean_solve_s": 0.0,
            "throughput_jobs_per_hr": 0.0,
            "single_job_speedup": None,
            "throughput_speedup": None,
            "system_telemetry": {},
            "workers": [],
        }
        lines = self._render_md_rows([row])
        assert "0.00x" in lines[0]

    def test_markdown_none_wall_s_failure_mode(self):
        """ADVERSARIAL: When wall_s=None, markdown row rendering safely formats to 0.00."""
        row = {
            "profile": "p_none",
            "wall_s": None,
            "mean_solve_s": None,
            "throughput_jobs_per_hr": None,
        }
        lines = self._render_md_rows([row])
        assert len(lines) == 1
        assert "| `p_none` | 0 | 0.00 | 0.00 | 0.00x | 0.00 (0.00x) | -/- | - | 0.00 / 0.00 |" == lines[0]


# ===========================================================================
# 3. Feature F6: Dynamic Drive Fallback & Health Check Stress Tests
# ===========================================================================
class TestDriveFallbackAdversarial:
    """Stress tests for get_system_health and resolve_cfd_drive."""

    def test_get_system_health_nonexistent_drive_fallback(self):
        """Non-existent drive letters must fall back cleanly without FileNotFoundError."""
        for bad_drive in ["Z:\\", "Q:\\cfd", "NON_EXISTENT:\\dir\\subdir"]:
            h = db.get_system_health(bad_drive)
            assert isinstance(h, dict)
            assert h["free_disk_gb"] >= 0.0
            assert h["free_disk_e_gb"] == h["free_disk_gb"]

    def test_get_system_health_invalid_chars(self):
        """Invalid Windows path characters must be caught by OSError and fall back."""
        bad_paths = [
            "C:\\invalid<?*>:|",
            "D:\\<>:\"/\\|?*",
            "Z:\\nul\\con\\aux",
        ]
        for p in bad_paths:
            h = db.get_system_health(p)
            assert isinstance(h, dict)
            assert h["free_disk_gb"] >= 0.0

    def test_get_system_health_unc_path(self):
        """Unreachable network UNC path must fall back without crashing."""
        unc = r"\\nonexistent_server_12345\share"
        h = db.get_system_health(unc)
        assert isinstance(h, dict)
        assert h["free_disk_gb"] >= 0.0

    def test_get_system_health_root_and_empty(self):
        """Root paths and empty string must resolve safely."""
        for p in ["/", "\\", ""]:
            h = db.get_system_health(p)
            assert isinstance(h, dict)
            assert h["free_disk_gb"] >= 0.0

    def test_get_system_health_null_byte_failure_mode(self):
        """ADVERSARIAL: An embedded null byte must be caught safely and fall back."""
        h = db.get_system_health("\x00")
        assert isinstance(h, dict)
        assert h["free_disk_gb"] >= 0.0
        assert h["free_disk_e_gb"] == h["free_disk_gb"]

    def test_calibrate_c04_3d_resolve_default_source_s0_mesh2(self):
        """Verify resolve_default_source_s0_mesh2 survives nonexistent drives."""
        p = c04cal.resolve_default_source_s0_mesh2()
        assert isinstance(p, Path)
        # Should return a Path without throwing an uncaught exception
