---
name: comsol-api
description: Direct control, inspection, and safe execution of COMSOL Multiphysics models using the official COMSOL Java API and comsolbatch/comsolcompile tools.
---

# COMSOL Java API Skill (HydroDisturb-Bench)

This skill provides verified, safe, and programmatic control over COMSOL Multiphysics models for HydroDisturb-Bench using the official COMSOL Java API.

## 1. Environment & Architecture

- **COMSOL Installation Root**: `D:\Program Files\COMSOL64\Multiphysics` (auto-detected via Windows Registry `HKLM:\SOFTWARE\COMSOL\COMSOL64\COMSOLROOT` or `COMSOL_ROOT` env).
- **Installed Version**: COMSOL Multiphysics 6.4 (Build 429), Win64.
- **Bundled Java Environment**: OpenJDK 21.0.11 LTS located at `java\win64\jre\bin\java.exe` & `javac.exe`.
- **Official Toolchain**:
  - Compiler: `bin\win64\comsolcompile.exe`
  - Batch Runner: `bin\win64\comsolbatch.exe`
  - Live Server: `bin\win64\comsolmphserver.exe`
- **Official Documentation**:
  - `doc\pdf\COMSOL_Multiphysics\COMSOL_ProgrammingReferenceManual.pdf` (Chapter 1 & 2: `ModelUtil`, `ModelNode`, `Parameters`, `Study`, `Batch Execution`).

## 2. Core Safety Contracts

1. **Baseline Model Immutability**:
   - The original model file (e.g., `case/cylinder_flow.mph`) is strictly read-only.
   - Its SHA-256 hash is computed and recorded **before** and **after** every operation. If the hash changes, the bridge aborts and raises `MODEL_VALIDATION_FAILED`.
2. **Copy-on-Write**:
   - All parameter mutations and solver executions must specify an output path distinct from the baseline model. In-place overwriting of the source file is rejected.
3. **Dynamic Discovery (No Blind Guessing)**:
   - Node tags (`comp1`, `geom1`, `spf`, `std1`) and parameter names (`U_mean`, `H`, etc.) must not be blindly assumed. Agents must invoke `inspect_model` first to discover active tags.
4. **Strict Error Contract**:
   Operations report structured errors mapped to standard error codes:
   - `MODEL_NOT_FOUND`: Target model file missing.
   - `API_UNSUPPORTED`: Requested Java API not supported in current version.
   - `NODE_NOT_FOUND`: Referenced component, geometry, mesh, physics, or study tag does not exist.
   - `INVALID_PARAMETER`: Parameter does not exist or expression syntax is invalid.
   - `INVALID_UNIT`: Parameter unit is inconsistent or invalid in COMSOL unit system.
   - `COMSOL_CONNECTION_FAILED`: COMSOL batch runner / compiler could not be launched or crashed.
   - `MODEL_VALIDATION_FAILED`: Immutability violation or corrupted model output.
   - `SOLVER_FAILED`: COMSOL solver aborted or generated no solution nodes.

## 3. Tool Scripts & Usage

All scripts reside in `.agents/skills/comsol-api/scripts/` and can be invoked from the CLI or Python subprocess.

### 3.1 `inspect_api.py`
Inspects current COMSOL installation, verified binaries, and officially documented Java API methods.

```bash
python .agents/skills/comsol-api/scripts/inspect_api.py
```

### 3.2 `inspect_model.py`
Reads the complete structure of a `.mph` model (tags, dimensions, geometries, meshes, physics features, parameters, studies, solvers) without modifying the file.

```bash
python .agents/skills/comsol-api/scripts/inspect_model.py case/cylinder_flow.mph --out runs/comsol_api_smoke/inspection.json
```

### 3.3 `update_parameter.py`
Modifies a verified parameter on an independent working copy and verifies the change by immediate readback.

```bash
python .agents/skills/comsol-api/scripts/update_parameter.py \
  case/cylinder_flow.mph \
  runs/working_copy.mph \
  U_mean "1.15[m/s]" \
  --descr "Adjusted inlet velocity"
```

### 3.4 `run_study.py`
Solves a specified Study node on a working copy model and outputs solver execution logs and solved `.mph` file.

```bash
python .agents/skills/comsol-api/scripts/run_study.py \
  runs/working_copy.mph \
  runs/solved_output.mph \
  --study std1 \
  --tlist "range(0,0.05,0.1)" \
  --log-out runs/comsol_solve.log
```

## 4. Python API Integration

The Python bridge is exposed via `hydro_disturb_bench.comsol_java`:

```python
from hydro_disturb_bench.comsol_java import (
    inspect_api,
    inspect_model,
    update_parameter,
    run_study,
    ModelNotFoundError,
    InvalidParameterError,
    SolverFailedError,
)

# 1. Inspect API
api_info = inspect_api()

# 2. Inspect Model
model_tree = inspect_model("case/cylinder_flow.mph")

# 3. Safe Copy & Param Update
res_param = update_parameter("case/cylinder_flow.mph", "runs/copy.mph", "U_mean", "1.2[m/s]")

# 4. Run Study
res_solve = run_study("runs/copy.mph", "runs/solved.mph", study_tag="std1", tlist_override="range(0,0.05,0.1)")
```
