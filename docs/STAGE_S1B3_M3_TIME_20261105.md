# Stage S1-B3 Delivery Report: M3 Internal Time-Step Halving Cross-Check

**Delivery Date**: 2026-11-05  
**Stage Tag**: `S1-B3`  
**Study Type**: Identical M3 Mesh Internal Time-Step Halving Cross-Check (`study_type="m3_time"`)  
**Target Mesh**: Native M3 Mesh (`runs/re100_s1b2_m3_20261105_verified/re100_s1b2_m3_mesh.mph`, 34,752 elements, 17,910 vertices, 88,483 DOFs)  
**Output Directory**: [`runs/re100_s1b3_m3_dt025_20261105_verified`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/runs/re100_s1b3_m3_dt025_20261105_verified)  
**Evidence Directory**: [`docs/evidence/s1b3_m3_time_20261105`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_m3_time_20261105)  
**Acceptance Conclusion**: **`PASS`** (`INSENSITIVE_WITHIN_TESTED_RANGE`)  
**Phase Status**: **CLOSED** (Time discretization sensitivity on M3 mesh verified within engineering tolerance; no further time-step halving required)

---

## 1. Executive Summary & Engineering Decision

In accordance with the review directives of Stage S1-B2:
1. **Engineering Boundary Closed for Mesh Independence**: The spatial grid sensitivity study was closed at M3 without expanding to M4, based on verified $M2 \to M3$ four-metric changes entering the $\le 1.0\%$ envelope with strictly shrinking absolute increments.
2. **Minimal Target Task for S1-B3**: Conduct an isolated internal time-step halving cross-check on the **identical M3 native mesh** without rebuilding geometry or remeshing.
3. **Parameters**: Relative tolerance remained at $\text{rtol} = 10^{-4}$ with intermediate BDF stepping; maximum internal step cap $\Delta t_{\max}$ was halved from $\text{const } 0.05\,\mathrm{s}$ to $\text{const } 0.025\,\mathrm{s}$.
4. **Primary Finding**: Over the last 5 verified stable limit cycles, all four core physical indicators changed by **less than $0.07\%$** (an order of magnitude below the $1.0\%$ engineering threshold):
   - **Strouhal Number $St$**: $+0.0635\%$ (tol $1.0\%$)
   - **Time-Averaged Drag $\langle C_D \rangle$**: $-0.0002\%$ (tol $1.0\%$)
   - **Lift Half-Peak Amplitude $C_L'$**: $+0.0506\%$ (tol $1.0\%$)
   - **De-meaned Lift RMS $C_{L,\text{rms}}'$**: $+0.0532\%$ (tol $1.0\%$)
5. **Phase Decision**: The temporal discretization error on the M3 mesh is insensitive within the tested range. The engineering verification phase for S1 is fully satisfied.

> [!NOTE]
> **Scientific Scope Declaration**: These differences demonstrate that the four limit-cycle statistical indicators are insensitive to internal time-stepping on the M3 grid. This finding does not claim or constitute an exact-solution error bound or proof of local point-wise velocity convergence.

---

## 2. Experimental Setup & Model Invariance

The experiment preserves the complete physics and geometry of the benchmark, modifying strictly the BDF maximum step parameter in an isolated new run directory:

| Parameter / Feature | M3 Baseline Case (`dt=0.05s`) | M3 Halved Time-Step Case (`dt=0.025s`) | Status / Check |
|---|---|---|---|
| **Native Mesh Model** | `re100_s1b2_m3_mesh.mph` | `re100_s1b2_m3_mesh.mph` | **Identical (Read-only)** |
| **Mesh Elements / Vertices** | 34,752 elements / 17,910 vertices | 34,752 elements / 17,910 vertices | **Identical (0 diff)** |
| **Boundary Elements (Cylinder)** | 216 edge segments, 216 quads | 216 edge segments, 216 quads | **Identical (0 diff)** |
| **Total DOFs** | 88,483 DOFs (P1+P1 shape order) | 88,483 DOFs (P1+P1 shape order) | **Identical (0 diff)** |
| **Fluid Density $\rho$** | $998.2\,\mathrm{kg/m^3}$ | $998.2\,\mathrm{kg/m^3}$ | **Identical** |
| **Dynamic Viscosity $\mu$** | $1.004\times 10^{-3}\,\mathrm{Pa\cdot s}$ | $1.004\times 10^{-3}\,\mathrm{Pa\cdot s}$ | **Identical** |
| **Inflow Velocity $U_\infty$** | $0.0100581\,\mathrm{m/s}$ ($\text{Re}=100$) | $0.0100581\,\mathrm{m/s}$ ($\text{Re}=100$) | **Identical** |
| **Relative Tolerance $\text{rtol}$** | $1\times 10^{-4}$ | $1\times 10^{-4}$ | **Identical** |
| **Max Step Constraint Mode** | `const` | `const` | **Identical** |
| **Max Step Setting $\Delta t_{\max}$** | **$0.05\,\mathrm{s}$** | **$0.025\,\mathrm{s}$** | **Halved (Target)** |
| **Output Schedule** | `range(0, 0.2, 200)` (1001 steps) | `range(0, 0.2, 200)` (1001 steps) | **Identical** |
| **Target Output Dir** | `runs/re100_s1b2_m3_20261105_verified` | `runs/re100_s1b3_m3_dt025_20261105_verified` | **Isolated Directory** |

---

## 3. Solver Internal Step Log Audit

The solver accepted step log was extracted from `solver_run.log` and audited step-by-step:

- **Total Logged Internal Steps**: **8,065 steps** (compared to 4,087 steps for baseline, ratio **1.97×**).
- **Time Range Covered**: $t = 0.0\,\mathrm{s} \to 200.01\,\mathrm{s}$ (monotonic strictly increasing, contiguously indexed $1 \dots 8065$).
- **Step Size Range $\Delta t$**:
  - Global: $[0.001731\,\mathrm{s}, 0.025000\,\mathrm{s}]$ (initial startup steps adaptively ramp from $0.0017\,\mathrm{s}$).
  - Fully Developed Regime ($t \ge 160\,\mathrm{s}$): $[0.025000\,\mathrm{s}, 0.025000\,\mathrm{s}]$ (mean: $0.025000\,\mathrm{s}$).
- **Cap Invariance**: Every single logged internal step satisfies $0 < \Delta t \le 0.025\,\mathrm{s} \times (1 + 10^{-4})$.
- **Computational Cost**:
  - Wall-clock Solve Time: **1,516.30 s** (~25.3 min) vs **728.10 s** (~12.1 min), ratio **2.08×**.
  - Multi-threaded CPU Utilization: PID 2704 (`comsolbatch.exe`) sustained ~62% total CPU load across 28 logical cores (75 concurrent worker threads, >22,300 CPU seconds).

---

## 4. Wake Probe Verification (Precheck A & B)

Live probe values at $(x=0.15\,\mathrm{m}, y=0.12\,\mathrm{m})$ were read directly from the newly solved model in memory and compared against the exported TXT time series:

| Time $t$ (s) | Quantity | Direct Evaluation | TXT Time Series | Absolute Difference | Unit | Unit Source | Status |
|---|---|---|---|---|---|---|---|
| **180.0** | $u$ | $6.130370\times 10^{-3}$ | $6.130370\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **180.0** | $v$ | $1.108052\times 10^{-3}$ | $1.108052\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **185.0** | $u$ | $7.466582\times 10^{-3}$ | $7.466582\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **185.0** | $v$ | $-4.237024\times 10^{-3}$ | $-4.237024\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **190.0** | $u$ | $6.718550\times 10^{-3}$ | $6.718550\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **190.0** | $v$ | $-4.228829\times 10^{-3}$ | $-4.228829\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **195.0** | $u$ | $6.462004\times 10^{-3}$ | $6.462004\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **195.0** | $v$ | $1.330875\times 10^{-4}$ | $1.330875\times 10^{-4}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **200.0** | $u$ | $7.521739\times 10^{-3}$ | $7.521739\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |
| **200.0** | $v$ | $4.449705\times 10^{-3}$ | $4.449705\times 10^{-3}$ | $0.00\times 10^0$ | m/s | `comsol_model_export_node` | **MATCH** |

Precheck B confirmed exact parity in all non-target solver parameters (`rtol=1e-4`, `atol=1e-3`, `timemethod=bdf`, `tstepsbdf=intermediate`, `order 1..2`), mesh structure, and physics boundary conditions.

---

## 5. Limit-Cycle Stability & Four-Metric Comparison

Both cases were analyzed using endpoint-interpolated trapezoidal integration over their respective final 5 verified complete limit cycles:
- **Baseline Window**: $t \in [165.70\,\mathrm{s}, 196.07\,\mathrm{s}]$ (5 cycles, duration $30.37\,\mathrm{s}$)
- **Halved Time-Step Window**: $t \in [165.58\,\mathrm{s}, 195.93\,\mathrm{s}]$ (5 cycles, duration $30.35\,\mathrm{s}$)

Cycle-to-cycle stability drifts for the halved case:
- $St$ drift across cycles: **$0.0081\%$**
- $C_D$ drift across cycles: **$0.0001\%$**
- $C_L'$ amplitude drift across cycles: **$0.3519\%$**

### Quantitative Comparison Table

| Physical Metric | Baseline M3 ($\Delta t_{\max}=0.05\,\mathrm{s}$) | Halved M3 ($\Delta t_{\max}=0.025\,\mathrm{s}$) | Absolute Difference $\Delta$ | Relative Change | Engineering Tolerance | Status |
|---|---|---|---|---|---|---|
| **Strouhal Number $St$** | 0.16369976 | 0.16380372 | $+1.040\times 10^{-4}$ | **$+0.0635\%$** | $\le 1.0\%$ | **PASS** |
| **Mean Drag $\langle C_D \rangle$** | 1.36510145 | 1.36509862 | $-2.829\times 10^{-6}$ | **$-0.0002\%$** | $\le 1.0\%$ | **PASS** |
| **Lift Amplitude $C_L'$** | 0.32964921 | 0.32981607 | $+1.669\times 10^{-4}$ | **$+0.0506\%$** | $\le 1.0\%$ | **PASS** |
| **De-meaned RMS $C_{L,\text{rms}}'$** | 0.23355200 | 0.23367636 | $+1.244\times 10^{-4}$ | **$+0.0532\%$** | $\le 1.0\%$ | **PASS** |

### Wake Velocity Comparison ($x=0.15\,\mathrm{m}, y=0.12\,\mathrm{m}$)

Normalized by inflow velocity $U_\infty = 0.0100581\,\mathrm{m/s}$:
- **Streamwise Mean $\langle u \rangle / U_\infty$**: $0.68139 \to 0.68125$ ($\Delta = -0.014\% U_\infty$)
- **Streamwise RMS $u_{\text{rms}}' / U_\infty$**: $0.05011 \to 0.05004$ ($\Delta = -0.006\% U_\infty$)
- **Transverse RMS $v_{\text{rms}}' / U_\infty$**: $0.35489 \to 0.35503$ ($\Delta = +0.014\% U_\infty$)

---

## 6. Closure of Evidence Management Items (P1 & P2 Resolutions)

1. **Manifest and Git Blob Byte Synchronization (P2 Closure)**:
   - **Root Cause Eliminated**: Windows text-mode writing (`write_text()`) previously converted `\n` to `\r\n` (CRLF) locally, causing manifest records to mismatch the LF bytes stored in Git blobs. All evidence writers were switched to strict binary writing (`write_bytes(normalized.encode("utf-8"))`).
   - **Platform-Independent EOL Enforcement**: Added `.gitattributes` configuring `docs/evidence/** text eol=lf` to prevent line-ending translation across platforms.
   - **Git Blob Verification**: Verified using `git cat-file blob` on all 82 files across all three evidence manifests:
     * `docs/evidence/s1b2_m3_20261105/`: 45/45 items **100% MATCH**.
     * `docs/evidence/s1b3_m3_time_20261105/`: 27/27 items **100% MATCH**.
     * `docs/evidence/s1b3_tests_recaptured_20261105_153742/`: 10/10 items **100% MATCH**.
     * **Total Mismatches**: **0 / 82**.

2. **Raw Test Execution Outputs Recovered & Clarified**:
   - Explicitly clarified that `full_regression.txt` and `unit_regression.txt` in `docs/evidence/s1b3_m3_time_20261105/` are structured summary overviews (annotated with `# [REVISED NOTE]`), rather than raw process stdout.
   - Recovered exact stdout/stderr from background tasks directly into:
     - [`docs/evidence/s1b3_m3_time_20261105/raw_task_6195_full_regression_stdout.txt`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_m3_time_20261105/raw_task_6195_full_regression_stdout.txt) (Exit code 0, 275 passed in 311.83s).
     - [`docs/evidence/s1b3_m3_time_20261105/raw_task_6153_unit_regression_stdout.txt`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_m3_time_20261105/raw_task_6153_unit_regression_stdout.txt) (Exit code 0, 245 passed, 30 deselected in 15.78s).
     - [`docs/evidence/s1b3_m3_time_20261105/raw_task_6160_comsol_integration_stdout.txt`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_m3_time_20261105/raw_task_6160_comsol_integration_stdout.txt) (Exit code 0, 4 passed in 119.14s).
   - In addition, executed the independent recapture protocol into a dedicated new directory:
     [`docs/evidence/s1b3_tests_recaptured_20261105_153742/`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_tests_recaptured_20261105_153742)
     Capturing real-time `Tee-Object` stdout, JUnit XML test suite reports (`full_regression.xml`, `m3_time.xml`, `related_modules.xml`), and exact `$LASTEXITCODE` files (`*.exitcode.txt` = `0`).

3. **COMSOL Solver Log Extracted from JSON**:
   - Because `*.log` is gitignored, the solver output was extracted directly from [`runs/re100_s1b3_m3_dt025_20261105_verified/solve_response.json`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/runs/re100_s1b3_m3_dt025_20261105_verified/solve_response.json) into:
     [`docs/evidence/s1b3_m3_time_20261105/solver_run.txt`](file:///d:/%F0%9F%93%81%2001_Workspaces%20%28%E4%BB%A3%E7%A0%81%E4%B8%8E%E5%B7%A5%E7%A8%8B%E5%8C%BA%29/HydroDisturb-Bench/docs/evidence/s1b3_m3_time_20261105/solver_run.txt) (1,117,430 bytes, 16,720 lines of progress output).
   - Explicitly noted as an exported copy of `solve_response.json["log"]`.

4. **Audit Nature Disclaimer**:
   - `artifact_audit.json` updated with an explicit disclaimer confirming it is an agent-compiled summary artifact of the stage execution, and that acceptance relies directly on `m3_time_report.json` and raw solver data.

5. **Protected Models Immutability**:
   - SHA-256 hashes of all 11 baseline and historical `.mph` models were verified before and after execution: **100% unchanged**.
