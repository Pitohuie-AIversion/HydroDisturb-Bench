#!/usr/bin/env python3
"""Standalone CLI runner for the COMSOL 6.4 Native Optimization E2E Test Suite.

Executes the 4-tier independent opaque-box E2E test suite in tests/e2e_comsol_native_opt/:
- Tier 1: Feature Coverage (Category-Partition, F1-F9)
- Tier 2: Boundary & Corner Cases (BVA & Invariants)
- Tier 3: Cross-Feature Combinations (Pairwise Multi-Physics)
- Tier 4: Real-World Application Scenarios (1400 rpm & Native EGO)

Generates structured test reports in JSON format and manages exit codes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DIR = Path(__file__).resolve().parent
REPORTS_DIR = REPO_ROOT / "reports"


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run COMSOL 6.4 Native Optimization 4-Tier E2E Test Suite"
    )
    parser.add_argument(
        "--tier",
        choices=["all", "tier1", "tier2", "tier3", "tier4"],
        default="all",
        help="Target test tier to execute (default: all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose test output",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=str(REPORTS_DIR / "e2e_comsol_native_opt_report.json"),
        help="Output path for structured JSON report",
    )
    parser.add_argument(
        "-k", "--keyword",
        type=str,
        default=None,
        help="Only run tests which match the given substring expression",
    )
    parser.add_argument(
        "--maxfail",
        type=int,
        default=None,
        help="Exit after first N failures or errors",
    )
    return parser.parse_args(args)


def build_pytest_args(opts: argparse.Namespace) -> list[str]:
    """Assemble pytest command arguments."""
    cmd = [sys.executable, "-m", "pytest", str(TEST_DIR)]

    if opts.tier != "all":
        cmd.extend(["-m", opts.tier])

    if opts.verbose:
        cmd.append("-v")
    else:
        cmd.append("-q")

    if opts.keyword:
        cmd.extend(["-k", opts.keyword])

    if opts.maxfail:
        cmd.extend(["--maxfail", str(opts.maxfail)])

    cmd.extend(["--tb=short"])
    return cmd


def count_tier_tests() -> dict[str, int]:
    """Inspect and count tests defined in each tier file."""
    tier_files = {
        "tier1": TEST_DIR / "test_tier1_feature_coverage.py",
        "tier2": TEST_DIR / "test_tier2_boundary_corner.py",
        "tier3": TEST_DIR / "test_tier3_cross_feature.py",
        "tier4": TEST_DIR / "test_tier4_real_world.py",
    }
    counts = {}
    for tier, file_path in tier_files.items():
        if file_path.is_file():
            content = file_path.read_text(encoding="utf-8")
            # Count def test_ occurrences
            c = sum(1 for line in content.splitlines() if line.strip().startswith("def test_"))
            counts[tier] = c
        else:
            counts[tier] = 0
    return counts


def run_suite(opts: argparse.Namespace) -> int:
    """Execute pytest suite and generate report."""
    print("=" * 80)
    print("  COMSOL 6.4 Native Optimization & Hydro-Structural Coupling E2E Test Suite")
    print("=" * 80)
    print(f"Target Tier     : {opts.tier.upper()}")
    print(f"Test Directory  : {TEST_DIR}")
    print(f"Platform        : {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python Runtime  : {sys.version.split()[0]}")
    print("-" * 80)

    start_time = time.perf_counter()
    utc_start = datetime.datetime.now(datetime.timezone.utc).isoformat()

    cmd = build_pytest_args(opts)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=False)

    elapsed = time.perf_counter() - start_time
    exit_code = proc.returncode

    tier_counts = count_tier_tests()
    total_defined = sum(tier_counts.values())

    # Generate structured JSON report
    report_data: dict[str, Any] = {
        "suite_name": "COMSOL 6.4 Native Optimization & Hydro-Structural Co-Optimization E2E",
        "status": "PASSED" if exit_code == 0 else "FAILED",
        "timestamp_utc": utc_start,
        "duration_seconds": round(elapsed, 3),
        "exit_code": exit_code,
        "selected_tier": opts.tier,
        "tier_test_inventory": tier_counts,
        "total_test_count": total_defined,
        "environment": {
            "os": platform.platform(),
            "python": sys.version,
            "comsol_version": "6.4",
            "comsol_build": "429",
            "repo_root": str(REPO_ROOT),
        },
    }

    # Save report
    json_path = Path(opts.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_data, f, indent=2)

    print("-" * 80)
    print(f"Execution Status: {report_data['status']}")
    print(f"Total Tests     : {total_defined} across 4 tiers")
    for t_name, t_cnt in tier_counts.items():
        print(f"  - {t_name.upper():<7}: {t_cnt} test cases")
    print(f"Elapsed Time    : {elapsed:.2f} seconds")
    print(f"Report JSON     : {json_path}")
    print("=" * 80)

    return exit_code


def main() -> None:
    """CLI entrypoint."""
    opts = parse_args()
    code = run_suite(opts)
    sys.exit(code)


if __name__ == "__main__":
    main()
