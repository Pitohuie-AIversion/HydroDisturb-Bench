# Research plan: hidden disturbances in submerged cylinder wakes

Updated **2026-11-06**, against the accepted experiment delivery [`195d613`](https://github.com/Pitohuie-AIversion/HydroDisturb-Bench/commit/195d613decee0f960b86f2dd588592f60fc01856). This is the current research roadmap; dated stage reports remain historical records. Implementation and evidence navigation are in [DEVELOPMENT.md](DEVELOPMENT.md), with acceptance boundaries in [VALIDATION.md](VALIDATION.md).

## Current position

| Workstream | Current state | Next decision |
| --- | --- | --- |
| Baseline and numerical sensitivity | Long-time stable wake, A–D time study, M0–M3 native mesh series and M3 time-cap check completed for selected engineering statistics | Reuse M3 with `rtol=1e-4 / const / 0.05 s`; no new M4 or extra time-step scan is scheduled |
| Data interface | Five-frame/two-grid sample and 151-frame continuous `[u,v,p]` baseline accepted | Reuse loaders, exact saved-time selection, provenance and masks |
| Native-state branching | Zero and 0.005 m/s² pulse branches accepted; 0.0025 and 0.010 m/s² added from the same checkpoint | Freeze this small amplitude group as a controlled prototype |
| Dataset design | One checkpoint family with three nonzero amplitudes; no train/test benchmark yet | Define independent trajectory/checkpoint groups, observation windows and hidden metadata before scaling |
| New mechanisms and forecasting | Not implemented or evaluated as a dataset/model benchmark | Separate future tasks; do not combine them with routine evidence review |

The existing groups show distinct physical futures above the measured restart difference. They do not establish generalization to unseen disturbances or a globally linear response law. All three reported response peaks lie at the right boundary of the preregistered `(181,186] s` window; they are window maxima, not full-trajectory maxima.

## Scientific questions

**Q1 — Hidden parameters.** Given the disturbance mechanism but not its amplitude, position, or timing, how much does historical flow information permit future-state forecasting?

**Q2 — Hidden mechanism.** How well does a model generalize to a mechanism absent from training, rather than merely unseen parameters of a familiar force?

**Q3 — Unpredictable events.** When a future forcing event is statistically independent of the past, can a probabilistic predictor express uncertainty and improve after the next observation arrives?

Correct anticipation of an independent random future event is not an expected deterministic capability. “Hidden” means withheld from the learner; the simulator must retain the exact forcing truth.

## Observability timing protocol

| Event timing | Information available | Task |
| --- | --- | --- |
| Before history window | Response already in observed states | Hidden-parameter forecasting |
| Within history window | Partial response observed | Disturbance-conditioned latent state estimation |
| Within future forecast window | Forcing may be independent of history | Probabilistic forecasting and post-event update |

The implemented branches start at 180 s from native `sol1 / solnum 901`; the pulse acts during 181–183 s. Source position, direction, amplitude and timing are retained for evaluation. No prediction model or visibility filter has been trained or validated yet.

## Physical regime and accepted configuration

- Fixed 2-D cylinder, diameter 0.01 m, target Re=100, single-phase incompressible laminar water flow.
- Actual Java benchmark domain 0.40×0.24 m; density 998.2 kg/m³, dynamic viscosity 1.004e−3 Pa·s. The original generic planner configuration is a separate candidate contract.
- Native M3 mesh and maximum internal time-step cap 0.05 s; output interval 0.2 s. Four stable-cycle indicators passed the defined sensitivity thresholds. Domain-size sensitivity and independent-solver validation remain separate, unfinished research tasks.
- Branches copy the same native U and UDot, then restart BDF with the frozen `consistent=off` protocol. This protocol differs from the original continuous baseline and is assessed using zero-branch minus baseline differences.
- The implemented force is a localized Gaussian body acceleration, converted using density to volume-force units, with a compact sin² time envelope. The three nonzero amplitudes are 0.0025, 0.005 and 0.010 m/s². Other physical settings are frozen in the [series plan](evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json).

Local momentum sources are equivalent forces, not automatically faithful models of jets, thrusters or moving obstacles. Source-integral checks do not replace a complete momentum balance including boundaries and fluxes.

## Dataset record and splitting

The delivered field format is NPZ with JSON metadata: `(T,3,Ny,Nx)` `float64`, `[u,v,p]`, physical x/y arrays, saved times, cylinder/invalid masks, source identity, units and hashes. The continuous baseline covers 170–200 s at 0.2 s; each branch covers 180–200 s at the same interval. Full data remain in local storage, with necessary evidence and identity inventories in Git.

Future production records may add vorticity, forcing-field tensors, explicit learner-visible views, trajectory-family identifiers, dataset splits and HDF5/Zarr packaging. Those additions are not delivered by the present `[u,v,p]` sequence alone.

Split whole trajectory/checkpoint families **before** constructing overlapping windows. Sibling branches sharing a checkpoint and common history should remain grouped unless an explicitly defined evaluation protocol requires otherwise. Reserve independent histories, seeds and parameter combinations; evaluate unseen amplitudes, positions and mechanisms separately. Randomly splitting overlapping windows from the 151-frame baseline would leak raw frames between partitions.

Before a larger run, specify the number of independent starting states, parameter ranges, visible history, forecast horizon, split policy and data budget. The current three-amplitude family is useful for exercising that design but cannot substitute for independent test cases.

## Disturbance pool and evaluation: future work

| Family | Examples | Current implementation |
| --- | --- | --- |
| Body acceleration | Gaussian pulse, moving/rotating sources, multiple sources | One fixed Gaussian/sin² pulse; three nonzero amplitudes |
| Boundary actuation | Inlet pulses, shear, wall rotation | Not part of the accepted branch dataset |
| Geometry/material changes | Oscillating cylinder, secondary obstacles, viscosity variation | Not implemented as controlled dataset mechanisms |
| Passive scalar | Source/sink and transport | Not added |
| Observation changes | Noise, missing regions, sparse sensors | Future observation operators, not physical force labels |
| Numerical discrepancy | Mesh/time/model uncertainty | Studied for selected baseline statistics; not a physical disturbance label |

Future model evaluation can compare persistence, reduced-order and neural baselines using rollout errors, force/frequency statistics and event-aligned recovery. For independent future events, use calibration, coverage and proper probabilistic scores. No model benchmark, unknown-disturbance generalization result or production dataset release is currently claimed.