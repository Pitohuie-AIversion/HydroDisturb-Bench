# Research plan: hidden disturbances in submerged cylinder wakes

## Scientific questions

**Q1 — Hidden parameters.** Given the disturbance mechanism but not its amplitude, position, or timing, how much does historical flow information permit future-state forecasting?

**Q2 — Hidden mechanism.** How well does the model generalize to a forcing mechanism absent from training, rather than merely unseen parameters of a familiar force?

**Q3 — Unpredictable events.** When a future forcing event is statistically independent of the past, can a probabilistic predictor express uncertainty and improve after the next observation arrives?

Do not characterize correct anticipation of an independent random future event as an expected deterministic capability.

## Observability timing protocol

| Event timing | Information available | Task |
| --- | --- | --- |
| Before history window | response already in observed states | hidden-parameter forecasting |
| Within history window | partial response observed | disturbance-conditioned latent state estimation |
| Within future forecast window | forcing may be independent of history | probabilistic forecasting and post-event update |

The first pilot provides event onset metadata for controlled evaluation even when its future values are not visible to the learner.

## Physical regime

- Water at approximately 20°C, cylinder diameter 0.01 m, target `Re=100`.
- 2-D single-phase, constant-density laminar flow; no free surface, bed, bubbles, or moving geometry.
- Three mesh levels, three time step levels, domain-size sensitivity studies before production.
- Two pilot conditions: unforced reference (S0) and one localized Gaussian force (S1).

## Disturbance pool (not all are implemented)

- **A:** inlet pulses, gusts, shear, transverse flow, time-correlated inlet fluctuations.
- **B:** rotation or tangential/normal wall forcing (requires physically consistent wall actuation).
- **C:** Gaussian body acceleration, rotating/divergence-free forcing, moving sources, multi-source forcing.
- **D:** oscillating cylinder, secondary obstacles, changed geometry (may require mesh motion/remeshing).
- **E:** viscosity/density/temperature variation (requires compatible physical equations).
- **F:** passive scalar source/sink (changes scalar transport, not necessarily momentum).
- **G:** sensor noise, missing patches, sparse observations (observation operator only).
- **H:** discretization/model discrepancy (analysis of numerical uncertainty, not a physical forcing label).

**Note:** Local momentum sources are equivalent forces, not automatically high-fidelity models of jets, thrusters, or moving obstacles.

## Proposed dataset record

- Ground truth: velocity, pressure, vorticity, cylinder mask, physical forcing field, force parameters, trajectory ID, random seed, solver and mesh information.
- Visible input: history fields and explicitly known conditions only.
- Target: future fields after a specified prediction horizon.
- Metadata: chronology, forcing onset, solver version, unit convention, numerical acceptance tests.

### Splitting

Split whole simulation trajectories by scenario **before** creating overlapping time windows. Reserve distinct random seeds and parameter combinations; evaluate out-of-distribution positions, amplitudes and unseen mechanisms in separate tests.

## Model evaluation plan (future work)

- Deterministic baselines: persistence, simple reduced-order model, standard neural forecasting baseline.
- Physical diagnostics: mean drag, lift RMS, Strouhal number, downstream velocity/vorticity statistics, spectral characteristics.
- Prediction diagnostics: short/long rollout errors and event-aligned recovery after new observations.
- Probabilistic diagnostics where appropriate: calibration, coverage and proper scoring rules (do not present one sampled future as guaranteed truth).

No model benchmark result is currently claimed.
