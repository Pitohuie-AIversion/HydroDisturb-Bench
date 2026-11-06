# COMSOL 2D Cylinder Flow Models

This directory contains configuration, documentation, and metadata for 2D cylinder CFD models in HydroDisturb-Bench.

See the [development status and evidence index](../../docs/DEVELOPMENT.md) and [model contracts](../../docs/COMSOL_MODEL_CONTRACT.md) for the distinction between the early MPh template and the validated Java workflows.

> **Note on `.mph` files**: Due to their large binary nature and licensing environment dependencies, raw `*.mph` simulation binaries are ignored by Git (see `.gitignore`).

---

## 1. Model Baseline Differentiation

HydroDisturb-Bench separates the original channel template from the Java Re=100 model and its later M3 solutions:

| Model | Path | Purpose | Key Attributes | Inflow & Boundary Conditions |
|---|---|---|---|---|
| **Original Channel Flow Baseline** | `case/cylinder_flow.mph` | Read-only regression & API verification | Schäfer–Turek DFG benchmark style; channel height $H=0.41\,\text{m}$, cylinder $D=0.1\,\text{m}$ ($24.4\%$ blockage ratio). | Parabolic inlet profile $U(y) = 4 U_m y (H - y) / H^2$, no-slip top and bottom channel walls. |
| **Stage S0 Re=100 External Flow Benchmark** | `models/cylinder_2d/re100_benchmark.mph` | Low-blockage underwater flow benchmark for disturbance injection & world model datasets | Domain $0.40\,\text{m} \times 0.24\,\text{m}$, cylinder $D=0.01\,\text{m}$ at $(0.10, 0.12)\,\text{m}$ ($4.17\%$ blockage ratio $< 5\%$). | Uniform inlet $U_\infty = 0.0100581\,\text{m/s}$, symmetry top/bottom boundaries, no-slip cylinder wall ($u=v=0$). |
| **Validated M3 saved solution** | `runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_solved.mph` | Baseline for saved-field sampling and controlled native-state pulse branches | Same Re=100 geometry and physical setup, with the separately studied M3 solver mesh. | Preserve the Re=100 baseline boundaries; body forces are introduced only in independent branch models. |

The early MPh adapter expects keys such as `Uin`, `rho`, `nu` and `A_accel`. The Java Re=100 model uses `U_inf`, `rho_f`, `mu_f` and separate `branch_*` pulse parameters. The original channel file is not interchangeable with either contract and remains unchanged.

---

## 2. Generating the Re=100 Benchmark Case

The Re=100 benchmark model is programmatically constructed from the baseline template using the official COMSOL Java API bridge:

```bash
# Using the HydroDisturb CLI:
python -m hydro_disturb_bench.cli java-build-re100 --model case/cylinder_flow.mph --output models/cylinder_2d/re100_benchmark.mph
```

```python
# Or using the Python bridge:
from hydro_disturb_bench.comsol_java import build_re100_benchmark
build_re100_benchmark("case/cylinder_flow.mph", "models/cylinder_2d/re100_benchmark.mph")
```

These are model-construction commands, not commands to review existing evidence. The output must be a new path; the CLI rejects an existing destination unless overwrite is explicitly enabled. Preserve the existing S0 and M3 artifacts when creating another example.

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

## 3. Historical S0 Short-Time Solve & Data Verification

The following historical script actually calls `run_study` for `range(0,0.05,0.5)` and then exports and plots the resulting fields. It is not a read-only review of an already saved M3 solution:

```bash
python scripts/verify_and_plot_re100_s0.py
```

This exports flow field coordinates, velocities ($u, v$), pressure ($p$), and out-of-plane vorticity ($\omega = \partial v/\partial x - \partial u/\partial y = \text{vx} - \text{uy}$) without modifying the base model.

The script uses the fixed directory `runs/re100_s0_verification` and enables overwrite for its solved model and field export. Do not rerun it merely to review existing results; use the archived reports and evidence instead.

## 4. Current M3 Sampling and Pulse Evidence

The Java workflow has already validated [two-resolution full-field sampling](../../docs/STAGE_M3_FIELD_SAMPLING_20261105.md) and a [151-frame continuous baseline](../../docs/STAGE_M3_FIELD_SEQUENCE_20261105.md) from the existing M3 solution without re-solving CFD. Arrays use `(T,3,Ny,Nx)` ordering with `[u,v,p]`, explicit coordinates, saved times, masks and actual unit/source checks. Increasing this sampling resolution does not refine the native solver mesh.

The [native-state branch experiment](../../docs/STAGE_M3_BRANCH_PULSE_20261105.md) and [three-amplitude series](../../docs/reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md) subsequently solved independent zero/forced futures from the same native `t=180 s` state. Their compact sin² body-force pulse and actual source-integral checks are distinct from the early MPh `flc2hs` template. These are validated controlled examples; they do not establish a randomized training dataset or an exact-solution error guarantee.
