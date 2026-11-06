# HydroDisturb-Bench

**Reproducible COMSOL cylinder-wake experiments and controlled hidden-disturbance branches.**

As of **2026-11-06**, `main` contains a Re=100 baseline, time/mesh sensitivity studies, verified `[u,v,p]` sampling, a continuous 151-frame baseline, and native-checkpoint pulse branches at three nonzero amplitudes. The latest experiment delivery is [`195d613`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/195d613decee0f960b86f2dd588592f60fc01856).

The numerical acceptance applies to the documented configurations and selected statistics. This is a **controlled experiment prototype**, not a completed unknown-disturbance generalization benchmark or a bound on the full-field error relative to an exact solution.

Start with the [development status and evidence index](docs/DEVELOPMENT.md), [changelog](CHANGELOG.md), [validation ledger](docs/VALIDATION.md), and [research plan](docs/RESEARCH_PLAN.md).

## Current results

| Area | Delivered on `main` | Evidence |
| --- | --- | --- |
| COMSOL execution | Official Java API bridge; isolated outputs, failure records, native unit/source readback and Windows startup recovery | [API integration](docs/COMSOL_API_INTEGRATION.md) |
| Temporal sensitivity | A/B/C/D comparisons; C/D changes below 0.1% for four stable-cycle statistics | [Case D](docs/STAGE_S1B1_CASE_D_20261104.md) |
| Spatial sensitivity | Native M0–M3 meshes; final M2/M3 changes below the 1% engineering threshold | [M3 comparison](docs/STAGE_S1B2_M3_20261105.md) |
| Time check on M3 | Internal step cap 0.05 → 0.025 s; four changes below 0.07% | [M3 time check](docs/STAGE_S1B3_M3_TIME_20261105.md) |
| Field sampling | Five saved times on two grids, then 151 consecutive frames at 161×97 | [Small sample](docs/STAGE_M3_FIELD_SAMPLING_20261105.md), [continuous sequence](docs/STAGE_M3_FIELD_SEQUENCE_20261105.md) |
| Controlled futures | Shared native state at 180 s; zero branch plus acceleration amplitudes 0.0025, 0.005 and 0.010 m/s² | [Amplitude series](docs/reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md) |

The latest archived offline regression is **514 passed, 30 deselected** ([raw log](docs/evidence/m3_pulse_amplitude_series_20261106/logs/offline_regression_after_remote_sync/stdout.txt)). The two new physical solves have separate COMSOL evidence. These are recorded execution results, not a claim that every fresh clone or platform has rerun them.

The **COMSOL MCP server and Universal Software MCP Builder plugin are on the separate, unmerged `feat/comsol-mcp` branch**. Their implementation and verification boundaries are listed in the [development index](docs/DEVELOPMENT.md#未合入的开发).

## Stack and supported environment

- Python ≥3.10 for planning, validation and data organization; NumPy/SciPy for analysis.
- Official COMSOL Java API through `comsolbatch` or the documented standalone fallback; the actual experiment records use COMSOL 6.4 on Windows.
- Optional MPh adapter for the original generic parameter contract. It is separate from the Java workflow used by the accepted experiments.
- NPZ plus JSON manifests for structured field samples and sequences. HDF5/Zarr production packaging and ML training remain future work.

Windows Win64 with COMSOL 6.4 is the supported execution target; the archived runs substantiate the tested local installation. Windows Server is a deployment target, not a claim of acceptance on every server. Linux solver execution remains experimental. Offline tests on Linux do not establish COMSOL integration support.

## Quick start without COMSOL or local simulation data

```bash
python -m pip install -e '.[dev]'
hydrobench plan --config configs/base_case.json --count 20 --seed 2026 --out runs/pilot
hydrobench inspect --case runs/pilot/cases/case_0000.json
python -m pytest -q tests/test_planning.py
```

Use a new or empty output directory. The planner creates **unsimulated inputs**, not flow data. Its initial `configs/base_case.json` is not the frozen M3 experimental configuration; see the [model contract](docs/COMSOL_MODEL_CONTRACT.md).

To review the latest lightweight delivery without COMSOL or local large files, use its fixed experiment commit and a new output filename:

```bash
python -X utf8 docs/evidence/m3_pulse_amplitude_series_20261106/audit_delivery.py --git-ref 195d613decee0f960b86f2dd588592f60fc01856 --output amplitude_delivery_review_01.json
```

This checks committed evidence and provenance records; it does not recompute full tensors or native solution vectors. Do not substitute a later documentation commit or `HEAD` for the experiment commit.

## Development and real COMSOL execution

The [developer guide](docs/DEVELOPMENT.md) maps scripts to their effects and required data. The [Java API guide](docs/COMSOL_API_INTEGRATION.md) provides CLI syntax and execution contracts. Install `.[analysis]` when plotting; install `.[comsol]` only for the MPh adapter.

The GitHub Actions workflow uses uv with a committed lockfile, explicit development/analysis dependencies, and separate test categories. To run the same data-free checks locally:

```bash
uv sync --locked --extra dev --extra analysis
uv run --no-sync ruff check .
uv run --no-sync pytest -q --strict-markers -m "not integration and not artifact"
uv run --no-sync python scripts/check_repo.py
```

Tests marked `artifact` verify retained local models/results and fail if the required data are missing; they are not part of data-free CI. In a checkout with those historical fixtures, `python -m pytest -q -m "not integration"` includes that audit. Tests marked `integration` require COMSOL and may solve models. The archived 514-test result predates this test separation and remains a record of that execution. See [test scope and known limits](docs/DEVELOPMENT.md#测试与已知限制) and the [CI and Python refactoring report](docs/reports/CODE_HEALTH_CI_20261106.md).

The subsequent [filesystem transaction extraction](docs/reports/FILE_TRANSACTION_REFACTOR_20261106.md) preserves the existing public API and transaction behavior, with separate compatibility, failure-retention and cross-process lock checks. It does not add a new CFD accuracy result.

Saved field data now support [bounded batch/range reads and family-separated window indexes](docs/DATA_USAGE.md). The registered M3 example uses five existing sequences (555 frames) to build 105 windows from 5 history + 5 future frames with stride 5. All five sequences share one ancestor family and stay in train; validation/test remain empty. The learner-facing reader returns only flow, masks, coordinates and times. [Actual-data audit and regression evidence](docs/reports/DATA_USAGE_20261106.md) are lightweight; models and full tensors remain in local data storage.

## Physical and data scope

The accepted Java benchmark is 2-D, single-phase, constant-density laminar flow around a fixed circular cylinder. The domain is 0.40×0.24 m, with a 0.01 m cylinder centered at (0.10, 0.12) m; density is 998.2 kg/m³ and dynamic viscosity is 1.004e−3 Pa·s, giving `U∞ ≈ 0.0100581 m/s` at Re=100. This differs from the original channel-flow API fixture and from the generic planner's candidate values.

The selected M3 setup uses 34,752 native mesh elements and 88,483 reported solver DOFs, with `rtol=1e-4`, maximum internal step mode `const`, cap 0.05 s and output interval 0.2 s. The cap is not a fixed-step integrator. Native restart branches additionally use the disclosed `consistent=off` protocol; they do not restore the original BDF internal history.

Structured fields use `float64`, shape `(T,3,Ny,Nx)`, channels `[u,v,p]`, increasing physical coordinates, exact saved times and explicit invalid-region masks with NaN entries. Increasing the sampling-grid resolution does not refine the native CFD mesh.

Free surfaces, moving geometry, passive scalars and multiple forcing mechanisms are outside the implemented prototype. Domain-size sensitivity, independent-solver comparison, full momentum-budget closure and model-training evaluation are not claimed complete.

## Repository and data storage

```text
src/hydro_disturb_bench/  Planner, Java/MPh interfaces, field and branch validators
scripts/                Stage-specific experiment, sampling and audit entrypoints
configs/                Generic planner inputs and native mesh plans
tests/                  Offline, local-fixture and COMSOL integration tests
docs/                   Current guides, historical reports and lightweight evidence
models/cylinder_2d/     Model documentation; local MPH files are gitignored
runs/                   Local simulation data and full samples (gitignored)
```

Code, frozen configuration, compact readbacks, solver/test logs, metrics and file identity manifests go into Git. New MPH files, native vectors, complete field TXT/NPZ and large failure archives stay in data storage. Existing historical archives are retained. Local-file hashes identify retained data; they do not mean that a remote reviewer has inspected it or that an independent backup exists.

Keep accepted models and historical outputs unchanged. Use a new output directory for new work, preserve failed-run records, and split future datasets by trajectory/checkpoint family before creating overlapping windows. See [publishing and retention](docs/PUBLISHING.md) for the current repository workflow.
