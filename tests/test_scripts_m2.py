"""Targeted unit tests for Milestone 2 scripts hardening and environmental portability.

Verifies:
- Feature F6: Dynamic drive fallback in dispatch_backfill and calibrate_c04_3d
- Feature F7: Zero-division guards, safe formatting, and dry-run in benchmark_comsol_numa
- Feature F8: Configurable artifacts directory, deferred imports, and dry-run in export_vorticity_videos
- Feature F9: PowerShell script portability, dynamic parameterization, and missing drive tolerance
- Fast execution with zero external dependencies and zero live COMSOL invocations.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


# ---------------------------------------------------------------------------
# Category 1: CLI Invocations (--help across all M2 scripts)
# ---------------------------------------------------------------------------
class TestM2ScriptsCliHelp:
    """Verify that all M2 scripts provide working CLI help without crashing."""

    @pytest.mark.parametrize(
        "script_name,expected_tokens",
        [
            ("dispatch_backfill.py", ["--queue", "--dry-run", "--check-slots", "--drive"]),
            ("calibrate_c04_3d.py", ["prepare", "run", "evaluate", "plan-campaign"]),
            ("benchmark_comsol_numa.py", ["--model", "--ini", "--workload", "--dry-run"]),
            ("export_vorticity_videos.py", ["--artifacts-dir", "--dry-run", "--sequence-dir"]),
        ],
    )
    def test_script_help_exits_zero(self, script_name: str, expected_tokens: list[str]):
        script_path = SCRIPTS_DIR / script_name
        assert script_path.is_file(), f"Script not found: {script_path}"
        res = subprocess.run(
            [sys.executable, str(script_path), "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode == 0, f"{script_name} --help failed: {res.stderr}"
        for token in expected_tokens:
            assert token in res.stdout, f"Token '{token}' missing from {script_name} --help"


# ---------------------------------------------------------------------------
# Category 2: Feature F6 (Dynamic Drive Fallback)
# ---------------------------------------------------------------------------
class TestFeatureF6DriveFallback:
    """Verify dynamic drive fallback in dispatch_backfill and calibrate_c04_3d."""

    def test_get_system_health_default_safe(self):
        import dispatch_backfill as db

        health = db.get_system_health()
        assert isinstance(health, dict)
        assert "free_ram_gb" in health
        assert "total_ram_gb" in health
        assert "free_disk_e_gb" in health
        assert "free_disk_gb" in health
        assert health["free_ram_gb"] >= 0.0
        assert health["total_ram_gb"] >= 0.0
        assert health["free_disk_e_gb"] >= 0.0
        assert health["free_disk_gb"] >= 0.0
        assert health["free_disk_e_gb"] == health["free_disk_gb"]

    def test_get_system_health_nonexistent_drive_fallback(self):
        import dispatch_backfill as db

        health = db.get_system_health(drive="NON_EXISTENT_XYZ:\\")
        assert isinstance(health, dict)
        assert health["free_disk_e_gb"] >= 0.0
        assert health["free_disk_gb"] >= 0.0

    @pytest.mark.parametrize("required_disk_gb", [0.0, 20.0])
    def test_disk_probe_failure_blocks_dispatch(self, monkeypatch, tmp_path, required_disk_gb):
        import dispatch_backfill as db

        def unavailable_disk(_path):
            raise OSError("Disk capacity unavailable")

        monkeypatch.setattr(db.shutil, "disk_usage", unavailable_disk)
        monkeypatch.setattr(db, "list_live_comsol_processes", lambda: [])
        monkeypatch.setattr(
            db.subprocess, "Popen", lambda *a, **kw: pytest.fail("Job must not launch")
        )
        output_dir = tmp_path / "output"
        queue_path = tmp_path / "queue.json"
        queue_path.write_text(json.dumps({"queue": [{
            "job_id": "disk-check",
            "status": "READY",
            "case_id": "test_case",
            "out_dir": str(output_dir),
            "min_free_ram_gb": 0.0,
            "min_free_disk_e_gb": required_disk_gb,
        }]}), encoding="utf-8")

        result = db.dispatch_one_job(queue_path=queue_path, drive=tmp_path)

        assert result["status"] == "NO_ELIGIBLE_JOB"
        assert result["health"]["free_disk_gb"] == 0.0
        assert result["health"]["free_disk_e_gb"] == 0.0
        assert result["skipped"][0]["reason"].startswith("insufficient_disk")
        assert not output_dir.exists()

    def test_get_system_health_embedded_null_byte_safe(self):
        """Verify get_system_health survives embedded null byte in drive path."""
        import dispatch_backfill as db

        health = db.get_system_health("\x00")
        assert isinstance(health, dict)
        assert health["free_disk_gb"] >= 0.0
        assert health["free_disk_e_gb"] == health["free_disk_gb"]

    def test_get_system_health_malformed_inputs_safe(self):
        """Verify get_system_health survives malformed types and strings."""
        import dispatch_backfill as db

        for bad in ["\x00", "C:\\\x00", b"\x00", 12345]:
            health = db.get_system_health(bad)  # type: ignore[arg-type]
            assert isinstance(health, dict)
            assert health["free_disk_gb"] >= 0.0

    def test_resolve_cfd_drive_precedence(self, monkeypatch, tmp_path: Path):
        import dispatch_backfill as db

        # 1. Explicit argument takes top priority
        explicit = tmp_path / "explicit"
        assert db.resolve_cfd_drive(explicit) == explicit

        # 2. HYDRO_CFD_DRIVE env var takes second priority
        env_dir = tmp_path / "env_drive"
        monkeypatch.setenv("HYDRO_CFD_DRIVE", str(env_dir))
        assert db.resolve_cfd_drive() == env_dir

        # 3. Default fallback when env var unset
        monkeypatch.delenv("HYDRO_CFD_DRIVE", raising=False)
        resolved = db.resolve_cfd_drive()
        assert isinstance(resolved, Path)
        if Path("E:\\").exists():
            assert resolved == Path("E:\\")
        else:
            assert resolved == Path(Path.cwd().anchor or ".")

    def test_calibrate_c04_3d_resolve_cfd_drive(self):
        import calibrate_c04_3d as c04cal

        drive = c04cal.resolve_cfd_drive()
        assert isinstance(drive, Path)
        assert drive.exists() or drive.anchor == Path.cwd().anchor

    def test_calibrate_c04_3d_resolve_default_source_s0_mesh2_safe(self):
        import calibrate_c04_3d as c04cal

        p = c04cal.resolve_default_source_s0_mesh2()
        assert isinstance(p, Path)


# ---------------------------------------------------------------------------
# Category 3: Feature F7 (NUMA Benchmark Zero-Division Guards & Dry-Run)
# ---------------------------------------------------------------------------
class TestFeatureF7NumaSpeedupGuards:
    """Verify that NUMA benchmark speedup calculations cannot crash with ZeroDivisionError."""

    def test_speedup_normal_case(self):
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "np16_numa1", "ok": True, "wall_s": 100.0, "mean_solve_s": 50.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "np32_numa2", "ok": True, "wall_s": 50.0, "mean_solve_s": 25.0, "throughput_jobs_per_hr": 20.0},
        ]
        res = bcn.compute_speedups(results)
        assert res[0]["single_job_speedup"] == 1.0
        assert res[0]["throughput_speedup"] == 1.0
        assert res[1]["single_job_speedup"] == 2.0
        assert res[1]["throughput_speedup"] == 2.0

    def test_speedup_zero_mean_solve_protection(self):
        """Verify microsecond solve (mean_solve_s=0.0) does not cause ZeroDivisionError."""
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "base", "ok": True, "wall_s": 100.0, "mean_solve_s": 50.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "fast", "ok": True, "wall_s": 1.0, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 100.0},
        ]
        res = bcn.compute_speedups(results)
        assert res[1]["single_job_speedup"] == 0.0
        assert res[1]["throughput_speedup"] == 10.0

    def test_speedup_zero_baseline_throughput_protection(self):
        """Verify base with 0.0 throughput does not cause ZeroDivisionError."""
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "base", "ok": True, "wall_s": 100.0, "mean_solve_s": 50.0, "throughput_jobs_per_hr": 0.0},
            {"profile": "run2", "ok": True, "wall_s": 50.0, "mean_solve_s": 25.0, "throughput_jobs_per_hr": 20.0},
        ]
        res = bcn.compute_speedups(results)
        assert res[0]["throughput_speedup"] == 1.0
        assert res[1]["throughput_speedup"] == 0.0

    def test_speedup_all_failed(self):
        """Verify all-failed profiles safely default to 0.0."""
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "fail1", "ok": False, "wall_s": 10.0, "mean_solve_s": 50.0, "throughput_jobs_per_hr": 10.0},
            {"profile": "fail2", "ok": False, "wall_s": 10.0, "mean_solve_s": 25.0, "throughput_jobs_per_hr": 20.0},
        ]
        res = bcn.compute_speedups(results)
        assert res[0]["single_job_speedup"] == 0.0
        assert res[0]["throughput_speedup"] == 0.0
        assert res[1]["single_job_speedup"] == 0.0
        assert res[1]["throughput_speedup"] == 0.0

    def test_speedup_empty_results(self):
        import benchmark_comsol_numa as bcn

        assert bcn.compute_speedups([]) == []

    def test_speedup_alias_identity(self):
        import benchmark_comsol_numa as bcn

        assert bcn.compute_numa_speedups is bcn.compute_speedups

    def test_numa_benchmark_dry_run_cli(self, capsys):
        """Verify --dry-run executes cleanly with returncode 0 without COMSOL."""
        import benchmark_comsol_numa as bcn

        rc = bcn.main(["--dry-run", "--profiles", "np16_numa1,np32_numa2"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[DRY-RUN]" in captured.out
        assert "np16_numa1" in captured.out
        assert "np32_numa2" in captured.out

    def test_safe_float_comprehensive(self):
        """Verify _safe_float coercion across valid and invalid edge cases."""
        import benchmark_comsol_numa as bcn

        assert bcn._safe_float(None) == 0.0
        assert bcn._safe_float(None, 42.0) == 42.0
        assert bcn._safe_float(True) == 0.0
        assert bcn._safe_float(False) == 0.0
        assert bcn._safe_float("12.34") == 12.34
        assert bcn._safe_float(10) == 10.0
        assert bcn._safe_float("N/A") == 0.0
        assert bcn._safe_float("N/A", -1.0) == -1.0
        assert bcn._safe_float("-") == 0.0
        assert bcn._safe_float(float("nan")) == 0.0
        assert bcn._safe_float(float("inf")) == 0.0
        assert bcn._safe_float(float("-inf")) == 0.0
        assert bcn._safe_float([1, 2, 3]) == 0.0
        assert bcn._safe_float({"a": 1}) == 0.0

    def test_speedup_none_and_non_numeric_strings(self):
        """Verify compute_speedups survives None and non-numeric placeholder strings."""
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "p1", "ok": True, "wall_s": None, "mean_solve_s": 10.0, "throughput_jobs_per_hr": 1.0},
            {"profile": "p2", "ok": True, "wall_s": 10.0, "mean_solve_s": "N/A", "throughput_jobs_per_hr": "N/A"},
            {"profile": "p3", "ok": True, "wall_s": "timeout", "mean_solve_s": 5.0, "throughput_jobs_per_hr": 2.0},
        ]
        out = bcn.compute_speedups(results)
        assert len(out) == 3
        assert out[0]["single_job_speedup"] == 0.0
        assert out[1]["single_job_speedup"] == 0.0
        assert out[2]["single_job_speedup"] == 0.0


# ---------------------------------------------------------------------------
# Category 3b: Feature F7 (Markdown Table Formatting Robustness)
# ---------------------------------------------------------------------------
class TestFeatureF7MarkdownTableFormatting:
    """Verify render_markdown_table_rows handles edge cases, None values, and missing keys."""

    def test_render_markdown_table_rows_normal(self):
        import benchmark_comsol_numa as bcn

        rows = [
            {
                "profile": "p1",
                "n_workers": 2,
                "wall_s": 100.5,
                "mean_solve_s": 50.25,
                "single_job_speedup": 1.5,
                "throughput_jobs_per_hr": 12.0,
                "throughput_speedup": 1.2,
                "system_telemetry": {"avg_cpu_pct": 85.0, "max_cpu_pct": 98.0, "peak_sys_used_ram_gb": 32.5},
                "workers": [{"peak_phys_gb": 12.0, "peak_virt_gb": 16.0}],
            }
        ]
        lines = bcn.render_markdown_table_rows(rows)
        assert len(lines) == 1
        assert "| `p1` | 2 | 100.50 | 50.25 | 1.50x | 12.00 (1.20x) | 85.0/98.0 | 32.5 | 12.00 / 16.00 |" == lines[0]

    def test_render_markdown_table_rows_none_values(self):
        import benchmark_comsol_numa as bcn

        rows = [
            {
                "profile": "p_none",
                "wall_s": None,
                "mean_solve_s": None,
                "throughput_jobs_per_hr": None,
                "single_job_speedup": None,
                "throughput_speedup": None,
                "system_telemetry": None,
                "workers": None,
            }
        ]
        lines = bcn.render_markdown_table_rows(rows)
        assert len(lines) == 1
        assert "| `p_none` | 0 | 0.00 | 0.00 | 0.00x | 0.00 (0.00x) | -/- | - | 0.00 / 0.00 |" == lines[0]

    def test_render_markdown_table_rows_missing_keys_and_empty_dict(self):
        import benchmark_comsol_numa as bcn

        rows = [{}]
        lines = bcn.render_markdown_table_rows(rows)
        assert len(lines) == 1
        assert "| `unknown` | 0 | 0.00 | 0.00 | 0.00x | 0.00 (0.00x) | -/- | - | 0.00 / 0.00 |" == lines[0]

    def test_render_markdown_table_rows_string_placeholders_and_nan(self):
        import benchmark_comsol_numa as bcn

        rows = [
            {
                "profile": "p_bad",
                "wall_s": "N/A",
                "mean_solve_s": "-",
                "throughput_jobs_per_hr": float("nan"),
                "workers": [{"peak_phys_gb": "bad", "peak_virt_gb": float("inf")}],
                "system_telemetry": {"avg_cpu_pct": None, "max_cpu_pct": None, "peak_sys_used_ram_gb": None},
            }
        ]
        lines = bcn.render_markdown_table_rows(rows)
        assert len(lines) == 1
        assert "| `p_bad` | 0 | 0.00 | 0.00 | 0.00x | 0.00 (0.00x) | -/- | - | 0.00 / 0.00 |" == lines[0]

    def test_render_benchmark_markdown_table_alias(self):
        import benchmark_comsol_numa as bcn

        assert bcn.render_benchmark_markdown_table is bcn.render_markdown_table_rows


# ---------------------------------------------------------------------------
# Category 4: Feature F8 (Configurable Video Export Artifacts Directory)
# ---------------------------------------------------------------------------
class TestFeatureF8VorticityVideoConfig:
    """Verify configurable artifacts directory in export_vorticity_videos."""

    def test_resolve_artifacts_dir_cli_argument(self, tmp_path: Path):
        import export_vorticity_videos as evv

        custom_dir = tmp_path / "custom_artifacts"
        resolved = evv.resolve_artifacts_dir(str(custom_dir))
        assert resolved == custom_dir

    def test_resolve_artifacts_dir_env_var(self, tmp_path: Path, monkeypatch):
        import export_vorticity_videos as evv

        custom_env = tmp_path / "env_artifacts"
        monkeypatch.setenv("HYDRO_ARTIFACTS_DIR", str(custom_env))
        resolved = evv.resolve_artifacts_dir(None)
        assert resolved == custom_env

    def test_resolve_artifacts_dir_antigravity_fallback(self, tmp_path: Path, monkeypatch):
        import export_vorticity_videos as evv

        monkeypatch.delenv("HYDRO_ARTIFACTS_DIR", raising=False)
        custom_anti = tmp_path / "antigravity_artifacts"
        monkeypatch.setenv("ANTIGRAVITY_ARTIFACTS_DIR", str(custom_anti))
        resolved = evv.resolve_artifacts_dir(None)
        assert resolved == custom_anti

    def test_resolve_artifacts_dir_no_hardcoded_session_uuid(self, monkeypatch):
        import export_vorticity_videos as evv

        monkeypatch.delenv("HYDRO_ARTIFACTS_DIR", raising=False)
        monkeypatch.delenv("ANTIGRAVITY_ARTIFACTS_DIR", raising=False)
        resolved = evv.resolve_artifacts_dir(None)
        assert resolved is None

    @pytest.mark.artifact
    def test_export_vorticity_videos_cli_dry_run(self, capsys):
        import export_vorticity_videos as evv

        rc = evv.main(["--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "[DRY RUN]" in captured.out
        assert "Pre-flight checks passed successfully." in captured.out

    def test_export_vorticity_videos_missing_seq_dir(self, tmp_path: Path, capsys):
        import export_vorticity_videos as evv

        nonexistent = tmp_path / "does_not_exist"
        rc = evv.main(["--sequence-dir", str(nonexistent)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Sequence directory not found" in captured.err


# ---------------------------------------------------------------------------
# Category 5: Feature F9 (PowerShell Script Portability)
# ---------------------------------------------------------------------------
class TestFeatureF9PowerShellPortability:
    """Verify elimination of hardcoded paths and presence of dynamic parameters in PowerShell scripts."""

    def test_recapture_script_no_hardcoded_python(self):
        script_text = (SCRIPTS_DIR / "recapture_s1b3_tests.ps1").read_text(encoding="utf-8")
        assert '$py = "D:\\Program Files\\Anaconda\\envs\\seagent\\python.exe"' not in script_text
        assert "param(" in script_text
        assert "PythonExe" in script_text
        assert "RepoRoot" in script_text

    def test_setup_e_workspace_no_hardcoded_repo(self):
        script_text = (SCRIPTS_DIR / "setup_e_workspace.ps1").read_text(encoding="utf-8")
        assert '[string]$RepoRoot = "D:\\HydroDisturb-Bench"' not in script_text
        assert "PSScriptRoot" in script_text or "HYDRODISTURB_BENCH_ROOT" in script_text

    def test_setup_e_workspace_no_raw_get_psdrive_crash(self):
        script_text = (SCRIPTS_DIR / "setup_e_workspace.ps1").read_text(encoding="utf-8")
        assert "Get-PSDrive C, D, E" not in script_text
        assert "TargetDrive" in script_text
        assert "driveAvailable" in script_text

    @pytest.mark.skipif(
        shutil.which("powershell") is None and shutil.which("pwsh") is None,
        reason="PowerShell not available on this platform",
    )
    def test_powershell_scripts_syntax_check(self):
        ps_cmd = shutil.which("powershell") or shutil.which("pwsh")
        for ps_script in ["recapture_s1b3_tests.ps1", "setup_e_workspace.ps1"]:
            path = SCRIPTS_DIR / ps_script
            script_path = str(path).replace("'", "''")
            command = (
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                f"Get-Command '{script_path}' -Syntax"
            )
            res = subprocess.run(
                [ps_cmd, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            assert res.returncode == 0, f"Syntax verification failed for {ps_script}: {res.stderr}"

    @pytest.mark.skipif(
        shutil.which("powershell") is None and shutil.which("pwsh") is None,
        reason="PowerShell not available on this platform",
    )
    def test_setup_e_workspace_execution_without_e_drive(self):
        ps_cmd = shutil.which("powershell") or shutil.which("pwsh")
        path = SCRIPTS_DIR / "setup_e_workspace.ps1"
        target_drive = next(
            (f"{letter}:" for letter in "ZYXWVUTSRQPONMLKJIHGFEDCBA" if not Path(f"{letter}:\\").exists()),
            None,
        )
        if target_drive is None:
            pytest.skip("No unmounted drive is available for an isolated setup check")
        script_path = str(path).replace("'", "''")
        command = (
            "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
            f"if (Test-Path -LiteralPath '{target_drive}\\') "
            "{ throw 'Test drive unexpectedly mounted; refusing setup' }; "
            f"& '{script_path}' -TargetDrive '{target_drive}'"
        )
        res = subprocess.run(
            [ps_cmd, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        assert res.returncode == 0, f"setup_e_workspace.ps1 failed: {res.stderr}"
        assert "[SKIPPED] Directory creation skipped" in res.stdout
        assert "[SKIPPED] comsolbatch_perf.ini generation skipped" in res.stdout
        assert "[CREATED]" not in res.stdout
        assert "[READY]" not in res.stdout
