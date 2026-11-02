# COMSOL model contract (initial / not yet implemented in COMSOL)

A manually authored `.mph` model is needed. Do not assume the adapter creates geometry, boundaries, or force equations.

## Required model parameter names

The MPh adapter expects these **exact** parameter keys in the COMSOL model:

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

## Fluid model

- 2-D, laminar, single-phase, incompressible Navier–Stokes;
- fixed, impermeable cylinder; non-slip wall;
- uniform nominal inlet, suitable outlet and far-field treatment;
- pressure reporting convention documented (absolute or gauge);
- spatial grid/mesh and time step validated independently.

## Gaussian body force

The parameter generator uses acceleration units `m/s²`:

`a(x,y,t) = A_accel * exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2)) * g(t) * (dirx,diry)`

The Laminar Flow interface's volume-force input normally expects **force per volume** `N/m³`. Accordingly, the COMSOL force component expressions should be:

```text
Fx = rho*A_accel*exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2))*dirx*(flc2hs(t-t0,eps_t)-flc2hs(t-t0-duration,eps_t))
Fy = rho*A_accel*exp(-((x-xc)^2+(y-yc)^2)/(2*sigma^2))*diry*(flc2hs(t-t0,eps_t)-flc2hs(t-t0-duration,eps_t))
```

Check COMSOL function syntax and units in your installed version. Do not apply these equations to a pressure equation or an incompatible body force interface. `eps_t` must be positive and small relative to `duration` while still being time-resolved. Smoothing changes event onset and integrated impulse; record the actual applied function. Some physics interfaces may define force density differently; verify within that interface.

### Time and nondimensionalization

`t* = t Uin/D`, `a* = A_accel D/Uin²`. All generated dimensional time values derive from `Uin` and `D`.

### Non-guarantees

The adapter's parameter check does not verify actual boundary selections, PDE choice, solver tolerances, mesh quality, or numeric correctness. Review the GUI model and solution before running a large batch.

## Output contract

The first adapter only saves a solved `.mph` to a **new output path**. It does **not** export `u`, `v`, `p`, pressure convention, mesh mask, force field, or HDF5 dataset yet. Implement and validate those separately; do not describe such files as available until verified.
