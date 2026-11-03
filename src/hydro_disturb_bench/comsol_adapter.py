"""Optional COMSOL MPh bridge; no model or solver available in repository."""

from __future__ import annotations

from pathlib import Path
from typing import Any

REQUIRED_PARAMETERS = (
    "Re",
    "D",
    "rho",
    "nu",
    "Uin",
    "A_accel",
    "xc",
    "yc",
    "sigma",
    "t0",
    "duration",
    "dirx",
    "diry",
    "Tend",
    "dt_out",
)


def _start(cores: int):
    if cores < 1:
        raise ValueError("cores must be positive")
    try:
        import mph
    except ImportError as err:
        raise RuntimeError("MPh not installed. Run: pip install -e '.[comsol]'") from err
    return mph.start(cores=cores)


def probe_model(model_file: Path, *, cores: int = 1) -> list[str]:
    model_file = Path(model_file)
    if not model_file.is_file() or model_file.suffix.lower() != ".mph":
        raise FileNotFoundError(f"Missing COMSOL .mph model: {model_file}")
    client = _start(cores)
    model = client.load(str(model_file))
    return sorted(model.parameters().keys())


def _parameter_values(case: dict[str, Any]) -> dict[str, str]:
    p, h, tt = case["physical"], case["hidden_truth"], case["time"]
    return {
        "Re": f"{p['Re']:.12g}",
        "D": f"{p['D_m']:.12g}[m]",
        "rho": f"{p['rho_kg_m3']:.12g}[kg/m^3]",
        "nu": f"{p['nu_m2_s']:.12g}[m^2/s]",
        "Uin": f"{p['Uin_m_s']:.12g}[m/s]",
        "A_accel": f"{h['A_accel_m_s2']:.12g}[m/s^2]",
        "xc": f"{h['xc_m']:.12g}[m]",
        "yc": f"{h['yc_m']:.12g}[m]",
        "sigma": f"{h['sigma_m']:.12g}[m]",
        "t0": f"{h['t0_s']:.12g}[s]",
        "duration": f"{h['duration_s']:.12g}[s]",
        "dirx": f"{h['dirx']:.12g}",
        "diry": f"{h['diry']:.12g}",
        "Tend": f"{tt['end_s']:.12g}[s]",
        "dt_out": f"{tt['output_dt_s']:.12g}[s]",
    }


def solve_single_case(
    model_file: Path, case: dict[str, Any], output_file: Path, *, cores: int = 4
) -> None:
    model_file = Path(model_file)
    output_file = Path(output_file)
    if not model_file.is_file() or model_file.suffix.lower() != ".mph":
        raise FileNotFoundError(f"Missing source .mph model: {model_file}")
    if output_file.suffix.lower() != ".mph":
        raise ValueError("Output model must have .mph extension")
    if model_file.resolve() == output_file.resolve():
        raise ValueError("Refusing to overwrite the original baseline model")
    if output_file.exists():
        raise FileExistsError(f"Refusing to overwrite existing result {output_file}")
    if case.get("status") != "PLANNED_NOT_SIMULATED":
        raise ValueError("Only a planned unsimulated case may be submitted")
    client = _start(cores)
    model = client.load(str(model_file))
    params = model.parameters()
    missing = set(REQUIRED_PARAMETERS) - set(params)
    if missing:
        raise ValueError(
            f"COMSOL model is missing parameters: {sorted(missing)}; no parameters were changed"
        )
    values = _parameter_values(case)
    # All expected parameters must already exist. Configuration and physical source term
    # in the COMSOL model still require independent inspection/verification.
    for k, v in values.items():
        model.parameter(k, v)
    model.solve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    model.save(str(output_file))
