"""Generate deterministic hidden-force case parameter sets."""
from __future__ import annotations

import math
import random
from typing import Any
from .config import base_quantities, validate_config


def sample_cases(config: dict[str, Any], *, count: int, seed: int) -> list[dict[str, Any]]:
    validate_config(config)
    if count <= 0 or count > 100_000:
        raise ValueError("count must be between 1 and 100000")
    rng = random.Random(seed)
    cfg = config["disturbance"]
    base = base_quantities(config)
    d, u, tc = base["D_m"], base["Uin_m_s"], base["convective_time_s"]
    rows=[]
    for i in range(count):
        v={k:rng.uniform(*cfg[k]) for k in ("xc_D","yc_D","sigma_D","a_star","t0_star","duration_star","theta_rad")}
        theta = v["theta_rad"]
        rows.append({
            "case_id":f"case_{i:04d}", "seed":seed, "forcing_type":cfg["type"],
            "physical":base.copy(),
            "hidden_truth":{
                **v,
                "xc_m":v["xc_D"]*d, "yc_m":v["yc_D"]*d,
                "sigma_m":v["sigma_D"]*d,
                "A_accel_m_s2":v["a_star"]*u*u/d,
                "t0_s":v["t0_star"]*tc,
                "duration_s":v["duration_star"]*tc,
                "dirx":math.cos(theta), "diry":math.sin(theta)
            },
            "time": {
                "end_star":config["time"]["end_star"],
                "output_dt_star":config["time"]["output_dt_star"],
                "end_s":config["time"]["end_star"]*tc,
                "output_dt_s":config["time"]["output_dt_star"]*tc,
            },
            "status":"PLANNED_NOT_SIMULATED"
        })
    return rows
