"""Check tracked development entry points without loading local CFD artifacts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for rel in [
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "uv.lock",
    ".github/workflows/tests.yml",
    "configs/base_case.json",
    "docs/DEVELOPMENT.md",
    "docs/RESEARCH_PLAN.md",
    "docs/VALIDATION.md",
    "src/hydro_disturb_bench/cli.py",
    "src/hydro_disturb_bench/comsol_java/bridge.py",
    "src/hydro_disturb_bench/comsol_java/errors.py",
    "src/hydro_disturb_bench/comsol_java/file_transactions.py",
    "src/hydro_disturb_bench/comsol_java/ComsolBridge.java",
    "src/hydro_disturb_bench/field_sampling.py",
    "src/hydro_disturb_bench/field_sequence.py",
    "src/hydro_disturb_bench/branch_pulse.py",
    "src/hydro_disturb_bench/branch_evidence.py",
    "scripts/sample_re100_m3_fields.py",
    "scripts/sample_re100_m3_sequence.py",
    "scripts/run_m3_branch_pulse.py",
    "scripts/run_m3_pulse_amplitude_series.py",
    "tests/test_git_attributes.py",
]:
    assert (ROOT / rel).is_file(), f"Missing: {rel}"
print("Repository entry points and development documents are present; this check does not validate CFD results.")
