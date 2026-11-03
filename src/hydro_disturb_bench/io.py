"""Durable local JSON writes; never silently overwrite a case manifest."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def atomic_json(path: str | Path, data: Any) -> None:
    target=Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite existing file: {target}")
    fd,tmp=tempfile.mkstemp(prefix=f".{target.name}.",suffix=".tmp",dir=str(target.parent))
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        # Exclusive creation at final path. Avoid replacing a concurrently created output.
        os.link(tmp,target)
        if os.name != "nt":
            dir_fd=os.open(str(target.parent),os.O_RDONLY)
            try: os.fsync(dir_fd)
            finally: os.close(dir_fd)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)



# ---------------------------------------------------------------------------
# COMSOL "Data" export contract (Stage S0 field data admission)
# ---------------------------------------------------------------------------

class ComsolExportValidationError(ValueError):
    """Raised when a COMSOL Data export violates the field data contract."""


#: Expressions that every S0 transient export must contain at every output time.
S0_REQUIRED_EXPRS: tuple[str, ...] = ("u", "v", "p", "vx-uy", "spf.U")

#: Physical unit that COMSOL must report for each required expression.
S0_EXPECTED_UNITS: dict[str, str] = {
    "u": "m/s",
    "v": "m/s",
    "p": "Pa",
    "vx-uy": "1/s",
    "spf.U": "m/s",
}

#: Unit of the x / y coordinate columns (COMSOL '% Length unit:' metadata line).
S0_EXPECTED_LENGTH_UNIT = "m"

# One header token: '<name>', optionally followed by ' (<unit>)' and ' @ t=<time>'.
_HEADER_TOKEN_RE = re.compile(r"\S+(?:\s+\([^()]*\))?(?:\s+@\s+t=\S+)?")
# One field column token: '<expr> [(<unit>)] @ t=<time>'.
_COLUMN_RE = re.compile(r"^(?P<expr>[^\s()@]+)(?:\s+\((?P<unit>[^()]*)\))?\s+@\s+t=(?P<t>\S+)$")
# One metadata line: '% <Key>: <value>'.
_META_RE = re.compile(r"^%\s*(?P<key>[A-Za-z][A-Za-z ]*?)\s*:\s*(?P<val>.*)$")


def _same_time(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol * max(1.0, abs(a), abs(b))


def _parse_header_columns(
    body: str,
    lineno: int,
    required_exprs: tuple[str, ...],
    units_map: dict[str, str],
    expected_times: Any,
    time_tol: float,
) -> tuple[list[str], list[dict[str, Any]], list[float]]:
    """Decompose and validate the '% x y <expr> (<unit>) @ t=<t> ...' header line."""
    tokens = _HEADER_TOKEN_RE.findall(body)
    if "".join(tokens).replace(" ", "") != body.replace(" ", ""):
        raise ComsolExportValidationError(
            f"Header line {lineno} could not be fully tokenized (unbalanced unit parentheses?): {body!r}"
        )
    if len(tokens) < 3 or tokens[0] != "x" or tokens[1] != "y":
        raise ComsolExportValidationError(
            f"Invalid COMSOL header: expected coordinate columns 'x' and 'y' followed by field columns, got {tokens[:3]}"
        )

    col_meta: list[dict[str, Any]] = []
    unit_by_expr: dict[str, str] = {}
    for col_idx, tok in enumerate(tokens[2:], start=2):
        m = _COLUMN_RE.match(tok)
        if not m:
            raise ComsolExportValidationError(
                f"Column {col_idx} token does not match pattern '<expr> [(<unit>)] @ t=<time>': {tok!r}"
            )
        expr, unit, t_str = m.group("expr"), (m.group("unit") or "").strip(), m.group("t")
        try:
            t_val = float(t_str)
        except ValueError as err:
            raise ComsolExportValidationError(f"Column {col_idx} has invalid time value {t_str!r}") from err
        if not math.isfinite(t_val):
            raise ComsolExportValidationError(f"Column {col_idx} has non-finite time value {t_str!r}")

        for prev in col_meta:
            if prev["expr"] == expr and _same_time(prev["time"], t_val, time_tol):
                raise ComsolExportValidationError(
                    f"Duplicate expression-time column: {expr!r} @ t={t_str} appears in columns "
                    f"{prev['col_idx']} and {col_idx}"
                )

        if expr in unit_by_expr and unit_by_expr[expr] != unit:
            raise ComsolExportValidationError(
                f"Inconsistent unit for expression {expr!r}: {unit_by_expr[expr]!r} vs {unit!r} (column {col_idx})"
            )
        unit_by_expr[expr] = unit

        if expr in required_exprs and unit != units_map[expr]:
            raise ComsolExportValidationError(
                f"Unit mismatch for expression {expr!r} in column {col_idx}: expected "
                f"{units_map[expr]!r}, COMSOL header reports {unit!r}"
            )

        col_meta.append({"col_idx": col_idx, "expr": expr, "unit": unit, "time": t_val, "t_str": t_str, "token": tok})

    # Cluster distinct output times (in header order of first appearance, then sorted).
    times: list[float] = []
    for c in col_meta:
        if not any(_same_time(c["time"], t, time_tol) for t in times):
            times.append(c["time"])
    times.sort()

    reference_exprs: list[str] | None = None
    for t in times:
        t_exprs = sorted(c["expr"] for c in col_meta if _same_time(c["time"], t, time_tol))
        missing = [req for req in required_exprs if req not in t_exprs]
        if missing:
            raise ComsolExportValidationError(
                f"Time point t={t} is missing required expressions: {missing} (found: {t_exprs})"
            )
        if reference_exprs is None:
            reference_exprs = t_exprs
        elif t_exprs != reference_exprs:
            raise ComsolExportValidationError(
                f"Inconsistent time-step structure: t={t} has expressions {t_exprs}, "
                f"t={times[0]} has {reference_exprs}"
            )

    if expected_times is not None:
        exp = sorted(float(t) for t in expected_times)
        if len(exp) != len(times) or not all(_same_time(a, b, time_tol) for a, b in zip(exp, times)):
            raise ComsolExportValidationError(
                f"Exported time points {times} do not match expected time points {exp}"
            )

    return tokens, col_meta, times


def _read_and_validate_comsol_export(
    file_path: Path | str,
    required_exprs: tuple[str, ...],
    expected_units: dict[str, str] | None,
    expected_times: Any,
    expected_length_unit: str | None,
    time_tol: float,
    keep_rows: bool,
) -> tuple[dict[str, Any], list[list[float]]]:
    path = Path(file_path)
    if not path.is_file():
        raise ComsolExportValidationError(f"Export file not found: {path}")
    if path.stat().st_size == 0:
        raise ComsolExportValidationError(f"Export file is empty: {path}")

    units_map = dict(S0_EXPECTED_UNITS if expected_units is None else expected_units)
    undeclared = [e for e in required_exprs if e not in units_map]
    if undeclared:
        raise ComsolExportValidationError(f"No expected unit declared for required expressions: {undeclared}")

    meta: dict[str, str] = {}
    tokens: list[str] | None = None
    col_meta: list[dict[str, Any]] = []
    times: list[float] = []
    ncols = 0
    num_rows = 0
    rows: list[list[float]] = []
    col_min: list[float] = []
    col_max: list[float] = []
    nonfinite_cols: set[int] = set()

    with path.open("r", encoding="utf-8", errors="replace") as f:
        for lineno, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line:
                continue
            if line.startswith("%"):
                if tokens is not None:
                    raise ComsolExportValidationError(
                        f"Unexpected '%' line {lineno} after the data header (multiple data blocks are not allowed)"
                    )
                body = line[1:].strip()
                if body == "x" or body.startswith("x "):
                    tokens, col_meta, times = _parse_header_columns(
                        body, lineno, required_exprs, units_map, expected_times, time_tol
                    )
                    ncols = len(tokens)
                    col_min = [math.inf] * ncols
                    col_max = [-math.inf] * ncols
                    continue
                m = _META_RE.match(line)
                if m:
                    meta[m.group("key").strip()] = m.group("val").strip()
                continue

            if tokens is None:
                raise ComsolExportValidationError(
                    f"Numerical data at line {lineno} precedes the COMSOL '% x y ...' header"
                )
            parts = line.split()
            if len(parts) != ncols:
                raise ComsolExportValidationError(
                    f"Column count mismatch at line {lineno}: header declares {ncols} columns, row has {len(parts)}"
                )
            try:
                vals = [float(p) for p in parts]
            except ValueError as err:
                raise ComsolExportValidationError(f"Non-numeric value at line {lineno}: {err}") from err
            for i, v in enumerate(vals):
                if not math.isfinite(v):
                    nonfinite_cols.add(i)
                else:
                    if v < col_min[i]:
                        col_min[i] = v
                    if v > col_max[i]:
                        col_max[i] = v
            num_rows += 1
            if keep_rows:
                rows.append(vals)

    if tokens is None:
        raise ComsolExportValidationError(f"Could not locate header line starting with '% x' in {path}")

    dim = meta.get("Dimension")
    if dim is not None and dim != "2":
        raise ComsolExportValidationError(f"Expected a 2D export with x/y coordinates, metadata reports Dimension={dim!r}")

    length_unit = meta.get("Length unit")
    if expected_length_unit is not None and length_unit is not None and length_unit != expected_length_unit:
        raise ComsolExportValidationError(
            f"Coordinate unit mismatch: expected '% Length unit: {expected_length_unit}', got {length_unit!r}"
        )

    n_expr_meta = meta.get("Expressions")
    if n_expr_meta is not None and n_expr_meta.isdigit() and int(n_expr_meta) != len(col_meta):
        raise ComsolExportValidationError(
            f"Header declares {len(col_meta)} field columns but metadata reports Expressions={n_expr_meta}"
        )

    if num_rows == 0:
        raise ComsolExportValidationError(f"No numerical data rows found in {path}")

    n_nodes_meta = meta.get("Nodes")
    if n_nodes_meta is not None and n_nodes_meta.isdigit() and int(n_nodes_meta) != num_rows:
        raise ComsolExportValidationError(
            f"Row count {num_rows} does not match metadata Nodes={n_nodes_meta}"
        )

    if nonfinite_cols:
        bad = sorted(nonfinite_cols)
        raise ComsolExportValidationError(
            f"Data validation failed: NaN or Inf found in columns {bad} "
            f"({[tokens[i] for i in bad][:5]}) of {path}"
        )

    field_ranges: dict[str, list[float]] = {}
    for c in col_meta:
        lo, hi = col_min[c["col_idx"]], col_max[c["col_idx"]]
        rng = field_ranges.setdefault(c["expr"], [lo, hi])
        rng[0], rng[1] = min(rng[0], lo), max(rng[1], hi)

    summary = {
        "path": str(path.resolve()),
        "num_rows": num_rows,
        "num_columns": ncols,
        "coordinate_columns": ["x", "y"],
        "length_unit": length_unit,
        "times": times,
        "num_times": len(times),
        "expressions": {c["expr"]: c["unit"] for c in col_meta},
        "columns_per_time": len(col_meta) // len(times),
        "x_range": [col_min[0], col_max[0]],
        "y_range": [col_min[1], col_max[1]],
        "field_ranges": field_ranges,
        "column_meta": col_meta,
        "comsol_metadata": meta,
    }
    return summary, rows


def validate_comsol_export(
    file_path: Path | str,
    required_exprs: tuple[str, ...] = S0_REQUIRED_EXPRS,
    expected_units: dict[str, str] | None = None,
    expected_times: Any = None,
    expected_length_unit: str | None = S0_EXPECTED_LENGTH_UNIT,
    time_tol: float = 1e-9,
) -> dict[str, Any]:
    """Validate a COMSOL 'Data' export against the field data contract.

    Pure-Python streaming check (no optional dependencies) used as the
    pre-publish admission gate. Rejects (``ComsolExportValidationError``):

    - missing / unparseable '% x y ...' header, data before header, multiple blocks
    - coordinate columns other than leading 'x', 'y'; coordinate unit != expected
    - field tokens not of the form '<expr> [(<unit>)] @ t=<time>'
    - duplicate expression-time combinations
    - required expression missing at any time point, or inconsistent per-time structure
    - unit of a required expression differing from ``expected_units`` (or inconsistent across times)
    - exported time points differing from ``expected_times`` (when given)
    - row column count != header column count; non-numeric values
    - zero data rows; Nodes / Expressions metadata disagreeing with the data
    - any NaN / Inf sample

    Returns a summary dict (row/column counts, times, units, value ranges).
    """
    summary, _ = _read_and_validate_comsol_export(
        file_path, tuple(required_exprs), expected_units, expected_times,
        expected_length_unit, time_tol, keep_rows=False,
    )
    return summary


def parse_comsol_txt(
    file_path: Path | str,
    target_time: float | None = 0.5,
    required_exprs: tuple[str, ...] = S0_REQUIRED_EXPRS,
    expected_units: dict[str, str] | None = None,
    expected_length_unit: str | None = S0_EXPECTED_LENGTH_UNIT,
) -> dict[str, Any]:
    """Parse a COMSOL exported field text file after full contract validation.

    Columns are located exclusively through the header metadata
    ('<expr> (<unit>) @ t=<time>'); no column position is assumed.
    See :func:`validate_comsol_export` for the rejection rules.
    """
    import numpy as np

    summary, rows = _read_and_validate_comsol_export(
        file_path, tuple(required_exprs), expected_units, None,
        expected_length_unit, 1e-9, keep_rows=True,
    )
    data = np.atleast_2d(np.asarray(rows, dtype=float))
    col_meta = summary["column_meta"]
    times = summary["times"]
    metadata = {
        "num_nodes": summary["num_rows"],
        "column_meta": col_meta,
        "validation": {k: v for k, v in summary.items() if k != "column_meta"},
    }

    if target_time is not None:
        matching_times = [t for t in times if abs(t - target_time) < 1e-5]
        if not matching_times:
            raise KeyError(f"Target time t={target_time} not found in exported timesteps: {times}")
        matched_t = matching_times[0]
        t_cols = {c["expr"]: c["col_idx"] for c in col_meta if _same_time(c["time"], matched_t, 1e-9)}

        return {
            "x": data[:, 0],
            "y": data[:, 1],
            "time": matched_t,
            "u": data[:, t_cols["u"]],
            "v": data[:, t_cols["v"]],
            "p": data[:, t_cols["p"]],
            "vorticity": data[:, t_cols["vx-uy"]],
            "U": data[:, t_cols["spf.U"]],
            "timesteps": times,
            "metadata": metadata,
        }

    return {
        "x": data[:, 0],
        "y": data[:, 1],
        "raw_data": data,
        "timesteps": times,
        "metadata": metadata,
    }
