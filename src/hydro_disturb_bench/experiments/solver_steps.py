"""Validation of the actual accepted-step logs for the frozen branch protocol."""
from __future__ import annotations

import hashlib
import re

import numpy as np

from hydro_disturb_bench.comsol_java import bridge

from .evidence_io import file_sha256, require


def parse_internal_steps(text, start=180.0, stop=200.0, cap=0.05):
    """Read accepted internal rows; output-only rows never count as steps."""
    header = re.compile(r"^\s*Step\s+Time\s+Stepsize(?:\s|$)")
    candidate = re.compile(r"^\s*(\d+|-)\s+(\S+)\s+(\S+)(?:\s|$)")
    rows, initial_times, output_rows = [], [], 0
    headers = 0
    for line in text.splitlines():
        if header.match(line):
            headers += 1
            continue
        if not headers:
            continue
        match = candidate.match(line)
        if not match:
            continue
        step_text, time_text, dt_text = match.groups()
        if step_text == "-" and not re.search(r"\sout(?:\s|$)", line):
            # COMSOL prints progress lines such as '- Current Progress: 10 %'.
            # They share the leading dash with output rows, but have no 'out'.
            continue
        try:
            actual_time = float(time_text)
        except ValueError as exc:
            raise bridge.ModelValidationError("Malformed time in internal-step table") from exc
        require(np.isfinite(actual_time), "Non-finite internal-step time")
        if dt_text == "-":
            require(re.search(r"\sout(?:\s|$)", line), "Malformed output-only solver row")
            output_rows += 1
            if abs(actual_time - start) <= 1e-8:
                initial_times.append(actual_time)
            continue
        require(step_text != "-", "Accepted internal step lacks its number")
        try:
            dt = float(dt_text)
        except ValueError as exc:
            raise bridge.ModelValidationError("Malformed dt in internal-step table") from exc
        require(np.isfinite(dt), "Non-finite internal-step size")
        rows.append((int(step_text), actual_time, dt))
    require(headers > 0, "Missing actual Step Time Stepsize header")
    require(bool(rows), "Missing accepted internal-step records")
    first = rows[0][0]
    require(first in (0, 1) and [row[0] for row in rows] == list(range(first, first + len(rows))),
             "Accepted internal-step numbers must be contiguous and unique from 0 or 1")
    require(initial_times or abs(rows[0][1] - start) <= 1e-8,
             "Internal-step log does not establish the actual 180 s start")
    require(rows[0][1] >= start - 1e-8 and rows[-1][1] >= stop - 1e-8,
             "Missing complete accepted internal-step log through the final time")
    # Printed times can coincide when a tiny positive step rounds to the same value.
    require(all(left[1] <= right[1] for left, right in zip(rows, rows[1:])),
             "Displayed internal-step times move backwards")
    cap_limit = cap * (1.0 + 1e-4)
    require(all(0 < row[2] <= cap_limit for row in rows),
             "Actual accepted dt violates the frozen positive step cap")
    require(len(rows) * cap_limit >= stop - start - 1e-8,
             "Too few accepted internal steps to cover the requested time interval at the frozen cap")
    return {"status": "PASS", "headers_seen": headers, "accepted_step_count": len(rows),
            "first_step_number": first, "last_step_number": rows[-1][0],
            "initial_time_s": start, "first_accepted_time_s": rows[0][1],
            "final_internal_time_s": rows[-1][1], "output_only_rows_excluded": output_rows,
            "dt_min_s": min(row[2] for row in rows), "dt_max_s": max(row[2] for row in rows),
            "cap_s": cap, "cap_display_relative_tolerance": 1e-4,
            "contiguous_unique_accepted_step_ids": True, "displayed_time_nondecreasing": True,
            "last_internal_step_may_exceed_last_output_time": True}



def validate_branch_steps(directory, response, plan):
    getter_text = response.get("time_solver_log")
    getter_available = isinstance(getter_text, str) and bool(getter_text.strip())
    header = re.compile(r"^\s*Step\s+Time\s+Stepsize(?:\s|$)", re.MULTILINE)
    getter = {"available": getter_available,
              "character_count": len(getter_text) if isinstance(getter_text, str) else 0,
              "has_step_table_header": bool(getter_available and header.search(getter_text)),
              "complete_step_table": False, "truncated_or_invalid": False}
    getter_result = None
    if getter_available:
        getter["text_utf8_sha256"] = hashlib.sha256(getter_text.encode("utf-8")).hexdigest()
        try:
            getter_result = parse_internal_steps(
                getter_text, plan["branch_time_s"], plan["stop_s"], plan["solver"]["maxstepbdf_s"])
            getter["complete_step_table"] = True
        except bridge.ModelValidationError as exc:
            getter["truncated_or_invalid"] = True
            getter["validation_error"] = str(exc)
    source = {"source": None, "getter_diagnostic": getter}
    try:
        candidates = []
        for path in sorted(directory.glob("process_*_stdout.log")):
            raw = path.read_bytes()
            decoded = raw.decode("utf-8", errors="replace")
            if header.search(decoded):
                candidates.append((path, decoded))
        source["stdout_table_candidates"] = [path.name for path, _ in candidates]
        require(len(candidates) <= 1, "Ambiguous actual solver stdout tables")
        if candidates:
            path, text = candidates[0]
            source.update({"source": path.name, "source_bytes_sha256": file_sha256(path),
                           "reason": "Original process stdout is the primary actual step-log source"})
            result = parse_internal_steps(text, plan["branch_time_s"], plan["stop_s"],
                                           plan["solver"]["maxstepbdf_s"])
        else:
            require(getter_available, "Missing actual solver stdout table and time_solver_log getter")
            source.update({"source": "readback.json:time_solver_log",
                           "source_text_utf8_sha256": getter["text_utf8_sha256"],
                           "reason": "No original stdout step table is available; complete actual getter used"})
            require(getter_result is not None,
                     "No original stdout step table and getter is incomplete: " + getter.get("validation_error", ""))
            result = getter_result
        return {**result, **source}
    except bridge.ModelValidationError as exc:
        return {"status": "FAIL", **source, "error": str(exc)}
