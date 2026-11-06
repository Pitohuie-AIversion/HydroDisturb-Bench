# COMSOL model contracts: MPh template, original channel, and Java Re=100/M3

The early MPh parameter adapter, the original channel model, and the validated Java Re=100/M3 workflow have different contracts. The template below is retained for the MPh adapter; its limitations do not describe the whole project. See [development status and evidence](DEVELOPMENT.md) for the current implementation and validation record.

## Early MPh template: required model parameter names

The MPh adapter in `src/hydro_disturb_bench/comsol_adapter.py` requires an existing, manually configured `.mph` model. It does not create geometry, boundaries, or force equations. It checks and sets these **exact** parameter keys, except `eps_t`, which is a separate GUI-model requirement and is not checked or set by that adapter:

| Name | Unit | Meaning |
| --- | --- | --- |
| `Re` | 1 | Reynolds number |
| `D` | m | cylinder diameter |
| `rho` | kg/m^3 | fluid density |
| `nu` | m^2/s | kinematic viscosity |
| `Uin` | m/s | inlet velocity (calculated as Re × nu / D) |
| `A_accel` | m/s^2 | localized acceleration amplitude |
| `xc`, `yc` | m | center of force field |
| `sigma` | m | Gaussian standard deviation |
| `t0`, `duration` | s | forcing onset and duration |
| `dirx`, `diry` | 1 | forcing direction components |
| `eps_t` | s | smoothing width for temporal window (set in GUI) |
| `Tend`, `dt_out` | s | simulation end time and output interval |

The Time Dependent study should explicitly use `range(0,dt_out,Tend)` as its output-time setting; check output time stepping separately from the internal solver step controls. The current adapter sets `Tend` and `dt_out` but does not inspect whether the study actually references them.

Model should use `Uin` at inlet and `rho`, `nu` in material settings. If `Re` is a derived parameter instead, adapt the contract before running. The adapter sets both Re and Uin from a single source of truth; it checks parameter names but not the expressions that depend on them.

## MPh template: fluid model

- 2-D, laminar, single-phase, incompressible Navier–Stokes;
- fixed, impermeable cylinder; non-slip wall;
- uniform nominal inlet, suitable outlet and far-field treatment;
- pressure reporting convention documented (absolute or gauge);
- spatial grid/mesh and time step validated independently.

## MPh template: candidate Gaussian body force

The parameter generator uses acceleration units `m/s²`:

`a(x,y,t) = A_accel * exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2)) * g(t) * (dirx,diry)`

The Laminar Flow interface's volume-force input normally expects **force per volume** `N/m³`. Accordingly, the COMSOL force component expressions should be:

```text
Fx = rho*A_accel*exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2))*dirx*(flc2hs(t-t0,eps_t)-flc2hs(t-t0-duration,eps_t))
Fy = rho*A_accel*exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2))*diry*(flc2hs(t-t0,eps_t)-flc2hs(t-t0-duration,eps_t))
```

Check COMSOL function syntax and units in your installed version. Do not apply these equations to a pressure equation or an incompatible body force interface. `eps_t` must be positive and small relative to `duration` while still being time-resolved. Smoothing changes event onset and integrated impulse; record the actual applied function. Some physics interfaces may define force density differently; verify within that interface.

These `flc2hs` expressions are the early template, not the forcing used in the accepted M3 pulse branches. Those branches use separate `branch_*` parameters and a compact sin-squared temporal envelope, as described below.

### Time and nondimensionalization

`t* = t Uin/D`, `a* = A_accel D/Uin²`. All generated dimensional time values derive from `Uin` and `D`.

### Non-guarantees

The adapter's parameter check does not verify actual boundary selections, PDE choice, solver tolerances, mesh quality, or numeric correctness. Review the GUI model and solution before running a large batch.

## Output contracts by implementation

The MPh adapter only saves a solved `.mph` to a **new output path**. It does not itself export field tensors, masks, force fields, or HDF5 datasets.

The separate Java sampling workflow has validated saved-solution `[u,v,p]` exports and readback with `(T,3,Ny,Nx)` ordering, coordinates, actual saved times, invalid-region masks, actual units and Dataset/model provenance. See the [small full-field sample](STAGE_M3_FIELD_SAMPLING_20261105.md) and [151-frame M3 sequence](STAGE_M3_FIELD_SEQUENCE_20261105.md). The [M3 branch experiment](STAGE_M3_BRANCH_PULSE_20261105.md) also verifies actual body-force node expressions, units and source integrals. These results do not imply that the MPh adapter now exports those artifacts, that a full force-field tensor or HDF5 training dataset has been delivered, or that arbitrary randomized disturbances have been validated.

## Original channel baseline (`case/cylinder_flow.mph`)

The original model audited through the Java API is a 2D Schäfer-Turek-style channel benchmark. It remains a read-only template and API regression source; the following parameters belong to that model, not to the later low-blockage Re=100/M3 cases:

| Name | Expression | Evaluated Value | Unit | Description |
| --- | --- | --- | --- | --- |
| `H` | `0.41[m]` | 0.41 | m | Channel height |
| `L` | `2.2[m]` | 2.2 | m | Channel length |
| `W` | `1[m]` | 1.0 | m | Channel width (out-of-plane, not modeled) |
| `R` | `0.05[m]` | 0.05 | m | Cylinder radius (cylinder diameter $D = 2R = 0.1\text{ m}$) |
| `U_mean` | `1[m/s]` | 1.0 | m/s | Mean inflow velocity |

### Mapping to Planned Parameter Contract

1. **Reynolds Number ($Re$)**: The existing model does not define `Re` as an independent parameter. Instead, $Re = \frac{\rho U_{\text{mean}} (2R)}{\mu}$. Modifying $Re$ for testing requires scaling `U_mean` (or fluid viscosity `mu` in material settings).
2. **Velocity Parameter**: The model uses `U_mean` rather than `Uin` for the parabolic inlet velocity profile: $u(0,y,t) = 4 U_{\text{mean}} y (H - y) / H^2$.
3. **Disturbance Forcing**: Parameters `A_accel`, `xc`, `yc`, `sigma`, `t0`, `duration`, `dirx`, `diry` are absent from this unperturbed channel baseline. The accepted M3 force experiments use independent branch models and `branch_*` parameters; they do not retrofit this original file or establish compatibility with the MPh template.

## Validated Java Re=100 and M3 branches

The Java builder creates an independent low-blockage model with a `0.40 m × 0.24 m` domain, a `0.01 m` cylinder at `(0.10,0.12) m`, uniform inlet and symmetry lateral boundaries. Its parameters include `Re`, `D`, `rho_f`, `mu_f`, `U_inf`, `x_c` and `y_c`; these are not automatic aliases for the MPh keys. Geometry, physical values and build commands are documented in the [model directory](../models/cylinder_2d/README.md).

The saved M3 baseline is `runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_solved.mph`. Its mesh/time studies and subsequent saved-solution sampling are separate from the early S0 smoke test. Pulse branches start from its native `sol1 / solnum 901 / t=180 s` state, preserve native U and UDot through a common checkpoint, and restart BDF rather than restoring its internal history. The accepted source uses `rho_f × acceleration × spatial Gaussian × temporal sin² × direction`, with support at `181–183 s` and actual `N/m³` readback. The [amplitude-series report](reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md) records the zero branch and `0.0025`, `0.005`, `0.010 m/s²` cases, their frozen settings and evidence boundaries. These accepted cases do not establish a general hidden-disturbance training dataset or full-field exact-solution error bound.

