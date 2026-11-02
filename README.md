# HydroDisturb-Bench

**A reproducible CFD case-generation and hidden-disturbance benchmark for submerged cylinder wakes.**

> **Status: research scaffold / NOT a validated CFD dataset.** No COMSOL `.mph` file or solver result is included. CFD numerical verification and COMSOL–MPh integration must be performed on the target installation before using output as ground truth.
>
> **Platform Support**: The officially supported and formally accepted target environment for development and remote compute servers is **Windows 11 / Windows Server 64-bit with COMSOL Multiphysics 6.4 (Win64)**. Linux compatibility is experimental and remains unaccepted in this phase; the project makes no claims of verified cross-platform production support.

## Scope

The initial benchmark models an **infinitely long cylinder approximated by a 2-D domain** submerged in constant-density, single-phase freshwater. It targets Reynolds number `Re = 100` and tests future flow-state prediction under hidden forcing. Free-surface waves, seabed effects, cavitation, three-dimensional transition, and full FSI are out of scope.

A simulator knows every forcing event; the downstream prediction model is deliberately denied selected parameters. The experiment design distinguishes disturbances already observable in the history from those occurring unpredictably in the future.

## Stack

- CFD case authoring and inspection: **COMSOL Multiphysics (GUI)**
- Programmatic control: **Python + MPh** (optional; COMSOL needs to be installed and configured)
- Reproducible parameter sampling and case manifests: **Python standard library**
- Independent numerical baseline: optional OpenFOAM / independent reference benchmark
- Training-set export: **planned** (HDF5 or Zarr; not implemented yet)

**Codex does not need an MCP server** to write and execute these Python scripts. MCP is an optional future interface to interact with a running COMSOL server.

## Quick start (no COMSOL required)

Requires Python >= 3.10. Run a deterministic case-generation pilot:

```bash
python -m pip install -e '.[dev]'
hydrobench plan --config configs/base_case.json --count 20 --seed 2026 --out runs/pilot
pytest -q
```

This writes `runs/pilot/manifest.json` and individual `case.json` files. These are **simulation inputs only**, not simulation results.

To see a proposed case:

```bash
hydrobench inspect --case runs/pilot/cases/case_0000.json
```

## Connect your COMSOL model (optional)

1. In COMSOL GUI, create and validate the 2-D cylinder baseline, then save `models/cylinder_2d/cylinder_base.mph` (locally; `.mph` is gitignored).
2. Define parameter names and physical equations as specified in [`docs/COMSOL_MODEL_CONTRACT.md`](docs/COMSOL_MODEL_CONTRACT.md). A local body-force expression must be added in the COMSOL model itself; modifying `A_accel` alone will **not** create a forcing term.
3. Install the optional interface: `python -m pip install -e '.[comsol]'`.
4. Perform a read-only connection check:

   ```bash
   hydrobench probe --model models/cylinder_2d/cylinder_base.mph
   ```

5. After reviewing the model and a generated case, run **one** non-production study:

   ```bash
   hydrobench solve --model models/cylinder_2d/cylinder_base.mph \
     --case runs/pilot/cases/case_0000.json \
     --output runs/pilot/results/case_0000.mph --cores 4
   ```

`solve` writes a separate `.mph` copy; it does **not** export a validated training dataset. The first real run should be supervised and its output compared against a known cylinder-wake benchmark.

## Model assumptions and starting conditions

| Parameter | Initial setting |
| --- | --- |
| Geometry | fixed, circular, 2-D cylinder |
| Water | freshwater at approximately 20°C |
| Density | 998.2 kg/m³ |
| Kinematic viscosity | 1.004e-6 m²/s |
| Diameter | 0.01 m |
| Reynolds number | 100 |
| Nominal speed | Re × ν / D ≈ 0.01004 m/s |
| Domain | 40D length × 20D height; inlet 10D upstream (candidate only) |

Mesh, time step, outlet conditions, force integration, and other settings require verification before use as a scientific baseline.

## Research progression

| Stage | Goal | Gate |
| --- | --- | --- |
| S0 | Unforced cylinder baseline | Strouhal, lift/drag, mesh/time/domain convergence |
| S1 | Single, hidden Gaussian acceleration source | Units, spatial integral, momentum balance, time history |
| S2 | Inlet pulses and rotational/moving forcings | Reproducible across random seeds and boundary settings |
| S3 | Multiple hidden sources | No train/validation/test trajectory leakage |
| S4 | Sparse observations and probabilistic forecasting | Separate pre-event prediction from post-event filtering |

See [`docs/RESEARCH_PLAN.md`](docs/RESEARCH_PLAN.md) and [`docs/VALIDATION.md`](docs/VALIDATION.md).

## Repository layout

```text
configs/                      Baseline physical setup and sampling bounds
src/hydro_disturb_bench/      Deterministic case planner and optional MPh adapter
models/cylinder_2d/          COMSOL model placement (example placeholder only)
docs/                         Physical model contract, protocol, validation
scripts/                      Developer helpers
tests/                        Runnable tests not requiring COMSOL
runs/                         Generated results (gitignored)
```

## Reproducibility and safe use

- Store a fresh copy of the original `.mph` model before running automatic edits; never solve into the original input path.
- Preserve solver version, mesh, physics configuration, units, seed, and hidden forcing truth with every trajectory.
- Split data by **whole simulation trajectories**, never by overlapping time windows.
- Retain failed-run diagnostics; a finished process is not proof of a physically valid simulation.
- Do not commit private licenses, tokens, model data, bulk simulation results, or local paths.

## Project status

- [x] Deterministic parameter sampling and manifest generation
- [x] Config validation and basic tests
- [x] Optional MPh single-case connection/solve interface (not tested against a real installation)
- [ ] Validated COMSOL `.mph` baseline
- [ ] Validated forcing expression in COMSOL GUI
- [ ] Conservative output sampling and HDF5 export
- [ ] Automated CFD-quality gates
- [ ] CPU cost benchmarks and independent solver comparison

No validated hydrodynamic results or research conclusions are claimed.

## Publish to GitHub

See [`docs/PUBLISHING.md`](docs/PUBLISHING.md). A `gh` CLI script is included for Windows PowerShell and Bash. It verifies `general-chen` and creates the repo **private by default** after confirmation.
