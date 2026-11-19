"""HydroDisturb-Bench Propeller E2E Test Suite CLI Runner.

Executes requirement-driven opaque-box propeller E2E suites across Tiers 1-4:
- Subprocess isolation per tier with `--strict-markers`, `PYTHONPATH=src;.`, UTF-8 I/O
- Modes: `--mode {offline,integration,all}` and `--include-integration`
- Completeness gate: `--require-complete` (`PROPELLER_E2E_REQUIRE_COMPLETE=1` + fail on skip)
- Dual reporting: `--json-out` / `--json-output` and `--junitxml` / `--xml-output`
- Strict exit codes: 0=pass, 1=test/output fail, 2=invalid CLI or missing COMSOL, 3=timeout
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

TIER_MAP: dict[str, dict[str, Any]] = {
    "1": {
        "name": "Tier 1: Propeller Feature Coverage",
        "files": ["tests/e2e_propeller/test_propeller_tier1_features.py"],
        "marker": "tier1",
        "has_integration": True,
    },
    "2": {
        "name": "Tier 2: Propeller Boundary & Negative Cases",
        "files": ["tests/e2e_propeller/test_propeller_tier2_boundaries.py"],
        "marker": "tier2",
        "has_integration": True,
    },
    "3": {
        "name": "Tier 3: Propeller Pairwise Interactions",
        "files": ["tests/e2e_propeller/test_propeller_tier3_pairwise.py"],
        "marker": "tier3",
        "has_integration": False,
    },
    "4": {
        "name": "Tier 4: Propeller Real-World ROV Scenarios",
        "files": [
            "tests/e2e_propeller/test_propeller_tier4_scenarios.py",
            "tests/e2e_propeller/test_propeller_tier4_integration.py",
        ],
        "marker": "tier4",
        "has_integration": True,
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
    errors: int = 0
    duration_s: float = 0.0
    status: str = "PENDING"
    error_message: str | None = None
    xml_element: ET.Element | None = None

    def to_json_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("xml_element", None)
        return data


def parse_junit_xml(
    xml_path: Path,
) -> tuple[int, int, int, int, int, float, ET.Element | None]:
    """Extract (total, passed, failed, skipped, errors, duration, elem) from JUnit XML."""
    if not xml_path.is_file():
        return 0, 0, 0, 0, 0, 0.0, None
    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        testsuite = root if root.tag == "testsuite" else root.find("testsuite")
        if testsuite is None:
            return 0, 0, 0, 0, 0, 0.0, None

        tests = int(testsuite.attrib.get("tests", 0))
        failures = int(testsuite.attrib.get("failures", 0))
        errors = int(testsuite.attrib.get("errors", 0))
        skipped = int(testsuite.attrib.get("skipped", 0))
        duration = float(testsuite.attrib.get("time", 0.0))
        passed = max(0, tests - failures - errors - skipped)
        return tests, passed, failures, skipped, errors, duration, testsuite
    except Exception:
        return 0, 0, 0, 0, 0, 0.0, None


def verify_integration_environment() -> tuple[bool, str]:
    """Check COMSOL 6.4 root and baseline .mph files before running live integration."""
    from hydro_disturb_bench.comsol_java.bridge import ComsolConnectionError, find_comsol_root

    try:
        find_comsol_root()
    except ComsolConnectionError as exc:
        return False, f"COMSOL 6.4 environment unavailable: {exc}"

    required = [
        REPO_ROOT / "models" / "prop test2" / "COMSOL_CW_1400rpm" / "V10f-StaticFine-1400rpm.mph",
        REPO_ROOT / "models" / "prop test2" / "COMSOL_CCW_1400rpm" / "V10f-CCWFine-1400rpm.mph",
    ]
    missing = [str(p) for p in required if not p.is_file()]
    if missing:
        return False, f"Missing baseline model assets: {missing}"
    return True, "OK"


def run_tier(
    tier_key: str,
    tier_info: dict[str, Any],
    *,
    mode: str,
    require_complete: bool,
    verbose: bool,
    exitfirst: bool,
    filter_expr: str | None,
    timeout: float,
) -> TierResult:
    """Execute a single propeller E2E tier in an isolated pytest subprocess."""
    rel_files: list[str] = list(tier_info["files"])
    abs_files = [REPO_ROOT / f for f in rel_files]
    missing_files = [str(p) for p in abs_files if not p.is_file()]
    suite_label = ", ".join(Path(f).name for f in rel_files)

    if missing_files:
        return TierResult(
            tier=f"Tier {tier_key}",
            suite_file=suite_label,
            status="FAIL",
            error_message=f"Missing test file(s): {missing_files}",
        )

    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp_xml:
        xml_path = Path(tmp_xml.name)

    try:
        cmd: list[str] = [
            sys.executable,
            "-m",
            "pytest",
            "--strict-markers",
            *[str(p) for p in abs_files],
            f"--junitxml={xml_path}",
        ]
        if mode == "offline":
            cmd.extend(["-m", "not integration and not artifact"])
        elif mode == "integration":
            cmd.extend(["-m", "integration"])
        else:
            cmd.extend(["-m", "not artifact"])

        if verbose:
            cmd.append("-v")
        if exitfirst:
            cmd.append("-x")
        if filter_expr:
            cmd.extend(["-k", filter_expr])

        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        pp_parts = [str(SRC_ROOT), str(REPO_ROOT)]
        if existing_pp:
            pp_parts.append(existing_pp)
        env["PYTHONPATH"] = os.pathsep.join(pp_parts)
        env["PYTHONIOENCODING"] = "utf-8"
        if require_complete:
            env["PROPELLER_E2E_REQUIRE_COMPLETE"] = "1"
        else:
            env.pop("PROPELLER_E2E_REQUIRE_COMPLETE", None)

        if mode in ("integration", "all"):
            env["PROPELLER_E2E_REQUIRE_LIVE"] = "1"

        start_t = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(REPO_ROOT),
                env=env,
                capture_output=not verbose,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout if timeout > 0 else None,
            )
            wall_s = time.perf_counter() - start_t
            tests, passed, failures, skipped, errors, xml_dur, ts_elem = parse_junit_xml(xml_path)
            duration = xml_dur if xml_dur > 0 else wall_s

            if proc.returncode == 0:
                status = "PASS"
            elif proc.returncode in (1, 2):
                status = "FAIL"
            elif proc.returncode == 5:
                status = "NO_TESTS"
            else:
                status = "ERROR"

            if require_complete and mode == "offline" and skipped > 0 and status == "PASS":
                status = "FAIL"

            err_msg = None
            if proc.returncode != 0 and not verbose:
                parts = []
                if proc.stdout and proc.stdout.strip():
                    parts.append(proc.stdout.strip())
                if proc.stderr and proc.stderr.strip():
                    parts.append(proc.stderr.strip())
                err_msg = "\n".join(parts) if parts else None
            elif status == "FAIL" and require_complete and skipped > 0:
                err_msg = f"--require-complete failed: {skipped} skipped test(s) in Tier {tier_key}"

            return TierResult(
                tier=f"Tier {tier_key}",
                suite_file=suite_label,
                total=tests,
                passed=passed,
                failed=failures,
                skipped=skipped,
                errors=errors,
                duration_s=duration,
                status=status,
                error_message=err_msg,
                xml_element=ts_elem,
            )
        except subprocess.TimeoutExpired as exc:
            wall_s = time.perf_counter() - start_t
            msg = f"Tier {tier_key} exceeded timeout of {timeout}s"
            for stream_val in (exc.stdout, exc.stderr):
                if stream_val:
                    txt = (
                        stream_val
                        if isinstance(stream_val, str)
                        else stream_val.decode("utf-8", errors="replace")
                    )
                    if txt.strip():
                        msg += "\n" + txt.strip()
            return TierResult(
                tier=f"Tier {tier_key}",
                suite_file=suite_label,
                total=0,
                passed=0,
                failed=1,
                skipped=0,
                errors=1,
                duration_s=wall_s,
                status="TIMEOUT",
                error_message=msg,
                xml_element=None,
            )
    finally:
        if xml_path.is_file():
            try:
                xml_path.unlink()
            except OSError:
                pass


def format_summary_table(results: list[TierResult]) -> str:
    """Format an ASCII summary table across all executed tiers."""
    header = (
        f"{'Tier':<8} {'Suite Files':<42} {'Total':>6} {'Pass':>6} {'Fail':>6} "
        f"{'Skip':>6} {'Err':>5} {'Time':>8} {'Status':<9}"
    )
    sep = "-" * len(header)
    dbl = "=" * len(header)
    lines = [
        dbl,
        "HydroDisturb-Bench Propeller E2E Suite Execution Summary".center(len(header)),
        dbl,
        header,
        sep,
    ]
    tot_t = sum(r.total for r in results)
    tot_p = sum(r.passed for r in results)
    tot_f = sum(r.failed for r in results)
    tot_s = sum(r.skipped for r in results)
    tot_e = sum(r.errors for r in results)
    tot_d = sum(r.duration_s for r in results)

    for r in results:
        label = r.suite_file if len(r.suite_file) <= 42 else r.suite_file[:39] + "..."
        lines.append(
            f"{r.tier:<8} {label:<42} {r.total:>6} {r.passed:>6} {r.failed:>6} "
            f"{r.skipped:>6} {r.errors:>5} {r.duration_s:>7.2f}s {r.status:<9}"
        )

    lines.append(sep)
    overall = (
        "TIMEOUT"
        if any(r.status == "TIMEOUT" for r in results)
        else (
            "FAIL"
            if any(r.status in ("FAIL", "ERROR", "NO_TESTS") for r in results) or tot_f > 0 or tot_e > 0
            else ("PASS" if tot_t > 0 else "EMPTY")
        )
    )
    lines.append(
        f"{'TOTAL':<8} {'All Executed Propeller Tiers':<42} {tot_t:>6} {tot_p:>6} "
        f"{tot_f:>6} {tot_s:>6} {tot_e:>5} {tot_d:>7.2f}s {overall:<9}"
    )
    lines.append(dbl)
    return "\n".join(lines)


def write_merged_xml(results: list[TierResult], out_path: Path) -> bool:
    """Write merged JUnit XML report across executed tiers."""
    try:
        if out_path.is_dir() or str(out_path).endswith(("/", "\\")):
            out_path = out_path / "propeller_e2e.xml"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        root = ET.Element("testsuites")
        root.attrib["name"] = "HydroDisturb-Bench Propeller E2E"
        root.attrib["tests"] = str(sum(r.total for r in results))
        root.attrib["failures"] = str(sum(r.failed for r in results))
        root.attrib["errors"] = str(sum(r.errors for r in results))
        root.attrib["skipped"] = str(sum(r.skipped for r in results))
        root.attrib["time"] = f"{sum(r.duration_s for r in results):.3f}"

        for r in results:
            if r.xml_element is not None:
                r.xml_element.attrib["name"] = r.tier
                root.append(r.xml_element)
            else:
                ts = ET.SubElement(
                    root,
                    "testsuite",
                    attrib={
                        "name": r.tier,
                        "tests": str(max(r.total, 1 if r.status in ("ERROR", "TIMEOUT") else 0)),
                        "failures": str(r.failed),
                        "errors": str(r.errors),
                        "skipped": str(r.skipped),
                        "time": f"{r.duration_s:.3f}",
                    },
                )
                if r.error_message:
                    err = ET.SubElement(ts, "error", attrib={"message": f"Subprocess {r.status}"})
                    err.text = r.error_message
        ET.ElementTree(root).write(out_path, encoding="utf-8", xml_declaration=True)
        return True
    except OSError as exc:
        print(f"[ERROR] Failed to write JUnit XML to '{out_path}': {exc}", file=sys.stderr)
        return False


def write_json_report(
    results: list[TierResult],
    out_path: Path,
    *,
    mode: str,
    require_complete: bool,
    exit_code: int,
    overall_status: str,
) -> bool:
    """Write machine-readable JSON summary report."""
    try:
        if out_path.is_dir() or str(out_path).endswith(("/", "\\")):
            out_path = out_path / "propeller_e2e.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "mode": mode,
            "include_integration": mode in ("integration", "all"),
            "require_complete": require_complete,
            "overall_status": overall_status,
            "exit_code": exit_code,
            "totals": {
                "tests": sum(r.total for r in results),
                "passed": sum(r.passed for r in results),
                "failed": sum(r.failed for r in results),
                "skipped": sum(r.skipped for r in results),
                "errors": sum(r.errors for r in results),
                "duration_s": round(sum(r.duration_s for r in results), 4),
            },
            "tiers": [r.to_json_dict() for r in results],
        }
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        print(f"[ERROR] Failed to write JSON report to '{out_path}': {exc}", file=sys.stderr)
        return False


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HydroDisturb-Bench Propeller E2E Test Runner")
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "4", "all"],
        default="all",
        help="Tier to execute (default: all)",
    )
    parser.add_argument(
        "--mode",
        choices=["offline", "integration", "all"],
        default="offline",
        help="Execution mode (default: offline)",
    )
    parser.add_argument(
        "--include-integration",
        action="store_true",
        help="Include @pytest.mark.integration tests with extended timeout",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Set PROPELLER_E2E_REQUIRE_COMPLETE=1 and fail on any skipped offline test",
    )
    parser.add_argument(
        "--json-out",
        "--json-output",
        dest="json_out",
        type=str,
        default=None,
        help="Write machine-readable JSON summary report",
    )
    parser.add_argument(
        "--junitxml",
        "--xml-output",
        dest="junitxml",
        type=str,
        default=None,
        help="Write merged JUnit XML report",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose pytest output")
    parser.add_argument("-x", "--exitfirst", action="store_true", help="Stop on first tier failure")
    parser.add_argument("-k", "--filter", type=str, default=None, help="Pytest -k filter expression")
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Per-tier timeout in seconds (default: 120 offline, 1800 integration/all)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = build_parser()
    args = parser.parse_args(argv)

    effective_mode = args.mode
    if args.include_integration and args.mode == "offline":
        effective_mode = "all"

    timeout = (
        args.timeout
        if args.timeout is not None
        else (1800.0 if effective_mode in ("integration", "all") else 120.0)
    )

    if effective_mode in ("integration", "all"):
        ok, msg = verify_integration_environment()
        if not ok:
            print(f"[ENV ERROR] {msg}", file=sys.stderr)
            return 2

    if args.tier == "all":
        selected_tiers = [
            k
            for k, info in TIER_MAP.items()
            if effective_mode != "integration" or info["has_integration"]
        ]
    else:
        selected_tiers = [args.tier]

    results: list[TierResult] = []
    for t_key in selected_tiers:
        res = run_tier(
            t_key,
            TIER_MAP[t_key],
            mode=effective_mode,
            require_complete=args.require_complete,
            verbose=args.verbose,
            exitfirst=args.exitfirst,
            filter_expr=args.filter,
            timeout=timeout,
        )
        results.append(res)
        if args.exitfirst and res.status in ("FAIL", "ERROR", "TIMEOUT", "NO_TESTS"):
            break

    print("\n" + format_summary_table(results) + "\n")

    failed_diag = [
        r for r in results if r.error_message and r.status in ("FAIL", "ERROR", "TIMEOUT", "NO_TESTS")
    ]
    if failed_diag:
        print("=" * 88, file=sys.stderr)
        print("PROPELLER E2E FAILURE DIAGNOSTICS", file=sys.stderr)
        print("=" * 88, file=sys.stderr)
        for r in failed_diag:
            print(f"\n--- [{r.tier}] {r.suite_file} ({r.status}) ---", file=sys.stderr)
            print(r.error_message, file=sys.stderr)
        print("=" * 88 + "\n", file=sys.stderr)

    has_timeout = any(r.status == "TIMEOUT" for r in results)
    has_fail = any(r.status in ("FAIL", "ERROR", "NO_TESTS") for r in results)
    total_executed = sum(r.total for r in results)
    total_failed = sum(r.failed + r.errors for r in results)
    total_skipped = sum(r.skipped for r in results)

    if has_timeout:
        exit_code = 3
        overall_status = "TIMEOUT"
    elif (
        has_fail
        or total_executed == 0
        or total_failed > 0
        or (args.require_complete and effective_mode == "offline" and total_skipped > 0)
    ):
        exit_code = 1
        overall_status = "FAIL"
    else:
        exit_code = 0
        overall_status = "PASS"

    if args.junitxml:
        xml_ok = write_merged_xml(results, Path(args.junitxml).resolve())
        if not xml_ok and exit_code == 0:
            exit_code = 1
            overall_status = "FAIL"

    if args.json_out:
        json_ok = write_json_report(
            results,
            Path(args.json_out).resolve(),
            mode=effective_mode,
            require_complete=args.require_complete,
            exit_code=exit_code,
            overall_status=overall_status,
        )
        if not json_ok and exit_code == 0:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
