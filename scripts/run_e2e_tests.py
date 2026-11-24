"""HydroDisturb-Bench Dedicated E2E Test Suite CLI Runner.

Executes end-to-end requirement-driven test suites across Tiers 1-4 with:
- Configurable tier execution (--tier {all, 1, 2, 3, 4})
- Standard flags: --verbose, --exitfirst, --xml-output, --filter (-k), --timeout
- Process isolation via subprocess pytest invocations
- Automated JUnit XML metric collection (Passed, Failed, Skipped, Duration)
- Formatted summary execution table and strict exit-code semantics.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

# Workspace root discovery
REPO_ROOT = Path(__file__).resolve().parents[1]

# Tier mapping definitions
TIER_MAP = {
    "1": {
        "name": "Tier 1: Feature Coverage Suite",
        "file": "tests/e2e/test_tier1_features.py",
        "marker": "tier1",
        "min_tests": 35,
    },
    "2": {
        "name": "Tier 2: Boundary & Edge Cases",
        "file": "tests/e2e/test_tier2_boundaries.py",
        "marker": "tier2",
        "min_tests": 35,
    },
    "3": {
        "name": "Tier 3: Pairwise Combinatorial",
        "file": "tests/e2e/test_tier3_pairwise.py",
        "marker": "tier3",
        "min_tests": 10,
    },
    "4": {
        "name": "Tier 4: Real-World Workloads",
        "file": "tests/e2e/test_tier4_workloads.py",
        "marker": "tier4",
        "min_tests": 5,
    },
}


@dataclass
class TierResult:
    tier: str
    suite_file: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    status: str = "PENDING"  # PASS, FAIL, PENDING, ERROR, NO_TESTS, TIMEOUT
    error_message: str | None = None
    xml_element: ET.Element | None = None


def parse_junit_xml(xml_path: Path) -> tuple[int, int, int, int, float, ET.Element | None]:
    """Extract total, passed, failed, skipped, duration, and testsuite element from JUnit XML."""
    if not xml_path.is_file():
        return 0, 0, 0, 0, 0.0, None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        testsuite = root if root.tag == "testsuite" else root.find("testsuite")
        if testsuite is None:
            return 0, 0, 0, 0, 0.0, None

        tests = int(testsuite.attrib.get("tests", 0))
        failures = int(testsuite.attrib.get("failures", 0))
        errors = int(testsuite.attrib.get("errors", 0))
        skipped = int(testsuite.attrib.get("skipped", 0))
        duration = float(testsuite.attrib.get("time", 0.0))

        failed = failures + errors
        passed = max(0, tests - failed - skipped)
        return tests, passed, failed, skipped, duration, testsuite
    except Exception:
        return 0, 0, 0, 0, 0.0, None


def run_tier(
    tier_key: str,
    tier_info: dict,
    verbose: bool,
    exitfirst: bool,
    extra_pytest_args: Sequence[str] | None = None,
    timeout: float | None = 120.0,
) -> TierResult:
    """Execute a single test tier via isolated pytest subprocess."""
    rel_path = tier_info["file"]
    abs_path = REPO_ROOT / rel_path

    if not abs_path.is_file():
        return TierResult(
            tier=f"Tier {tier_key}",
            suite_file=Path(rel_path).name,
            status="PENDING",
        )

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp_xml:
        xml_path = Path(tmp_xml.name)

    try:
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            str(abs_path),
            f"--junitxml={xml_path}",
        ]
        if verbose:
            cmd.append("-v")
        if exitfirst:
            cmd.append("-x")
        if extra_pytest_args:
            cmd.extend(extra_pytest_args)

        env = os.environ.copy()
        env["PYTHONPATH"] = f"{REPO_ROOT / 'src'}{os.pathsep}{REPO_ROOT}{os.pathsep}{env.get('PYTHONPATH', '')}"
        env["PYTHONIOENCODING"] = "utf-8"

        start_time = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=not verbose,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout if (timeout and timeout > 0) else None,
            )
            wall_duration = time.perf_counter() - start_time

            tests, passed, failed, skipped, xml_duration, ts_elem = parse_junit_xml(xml_path)
            duration = xml_duration if xml_duration > 0 else wall_duration

            if proc.returncode == 0:
                status = "PASS"
            elif proc.returncode in (1, 2):
                status = "FAIL"
            elif proc.returncode == 5:
                # Pytest returncode 5 = no tests collected
                status = "NO_TESTS"
            else:
                status = "ERROR"

            err_msg = None
            if proc.returncode != 0 and not verbose:
                diag_parts = []
                if proc.stdout and proc.stdout.strip():
                    diag_parts.append(proc.stdout.strip())
                if proc.stderr and proc.stderr.strip():
                    diag_parts.append(proc.stderr.strip())
                err_msg = "\n".join(diag_parts) if diag_parts else None

            return TierResult(
                tier=f"Tier {tier_key}",
                suite_file=Path(rel_path).name,
                total=tests,
                passed=passed,
                failed=failed,
                skipped=skipped,
                duration_s=duration,
                status=status,
                error_message=err_msg,
                xml_element=ts_elem,
            )
        except subprocess.TimeoutExpired as exc:
            wall_duration = time.perf_counter() - start_time
            timeout_msg = f"Test tier execution exceeded timeout limit of {timeout}s"
            diag_parts = []
            if exc.stdout:
                stdout_str = exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", errors="replace")
                if stdout_str.strip():
                    diag_parts.append(stdout_str.strip())
            if exc.stderr:
                stderr_str = exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
                if stderr_str.strip():
                    diag_parts.append(stderr_str.strip())
            if diag_parts:
                timeout_msg += "\n" + "\n".join(diag_parts)

            return TierResult(
                tier=f"Tier {tier_key}",
                suite_file=Path(rel_path).name,
                total=0,
                passed=0,
                failed=1,
                skipped=0,
                duration_s=wall_duration,
                status="TIMEOUT",
                error_message=timeout_msg,
                xml_element=None,
            )
    finally:
        if xml_path.is_file():
            try:
                xml_path.unlink()
            except OSError:
                pass


def format_summary_table(results: list[TierResult]) -> str:
    """Generate high-clarity ASCII summary table."""
    header = (
        f"{'Tier':<8} {'Test Suite Module':<28} {'Total':>6} {'Passed':>7} {'Failed':>7} "
        f"{'Skipped':>8} {'Duration':>9} {'Status':<8}"
    )
    separator = "-" * len(header)
    double_sep = "=" * len(header)

    lines = [
        double_sep,
        "HydroDisturb-Bench E2E Test Suite Execution Summary".center(len(header)),
        double_sep,
        header,
        separator,
    ]

    total_tests = 0
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_duration = 0.0

    for r in results:
        total_tests += r.total
        total_passed += r.passed
        total_failed += r.failed
        total_skipped += r.skipped
        total_duration += r.duration_s

        tot_str = str(r.total) if r.status != "PENDING" else "-"
        pass_str = str(r.passed) if r.status != "PENDING" else "-"
        fail_str = str(r.failed) if r.status != "PENDING" else "-"
        skip_str = str(r.skipped) if r.status != "PENDING" else "-"
        dur_str = f"{r.duration_s:.2f}s" if r.status != "PENDING" else "-"

        lines.append(
            f"{r.tier:<8} {r.suite_file:<28} {tot_str:>6} {pass_str:>7} {fail_str:>7} "
            f"{skip_str:>8} {dur_str:>9} {r.status:<8}"
        )

    lines.append(separator)
    has_suite_failures = any(r.status in ("FAIL", "ERROR", "TIMEOUT", "NO_TESTS") for r in results)
    overall_status = "FAIL" if (total_failed > 0 or has_suite_failures) else ("PASS" if total_tests > 0 else "EMPTY")
    lines.append(
        f"{'TOTAL':<8} {'All Executed Suites':<28} {total_tests:>6} {total_passed:>7} "
        f"{total_failed:>7} {total_skipped:>8} {total_duration:>8.2f}s "
        f"{overall_status:<8}"
    )
    lines.append(double_sep)
    return "\n".join(lines)


def write_merged_xml(results: list[TierResult], out_path: Path) -> bool:
    """Write combined JUnit XML file from executed test tiers.

    Returns True on success, False if writing failed.
    """
    try:
        if out_path.is_dir() or str(out_path).endswith(("/", "\\")):
            out_path = out_path / "e2e_junit_report.xml"

        out_path.parent.mkdir(parents=True, exist_ok=True)

        root = ET.Element("testsuites")
        total_tests = sum(r.total for r in results)
        total_failures = sum(r.failed for r in results)
        total_skipped = sum(r.skipped for r in results)
        total_errors = sum(1 for r in results if r.status in ("ERROR", "TIMEOUT"))
        total_time = sum(r.duration_s for r in results)

        root.attrib["name"] = "HydroDisturb-Bench E2E Test Suites"
        root.attrib["tests"] = str(total_tests)
        root.attrib["failures"] = str(total_failures)
        root.attrib["errors"] = str(total_errors)
        root.attrib["skipped"] = str(total_skipped)
        root.attrib["time"] = f"{total_time:.3f}"

        for r in results:
            if r.xml_element is not None:
                r.xml_element.attrib["name"] = r.tier
                root.append(r.xml_element)
            elif r.status != "PENDING":
                is_err = r.status in ("ERROR", "TIMEOUT")
                ts = ET.SubElement(root, "testsuite", attrib={
                    "name": r.tier,
                    "tests": str(max(r.total, 1 if is_err else 0)),
                    "failures": str(r.failed),
                    "errors": "1" if is_err else "0",
                    "skipped": str(r.skipped),
                    "time": f"{r.duration_s:.3f}",
                })
                if r.error_message:
                    err = ET.SubElement(ts, "error", attrib={"message": f"Subprocess {r.status}"})
                    err.text = r.error_message

        tree = ET.ElementTree(root)
        tree.write(out_path, encoding="utf-8", xml_declaration=True)
        return True
    except OSError as exc:
        print(f"[ERROR] Failed to write JUnit XML report to '{out_path}': {exc}", file=sys.stderr)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    # Paths may contain characters unsupported by the Windows console code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="HydroDisturb-Bench E2E Test Runner")
    parser.add_argument(
        "--tier",
        choices=["all", "1", "2", "3", "4"],
        default="all",
        help="Specify which tier to execute (default: all)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose pytest streaming output")
    parser.add_argument("-x", "--exitfirst", action="store_true", help="Exit on first failure")
    parser.add_argument("--xml-output", type=str, default=None, help="Save merged JUnit XML report to file")
    parser.add_argument("-k", "--filter", type=str, default=None, help="Pass keyword filter expression to pytest")
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Timeout in seconds for each test tier execution (default: 120.0)",
    )

    args = parser.parse_args(argv)

    selected_tiers = list(TIER_MAP.keys()) if args.tier == "all" else [args.tier]
    results: list[TierResult] = []

    # If an individual non-existent tier was requested explicitly, fail fast
    if args.tier != "all":
        tier_file = REPO_ROOT / TIER_MAP[args.tier]["file"]
        if not tier_file.is_file():
            print(f"[ERROR] Requested {TIER_MAP[args.tier]['name']} file missing: {tier_file}", file=sys.stderr)
            return 1

    extra_pytest_args = []
    if args.filter:
        extra_pytest_args.extend(["-k", args.filter])

    for t_key in selected_tiers:
        info = TIER_MAP[t_key]
        res = run_tier(
            t_key,
            info,
            verbose=args.verbose,
            exitfirst=args.exitfirst,
            extra_pytest_args=extra_pytest_args,
            timeout=args.timeout,
        )
        results.append(res)
        if args.exitfirst and res.status in ("FAIL", "ERROR", "TIMEOUT"):
            break

    # Print summary table
    table_str = format_summary_table(results)
    print("\n" + table_str + "\n")

    # Print diagnostic failure output for non-verbose executions
    failed_results = [r for r in results if r.error_message and r.status in ("FAIL", "ERROR", "TIMEOUT", "NO_TESTS")]
    if failed_results:
        print("=" * 88, file=sys.stderr)
        print("DIAGNOSTIC FAILURE TRACEBACKS (NON-VERBOSE CAPTURE)", file=sys.stderr)
        print("=" * 88, file=sys.stderr)
        for r in failed_results:
            print(f"\n--- [{r.tier}] {r.suite_file} (Status: {r.status}) ---", file=sys.stderr)
            print(r.error_message, file=sys.stderr)
        print("=" * 88 + "\n", file=sys.stderr)

    # If XML output requested, save it
    xml_write_success = True
    if args.xml_output:
        xml_out_path = Path(args.xml_output).resolve()
        if xml_out_path.is_dir() or args.xml_output.endswith(("/", "\\")):
            xml_out_path = xml_out_path / "e2e_junit_report.xml"

        xml_write_success = write_merged_xml(results, xml_out_path)
        if xml_write_success:
            print(f"[INFO] Merged JUnit XML report saved to: {xml_out_path}")

    # Evaluate overall pass/fail
    executed = [r for r in results if r.status != "PENDING"]
    total_executed_tests = sum(r.total for r in results)
    has_failures = any(r.status in ("FAIL", "ERROR", "TIMEOUT", "NO_TESTS") for r in results)

    if not xml_write_success:
        print("[FAIL] Failed to save requested JUnit XML output.", file=sys.stderr)
        return 1

    if not executed or total_executed_tests == 0:
        print("[FAIL] No tests were executed.", file=sys.stderr)
        return 1

    if has_failures:
        print("[FAIL] One or more E2E test suites failed.", file=sys.stderr)
        return 1

    print("[PASS] All executed E2E test suites passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
