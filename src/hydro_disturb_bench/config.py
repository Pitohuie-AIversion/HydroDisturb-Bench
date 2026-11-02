"""Validated physical configuration and nondimensional conversion."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

REQUIRED_KEYS = {"fluid", "cylinder", "domain", "time", "disturbance"}
RANGE_KEYS = ("xc_D", "yc_D", "sigma_D", "a_star", "t0_star", "duration_star", "theta_rad")


def read_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_config(config)
    return config


def _positive(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return float(value)


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict) or not REQUIRED_KEYS.issubset(config):
        raise ValueError(f"Missing top-level keys: {sorted(REQUIRED_KEYS - set(config)) if isinstance(config, dict) else 'invalid configuration'}")
    f, c, d, t, force = (config[key] for key in ("fluid", "cylinder", "domain", "time", "disturbance"))
    for v,n in ((f["density_kg_m3"],"density"),(f["kinematic_viscosity_m2_s"],"nu"),(c["diameter_m"],"D"),(c["reynolds"],"Re"),(d["upstream_D"],"upstream"),(d["downstream_D"],"downstream"),(d["half_height_D"],"half height"),(t["end_star"],"end time"),(t["output_dt_star"],"output timestep")):
        _positive(v,n)
    if force["type"] != "gaussian_body_acceleration":
        raise ValueError("Only gaussian_body_acceleration is implemented for case planning")
    for key in RANGE_KEYS:
        rng=force[key]
        if not isinstance(rng,list) or len(rng)!=2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in rng) or rng[0]>rng[1]:
            raise ValueError(f"Invalid finite interval for {key}")
    if force["sigma_D"][0]<=0 or force["duration_star"][0]<=0 or force["a_star"][0]<0:
        raise ValueError("Source width and duration must be positive; amplitude must not be negative")
    if force["t0_star"][1] + force["duration_star"][1] >= t["end_star"]:
        raise ValueError("Forcing may extend beyond the simulated time interval")
    if force["xc_D"][0] <= 0 or force["xc_D"][1] >= d["downstream_D"]:
        raise ValueError("Force center must lie inside downstream fluid region")
    if max(abs(x) for x in force["yc_D"]) + 3*force["sigma_D"][1] >= d["half_height_D"]:
        raise ValueError("Force should remain away from top/bottom boundaries")


def base_quantities(config: dict[str, Any]) -> dict[str, float]:
    fluid, cylinder = config["fluid"], config["cylinder"]
    nu = fluid["kinematic_viscosity_m2_s"]
    diameter = cylinder["diameter_m"]
    velocity = cylinder["reynolds"] * nu / diameter
    return {"rho_kg_m3": float(fluid["density_kg_m3"]), "nu_m2_s": float(nu), "D_m": float(diameter), "Re": float(cylinder["reynolds"]), "Uin_m_s": velocity, "convective_time_s":diameter/velocity}
