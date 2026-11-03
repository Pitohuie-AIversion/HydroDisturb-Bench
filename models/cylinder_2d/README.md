# COMSOL 2D Cylinder Flow Models

This directory contains configuration, documentation, and metadata for 2D cylinder CFD models in HydroDisturb-Bench.

> **Note on `.mph` files**: Due to their large binary nature and licensing environment dependencies, raw `*.mph` simulation binaries are ignored by Git (see `.gitignore`).

---

## 1. Model Baseline Differentiation

HydroDisturb-Bench maintains a strict separation between two cylinder benchmarks:

| Model | Path | Purpose | Key Attributes | Inflow & Boundary Conditions |
|---|---|---|---|---|
| **Original Channel Flow Baseline** | `case/cylinder_flow.mph` | Read-only regression & API verification | Schäfer–Turek DFG benchmark style; channel height $H=0.41\,\text{m}$, cylinder $D=0.1\,\text{m}$ ($24.4\%$ blockage ratio). | Parabolic inlet profile $U(y) = 4 U_m y (H - y) / H^2$, no-slip top and bottom channel walls. |
| **Stage S0 Re=100 External Flow Benchmark** | `models/cylinder_2d/re100_benchmark.mph` | Low-blockage underwater flow benchmark for disturbance injection & world model datasets | Domain $0.40\,\text{m} \times 0.24\,\text{m}$, cylinder $D=0.01\,\text{m}$ at $(0.10, 0.12)\,\text{m}$ ($4.17\%$ blockage ratio $< 5\%$). | Uniform inlet $U_\infty = 0.0100581\,\text{m/s}$, symmetry top/bottom boundaries, no-slip cylinder wall ($u=v=0$). |

---

## 2. Generating the Re=100 Benchmark Case

The Re=100 benchmark model is programmatically constructed from the baseline template using the official COMSOL Java API bridge:

```bash
# Using the HydroDisturb CLI:
python -m hydro_disturb_bench.cli java-build-re100 case/cylinder_flow.mph models/cylinder_2d/re100_benchmark.mph

# Or using the Python bridge:
from hydro_disturb_bench.comsol_java import build_re100_benchmark
build_re100_benchmark("case/cylinder_flow.mph", "models/cylinder_2d/re100_benchmark.mph")
```

### Physical Parameters
- **Fluid**: Water at $20^\circ\text{C}$ ($\rho = 998.2\,\text{kg/m}^3$, $\mu = 1.004 \times 10^{-3}\,\text{Pa}\cdot\text{s}$)
- **Cylinder Diameter**: $D = 0.01\,\text{m}$
- **Target Reynolds Number**: $Re = 100$
- **Free-Stream Velocity**: $U_\infty = \frac{Re \cdot \mu}{\rho \cdot D} = 0.01005810\,\text{m/s}$
- **Upstream Fetch**: $10D = 0.10\,\text{m}$
- **Downstream Fetch**: $30D = 0.30\,\text{m}$
- **Lateral Half-Width**: $12D = 0.12\,\text{m}$ (Total domain width $24D = 0.24\,\text{m}$)
- **Blockage Ratio**: $\frac{D}{H} = \frac{0.01}{0.24} \approx 4.17\% < 5\%$

---

## 3. Short-Time Solution & Data Verification

To run a verification study and export node-level CFD data:

```bash
python scripts/verify_and_plot_re100_s0.py
```

This exports flow field coordinates, velocities ($u, v$), pressure ($p$), and out-of-plane vorticity ($\omega = \partial v/\partial x - \partial u/\partial y = \text{vx} - \text{uy}$) without modifying the base model.
