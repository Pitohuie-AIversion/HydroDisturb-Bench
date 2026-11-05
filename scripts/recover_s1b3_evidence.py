"""Recover actual process outputs, extract solver log from JSON, and rebuild evidence manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV_DIR = ROOT / "docs/evidence/s1b3_m3_time_20261105"
SRC_DIR = ROOT / "runs/re100_s1b3_m3_dt025_20261105_verified"
TASK_DIR = Path(r"C:\Users\Administrator\.gemini\antigravity-ide\brain\78e0afb2-6233-4e38-b94c-b25dc49b4926\.system_generated\tasks")


def main() -> None:
    # 1. Extract solver_run.txt from solve_response.json
    resp_path = EV_DIR / "solve_response.json"
    resp_data = json.loads(resp_path.read_text(encoding="utf-8"))
    raw_log = resp_data.get("log", "")
    header = (
        "# Exported copy of COMSOL solver progress log from solve_response.json['log']\n"
        "# Source: runs/re100_s1b3_m3_dt025_20261105_verified/re100_s1b3_m3_dt025_solved.mph\n"
        "# Target maxstep: 0.025 s (const), rtol: 1e-4\n\n"
    )
    solver_run_txt = (header + raw_log).replace("\r\n", "\n")
    (EV_DIR / "solver_run.txt").write_text(solver_run_txt, encoding="utf-8")
    print(f"Extracted solver_run.txt: {(EV_DIR / 'solver_run.txt').stat().st_size} bytes")

    # 2. Recover raw task logs
    t6195 = TASK_DIR / "task-6195.log"
    if t6195.exists():
        header_6195 = (
            "# Raw stdout/stderr from background task-6195\n"
            "# Command: & 'D:\\Program Files\\Anaconda\\envs\\seagent\\python.exe' -m pytest -q\n"
            "# Exit code: 0\n"
            "# Timestamp: 2026-11-05T15:10:07+08:00 to 2026-11-05T15:15:21+08:00\n\n"
        )
        content_6195 = (header_6195 + t6195.read_text(encoding="utf-8", errors="replace")).replace("\r\n", "\n")
        (EV_DIR / "raw_task_6195_full_regression_stdout.txt").write_text(content_6195, encoding="utf-8")
        print("Wrote raw_task_6195_full_regression_stdout.txt")

    t6153 = TASK_DIR / "task-6153.log"
    if t6153.exists():
        header_6153 = (
            "# Raw stdout/stderr from background task-6153\n"
            "# Command: & 'D:\\Program Files\\Anaconda\\envs\\seagent\\python.exe' -m pytest -q -m 'not integration'\n"
            "# Exit code: 0\n"
            "# Timestamp: 2026-11-05T15:07:36+08:00 to 2026-11-05T15:07:54+08:00\n\n"
        )
        content_6153 = (header_6153 + t6153.read_text(encoding="utf-8", errors="replace")).replace("\r\n", "\n")
        (EV_DIR / "raw_task_6153_unit_regression_stdout.txt").write_text(content_6153, encoding="utf-8")
        print("Wrote raw_task_6153_unit_regression_stdout.txt")

    t6160 = TASK_DIR / "task-6160.log"
    if t6160.exists():
        header_6160 = (
            "# Raw stdout/stderr from background task-6160\n"
            "# Command: & 'D:\\Program Files\\Anaconda\\envs\\seagent\\python.exe' -m pytest -v -m integration tests/test_comsol_integration.py\n"
            "# Exit code: 0\n"
            "# Timestamp: 2026-11-05T15:08:01+08:00 to 2026-11-05T15:10:01+08:00\n\n"
        )
        content_6160 = (header_6160 + t6160.read_text(encoding="utf-8", errors="replace")).replace("\r\n", "\n")
        (EV_DIR / "raw_task_6160_comsol_integration_stdout.txt").write_text(content_6160, encoding="utf-8")
        print("Wrote raw_task_6160_comsol_integration_stdout.txt")

    # 3. Annotate full_regression.txt and unit_regression.txt
    full_reg = EV_DIR / "full_regression.txt"
    if full_reg.exists():
        orig_text = full_reg.read_text(encoding="utf-8")
        if "# [REVISED NOTE]" not in orig_text:
            new_text = (
                "# [REVISED NOTE]: This file is an agent-compiled summary overview of the full regression run, not raw process stdout.\n"
                "# For raw process stdout directly captured from the pytest process, see raw_task_6195_full_regression_stdout.txt.\n\n"
                + orig_text
            )
            full_reg.write_text(new_text.replace("\r\n", "\n"), encoding="utf-8")

    unit_reg = EV_DIR / "unit_regression.txt"
    if unit_reg.exists():
        orig_text = unit_reg.read_text(encoding="utf-8")
        if "# [REVISED NOTE]" not in orig_text:
            new_text = (
                "# [REVISED NOTE]: This file is an agent-compiled summary overview of the non-integration regression run, not raw process stdout.\n"
                "# For raw process stdout directly captured from the pytest process, see raw_task_6153_unit_regression_stdout.txt.\n\n"
                + orig_text
            )
            unit_reg.write_text(new_text.replace("\r\n", "\n"), encoding="utf-8")

    # 4. Update artifact_audit.json disclaimer
    audit_path = EV_DIR / "artifact_audit.json"
    audit_data = json.loads(audit_path.read_text(encoding="utf-8"))
    audit_data["audit_nature_disclaimer"] = (
        "This document is an agent-compiled summary artifact of the stage execution, not an independent re-audit. "
        "Acceptance and conclusions rely directly on m3_time_report.json and raw solver outputs."
    )
    audit_path.write_text(json.dumps(audit_data, indent=2, ensure_ascii=False).replace("\r\n", "\n") + "\n", encoding="utf-8")

    # 5. Rebuild evidence_manifest.json
    manifest = {
        "version": "1.0.0",
        "stage": "S1-B3",
        "description": "S1-B3 M3 internal time-step halving cross-check evidence and verification logs",
        "files": {},
    }

    for item in sorted(EV_DIR.iterdir()):
        if item.name == "evidence_manifest.json" or not item.is_file():
            continue
        content = item.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        size = len(content)

        orig_path = None
        orig_sha = None
        orig_bytes = None
        src_cand = SRC_DIR / item.name
        if src_cand.exists():
            orig_path = str(src_cand).replace("\\", "/")
            orig_content = src_cand.read_bytes()
            orig_sha = hashlib.sha256(orig_content).hexdigest()
            orig_bytes = len(orig_content)

        manifest["files"][item.name] = {
            "archive_sha256": sha,
            "archive_bytes": size,
            "original_path": orig_path,
            "original_sha256": orig_sha,
            "original_bytes": orig_bytes,
        }

    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False).replace("\r\n", "\n") + "\n"
    (EV_DIR / "evidence_manifest.json").write_bytes(manifest_text.encode("utf-8"))
    print(f"Rebuilt S1-B3 manifest. Total files: {len(manifest['files'])}")


if __name__ == "__main__":
    main()
