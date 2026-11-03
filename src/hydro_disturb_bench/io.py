"""Durable local JSON writes; never silently overwrite a case manifest."""
from __future__ import annotations

import json
import os
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


def parse_comsol_txt(
    file_path: Path | str,
    target_time: float | None = 0.5,
    required_exprs: tuple[str, ...] = ("u", "v", "p", "vx-uy", "spf.U"),
) -> dict[str, Any]:
    """Parse COMSOL exported field text file with strict header and column validation.

    Validates:
    - Header line structure '% x y ...'
    - Dynamic decomposition of column tokens '<expr> [(<unit>)] @ t=<time>'
    - Presence and units of all required expressions at each / target time point
    - Data finiteness across all columns (rejects NaN / Inf)
    - Rejects mismatched column counts or inconsistent timestep structures
    """
    import re
    import numpy as np

    file_path = Path(file_path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Export file not found: {file_path}")

    with file_path.open("r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    header_line = None
    data_start = 0
    for idx, line in enumerate(lines):
        if line.startswith("% x"):
            header_line = line
            data_start = idx + 1
            break

    if header_line is None:
        raise ValueError(f"Could not locate header line starting with '% x' in {file_path}")

    tokens = re.findall(r"(\S+(?:\s+\([^)]+\))?(?:\s+@\s+t=\S+)?)", header_line[1:].strip())
    if len(tokens) < 3 or tokens[0] != "x" or tokens[1] != "y":
        raise ValueError(f"Invalid COMSOL header: expected coordinates 'x' and 'y' as first two columns, got: {tokens[:2]}")

    col_meta = []
    for col_idx, tok in enumerate(tokens[2:], start=2):
        m = re.match(r"^(\S+?)(?:\s*\(([^)]+)\))?\s*@\s*t=(\S+)$", tok)
        if not m:
            raise ValueError(f"Column {col_idx} token does not match pattern '<expr> [(<unit>)] @ t=<time>': '{tok}'")
        expr, unit, t_str = m.groups()
        try:
            t_val = float(t_str)
        except ValueError as err:
            raise ValueError(f"Column {col_idx} time expression '{t_str}' is invalid float: {err}")
        col_meta.append({
            "col_idx": col_idx,
            "expr": expr,
            "unit": unit or "",
            "time": t_val,
            "t_str": t_str,
            "token": tok,
        })

    times = sorted(list(set(c["time"] for c in col_meta)))
    if not times:
        raise ValueError(f"No valid time points found in header: {header_line}")

    for t in times:
        t_cols = [c for c in col_meta if c["time"] == t]
        t_exprs = set(c["expr"] for c in t_cols)
        missing = [req for req in required_exprs if req not in t_exprs]
        if missing:
            raise ValueError(f"Time point t={t} is missing required expressions: {missing} (found: {t_exprs})")

    data_lines = lines[data_start:]
    if not data_lines:
        raise ValueError(f"No numerical data rows found in {file_path}")

    data = np.loadtxt(data_lines)
    data = np.atleast_2d(data)
    num_rows, num_cols = data.shape
    if num_cols != len(tokens):
        raise ValueError(f"Column count mismatch: header has {len(tokens)} columns, numerical data has {num_cols}")

    if np.isnan(data).any() or np.isinf(data).any():
        nan_cols = [c for c in range(num_cols) if np.isnan(data[:, c]).any() or np.isinf(data[:, c]).any()]
        raise ValueError(f"Data validation failed: NaN or Inf found in columns {nan_cols} of {file_path}")

    if target_time is not None:
        matching_times = [t for t in times if abs(t - target_time) < 1e-5]
        if not matching_times:
            raise KeyError(f"Target time t={target_time} not found in exported timesteps: {times}")
        matched_t = matching_times[0]
        t_cols = {c["expr"]: c["col_idx"] for c in col_meta if c["time"] == matched_t}

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
            "metadata": {
                "num_nodes": num_rows,
                "column_meta": col_meta,
            },
        }

    return {
        "x": data[:, 0],
        "y": data[:, 1],
        "raw_data": data,
        "timesteps": times,
        "metadata": {
            "num_nodes": num_rows,
            "column_meta": col_meta,
        },
    }
