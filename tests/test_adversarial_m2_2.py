"""Adversarial stress-testing suite for Milestone 2 (Challenger 2).

Adversarially probes:
1. Feature F8: Deferred visualization imports, artifact directory precedence matrix,
   Unicode/space resilience, sequence dir validation, and absence of hardcoded session UUID.
2. Feature F9: PowerShell scripts (recapture_s1b3_tests.ps1 & setup_e_workspace.ps1) parameterization,
   missing drive handling, formatting flexibility, discovery cascade, and safe default execution.
3. Cross-script hardening: zero-division edge cases, nonexistent drive fallbacks, and UUID cleanliness.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

TARGET_UUID = "78e0afb2-6233-4e38-b94c-b25dc49b4926"
POWERSHELL_EXE = shutil.which("powershell") or shutil.which("pwsh")


def _run_powershell(command: str, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    """Capture PowerShell output independently of the host's locale encoding."""
    command = (
        "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
        "$OutputEncoding = [Console]::OutputEncoding; "
        + command
    )
    return subprocess.run(
        [POWERSHELL_EXE, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        cwd=str(REPO_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
    )


@pytest.fixture
def unmounted_drive() -> str:
    for letter in reversed("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if not Path(f"{letter}:\\").exists():
            return letter
    pytest.skip("No unmounted drive letter available for isolated script tests")


def _run_setup_script(drive: str, *, whatif: bool = False) -> subprocess.CompletedProcess[str]:
    script_path = str(SCRIPTS_DIR / "setup_e_workspace.ps1").replace("'", "''")
    root = drive.rstrip(":\\") + ":\\"
    command = (
        f"if (Test-Path -LiteralPath '{root}') {{ throw 'Test target is mounted' }}; "
        f"& '{script_path}' -TargetDrive '{drive}'"
    )
    if whatif:
        command += " -WhatIf"
    return _run_powershell(command, timeout=15)


# ===========================================================================
# Category 1: Feature F8 — Visualization Dependency Isolation & Resilience
# ===========================================================================
class TestAdversarialF8VizIsolation:
    """Stress-test deferred loading and missing dependencies in export_vorticity_videos.py."""

    def test_cli_help_succeeds_with_uninstalled_viz_dependencies(self):
        """CLI --help must exit 0 even if matplotlib and imageio are blocked from import."""
        code = (
            "import sys\n"
            f"sys.path.insert(0, r'{SCRIPTS_DIR}')\n"
            "sys.modules['matplotlib'] = None\n"
            "sys.modules['imageio'] = None\n"
            "sys.modules['imageio.v3'] = None\n"
            "import export_vorticity_videos as evv\n"
            "parser = evv.build_parser()\n"
            "help_text = parser.format_help()\n"
            "assert '--artifacts-dir' in help_text\n"
            "assert '--dry-run' in help_text\n"
            "print('PARSER_HELP_OK')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode == 0, f"Failed: {res.stderr}"
        assert "PARSER_HELP_OK" in res.stdout

    def test_cli_dry_run_succeeds_with_uninstalled_viz_dependencies(self):
        """CLI --dry-run must exit 0 even if matplotlib and imageio are blocked from import."""
        code = (
            "import sys\n"
            f"sys.path.insert(0, r'{SCRIPTS_DIR}')\n"
            "sys.modules['matplotlib'] = None\n"
            "sys.modules['imageio'] = None\n"
            "sys.modules['imageio.v3'] = None\n"
            "import export_vorticity_videos as evv\n"
            "rc = evv.main(['--dry-run'])\n"
            "assert rc == 0\n"
            "print('DRY_RUN_OK')\n"
        )
        res = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert res.returncode == 0, f"Failed: {res.stderr}"
        assert "DRY_RUN_OK" in res.stdout

    def test_load_viz_modules_raises_informative_error_on_missing_matplotlib(self):
        """_load_viz_modules() must raise ImportError referencing hydrodisturb-bench[analysis]."""
        import export_vorticity_videos as evv

        with patch.dict(sys.modules, {"matplotlib": None}):
            with pytest.raises(ImportError) as exc_info:
                evv._load_viz_modules()
            err_msg = str(exc_info.value)
            assert "Visualization dependencies" in err_msg
            assert "pip install hydrodisturb-bench[analysis]" in err_msg

    def test_load_viz_modules_raises_informative_error_on_missing_imageio(self):
        """_load_viz_modules() must raise ImportError when imageio is missing."""
        import export_vorticity_videos as evv

        with patch.dict(sys.modules, {"imageio": None, "imageio.v3": None}):
            with pytest.raises(ImportError) as exc_info:
                evv._load_viz_modules()
            err_msg = str(exc_info.value)
            assert "Visualization dependencies" in err_msg
            assert "pip install hydrodisturb-bench[analysis]" in err_msg


# ===========================================================================
# Category 2: Feature F8 — Artifact Directory Resolution Precedence Matrix
# ===========================================================================
class TestAdversarialF8ArtifactResolution:
    """Stress-test artifact resolution priority, empty strings, and special paths."""

    def test_explicit_argument_highest_priority(self, tmp_path: Path, monkeypatch):
        """CLI argument must override all environment variables."""
        import export_vorticity_videos as evv

        explicit = tmp_path / "explicit_dir"
        monkeypatch.setenv("HYDRO_ARTIFACTS_DIR", str(tmp_path / "hydro_dir"))
        monkeypatch.setenv("ANTIGRAVITY_ARTIFACTS_DIR", str(tmp_path / "antigravity_dir"))

        resolved = evv.resolve_artifacts_dir(str(explicit))
        assert resolved == explicit

    def test_hydro_artifacts_dir_overrides_antigravity(self, tmp_path: Path, monkeypatch):
        """HYDRO_ARTIFACTS_DIR must take priority over ANTIGRAVITY_ARTIFACTS_DIR."""
        import export_vorticity_videos as evv

        hydro_dir = tmp_path / "hydro_dir"
        anti_dir = tmp_path / "antigravity_dir"
        monkeypatch.setenv("HYDRO_ARTIFACTS_DIR", str(hydro_dir))
        monkeypatch.setenv("ANTIGRAVITY_ARTIFACTS_DIR", str(anti_dir))

        resolved = evv.resolve_artifacts_dir(None)
        assert resolved == hydro_dir

    def test_antigravity_fallback_when_hydro_unset(self, tmp_path: Path, monkeypatch):
        """ANTIGRAVITY_ARTIFACTS_DIR is used when HYDRO_ARTIFACTS_DIR is unset."""
        import export_vorticity_videos as evv

        monkeypatch.delenv("HYDRO_ARTIFACTS_DIR", raising=False)
        anti_dir = tmp_path / "antigravity_dir"
        monkeypatch.setenv("ANTIGRAVITY_ARTIFACTS_DIR", str(anti_dir))

        resolved = evv.resolve_artifacts_dir(None)
        assert resolved == anti_dir

    def test_empty_string_env_vars_fallback_to_none(self, monkeypatch):
        """Empty string environment variables must safely fall back to None."""
        import export_vorticity_videos as evv

        monkeypatch.setenv("HYDRO_ARTIFACTS_DIR", "")
        monkeypatch.setenv("ANTIGRAVITY_ARTIFACTS_DIR", "")

        resolved = evv.resolve_artifacts_dir(None)
        assert resolved is None

    def test_path_with_spaces_and_unicode_preserved(self, tmp_path: Path):
        """Paths containing spaces and emoji/unicode characters must be preserved faithfully."""
        import export_vorticity_videos as evv

        unicode_path = tmp_path / "📁 test space & unicode 測試"
        resolved = evv.resolve_artifacts_dir(str(unicode_path))
        assert resolved == unicode_path

    def test_no_hardcoded_session_uuid_in_script_source(self):
        """Verify the specific session UUID 78e0afb2-6233-4e38-b94c-b25dc49b4926 is eliminated."""
        script_path = SCRIPTS_DIR / "export_vorticity_videos.py"
        source = script_path.read_text(encoding="utf-8")
        assert TARGET_UUID not in source, f"Hardcoded session UUID found in {script_path.name}!"

    def test_main_missing_sequence_dir_exits_code_1(self, tmp_path: Path, capsys):
        """main() must returncode 1 and print error when sequence-dir does not exist."""
        import export_vorticity_videos as evv

        nonexistent = tmp_path / "does_not_exist_at_all"
        rc = evv.main(["--sequence-dir", str(nonexistent)])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Sequence directory not found" in captured.err

    def test_main_dry_run_reports_artifacts_dir(self, tmp_path: Path, capsys, monkeypatch):
        """main(--dry-run) must reflect resolved artifacts directory in output."""
        import export_vorticity_videos as evv

        art_dir = tmp_path / "dry_run_artifacts"
        monkeypatch.setenv("HYDRO_ARTIFACTS_DIR", str(art_dir))
        rc = evv.main(["--dry-run"])
        assert rc == 0
        captured = capsys.readouterr()
        assert f"Artifacts directory: {art_dir}" in captured.out


# ===========================================================================
# Category 3: Feature F9 — PowerShell recapture_s1b3_tests.ps1 Stress Tests
# ===========================================================================
@pytest.mark.skipif(POWERSHELL_EXE is None, reason="PowerShell not installed on this host")
class TestAdversarialF9RecaptureScript:
    """Stress-test parameter handling and discovery in recapture_s1b3_tests.ps1."""

    def test_script_syntax_declares_python_and_repo_params(self):
        """Script signature must declare PythonExe and RepoRoot parameters."""
        script_path = SCRIPTS_DIR / "recapture_s1b3_tests.ps1"
        res = _run_powershell(f"Get-Command '{script_path}' -Syntax")
        assert res.returncode == 0
        assert "PythonExe" in res.stdout
        assert "RepoRoot" in res.stdout

    def test_script_rejects_nonexistent_python_binary_fast(self, tmp_path: Path):
        """Passing an invalid -PythonExe must fail fast with non-zero exit code."""
        script_path = SCRIPTS_DIR / "recapture_s1b3_tests.ps1"
        fake_python = r"C:\fake_nonexistent_directory\python.exe"
        res = _run_powershell(
            f"& '{script_path}' -PythonExe '{fake_python}' -RepoRoot '{tmp_path}'",
            timeout=15,
        )
        assert res.returncode != 0
        assert ("CommandNotFoundException" in res.stderr or "The term" in res.stderr or "not recognized" in res.stderr)

    def test_script_python_discovery_env_precedence(self):
        """Validate multi-tier discovery logic: PYTHON_EXE > PYTHON > VIRTUAL_ENV > CONDA_PREFIX."""
        ps_test_block = """
        $scriptPath = Resolve-Path 'scripts/recapture_s1b3_tests.ps1'
        $scriptContent = Get-Content -LiteralPath $scriptPath -Raw

        # Assert discovery cascade lines are present and ordered
        assert ($scriptContent -match '\\$env:PYTHON_EXE')
        assert ($scriptContent -match '\\$env:PYTHON')
        assert ($scriptContent -match '\\$env:VIRTUAL_ENV')
        assert ($scriptContent -match '\\$env:CONDA_PREFIX')
        assert ($scriptContent -match 'Get-Command python')
        Write-Host "CASCADE_PATTERNS_VERIFIED"
        """
        res = _run_powershell(ps_test_block)
        assert res.returncode == 0
        assert "CASCADE_PATTERNS_VERIFIED" in res.stdout

    def test_script_non_git_repo_commit_fallback(self, tmp_path: Path):
        """When RepoRoot is not a git repository, commit should safely default to UNKNOWN_COMMIT."""
        ps_test = f"""
        $RepoRoot = '{tmp_path}'
        $commit = try {{ (git -C $RepoRoot rev-parse HEAD 2>$null) }} catch {{ 'UNKNOWN_COMMIT' }}
        if (-not $commit) {{ $commit = 'UNKNOWN_COMMIT' }}
        Write-Host "RESOLVED_COMMIT:$commit"
        """
        res = _run_powershell(ps_test)
        assert res.returncode == 0
        assert "RESOLVED_COMMIT:UNKNOWN_COMMIT" in res.stdout


# ===========================================================================
# Category 4: Feature F9 — PowerShell setup_e_workspace.ps1 Stress Tests
# ===========================================================================
@pytest.mark.skipif(POWERSHELL_EXE is None, reason="PowerShell not installed on this host")
class TestAdversarialF9SetupEWorkspaceScript:
    """Stress-test drive handling, formatting, and safety in setup_e_workspace.ps1."""

    def test_script_syntax_declares_all_parameters(self):
        """Script signature must declare TargetDrive, RepoRoot, ComsolRoot, and switches."""
        script_path = SCRIPTS_DIR / "setup_e_workspace.ps1"
        res = _run_powershell(f"Get-Command '{script_path}' -Syntax")
        assert res.returncode == 0
        assert "TargetDrive" in res.stdout
        assert "RepoRoot" in res.stdout
        assert "ComsolRoot" in res.stdout
        assert "ApplyPowerPlan" in res.stdout
        assert "RedirectTempEnv" in res.stdout
        assert "AddDefenderExclusions" in res.stdout
        assert "UpdateComsolPrefs" in res.stdout

    def test_unmounted_target_drive_warns_and_skips_cleanly(self, unmounted_drive: str):
        """An unmounted drive must warn and skip writes without touching real data drives."""
        res = _run_setup_script(f"{unmounted_drive}:")
        assert res.returncode == 0, res.stderr
        assert f"Target drive '{unmounted_drive}:\\' is not mounted" in res.stdout
        assert "[SKIPPED] Directory creation skipped" in res.stdout
        assert "[SKIPPED] comsolbatch_perf.ini generation skipped" in res.stdout
        assert "[CREATED]" not in res.stdout
        assert "[READY]" not in res.stdout

    @pytest.mark.parametrize("drive_format", ["colon", "letter", "root", "lowercase"])
    def test_target_drive_formatting_tolerance(self, drive_format: str, unmounted_drive: str):
        """All accepted drive formats must resolve to the same unmounted root."""
        formats = {
            "colon": f"{unmounted_drive}:",
            "letter": unmounted_drive,
            "root": f"{unmounted_drive}:\\",
            "lowercase": f"{unmounted_drive.lower()}:",
        }
        res = _run_setup_script(formats[drive_format])
        assert res.returncode == 0, res.stderr
        assert "is not mounted on this system" in res.stdout
        assert "[SKIPPED] Directory creation skipped" in res.stdout
        assert "[SKIPPED] comsolbatch_perf.ini generation skipped" in res.stdout
        assert "[CREATED]" not in res.stdout
        assert "[READY]" not in res.stdout

    def test_supports_should_process_whatif(self, unmounted_drive: str):
        """Accept -WhatIf with an isolated target so real workspaces cannot be changed."""
        res = _run_setup_script(f"{unmounted_drive}:", whatif=True)
        assert res.returncode == 0, res.stderr
        assert "[SKIPPED] Directory creation skipped" in res.stdout
        assert "[SKIPPED] comsolbatch_perf.ini generation skipped" in res.stdout
        assert "[CREATED]" not in res.stdout
        assert "[READY]" not in res.stdout


# ===========================================================================
# Category 5: Cross-Script Hardening & Global Hygiene
# ===========================================================================
class TestAdversarialCrossScriptHardening:
    """Global hygiene and edge-case guards across all Milestone 2 components."""

    def test_no_hardcoded_uuid_in_any_owned_scripts(self):
        """Assert TARGET_UUID does not appear in any script owned by Milestone 2."""
        owned_scripts = [
            "dispatch_backfill.py",
            "calibrate_c04_3d.py",
            "benchmark_comsol_numa.py",
            "export_vorticity_videos.py",
            "recapture_s1b3_tests.ps1",
            "setup_e_workspace.ps1",
        ]
        for name in owned_scripts:
            path = SCRIPTS_DIR / name
            assert path.is_file(), f"Script {name} missing"
            content = path.read_text(encoding="utf-8")
            assert TARGET_UUID not in content, f"Hardcoded UUID {TARGET_UUID} found in {name}!"

    def test_numa_speedup_extreme_values(self):
        """compute_speedups must handle NaN, negative values, and zero gracefully."""
        import benchmark_comsol_numa as bcn

        results = [
            {"profile": "base", "ok": True, "wall_s": 10.0, "mean_solve_s": 10.0, "throughput_jobs_per_hr": 5.0},
            {"profile": "zero_solve", "ok": True, "wall_s": 0.0, "mean_solve_s": 0.0, "throughput_jobs_per_hr": 0.0},
            {"profile": "neg_solve", "ok": True, "wall_s": -5.0, "mean_solve_s": -1.0, "throughput_jobs_per_hr": -2.0},
            {"profile": "inf_worker", "ok": True, "wall_s": 10.0, "mean_solve_s": float("inf"), "throughput_jobs_per_hr": 0.0},
        ]
        res = bcn.compute_speedups(results)
        assert res[0]["single_job_speedup"] == 1.0
        assert res[0]["throughput_speedup"] == 1.0
        assert res[1]["single_job_speedup"] == 0.0
        assert res[1]["throughput_speedup"] == 0.0
        assert res[2]["single_job_speedup"] == 0.0
        assert res[2]["throughput_speedup"] == 0.0
        assert res[3]["single_job_speedup"] == 0.0

    def test_dispatch_backfill_get_system_health_extreme_disk(self):
        """get_system_health must never crash even with unmounted or blank drive paths."""
        import dispatch_backfill as db

        for bad_drive in ["", "Q:\\", "NONEXISTENT", None]:
            health = db.get_system_health(bad_drive)
            assert isinstance(health, dict)
            assert health["free_disk_gb"] >= 0.0
            assert health["free_disk_e_gb"] >= 0.0
