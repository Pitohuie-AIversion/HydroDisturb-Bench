# Numerical and data quality gates

None of the following gates have been completed in this scaffold. Passing Python unit tests does not validate a CFD solution.

## S0: unforced cylinder baseline

1. Verify geometry dimensions, fluid units, boundary assignment and non-slip wall.
2. Check mesh quality, at least three spatial resolutions; pressure gauge convention.
3. Evaluate at least three time steps and a domain-size sensitivity test.
4. Compare shedding Strouhal number, mean drag, lift amplitude/RMS and near-wake profiles to a clearly documented literature benchmark with **matching** boundary conditions and force normalizations.
5. Measure conservation residuals and solver convergence, and document acceptance tolerances before production.

## S1: localized forcing

1. Check whether COMSOL volume force expects N/m³; validate conversion `rho * a`.
2. Check force center, width, direction and onset in both dimensional and nondimensional units.
3. Numerically integrate the **actual discrete force** across domain and time; compare to intended source and mesh convergence.
4. Verify transient kinetic-energy and momentum response, solver time step resolution, boundary consistency, and run-to-run reproducibility.
5. Record source field even if the machine-learning input must not see it.

## Dataset production

- Treat nonconverged/partial trajectories as rejected, keep logs and reasons.
- Save independent seed and case manifest; keep the original model immutable.
- Check for NaN/Inf, field dimensions, timestamps, mesh mapping and mask consistency.
- No overlapping windows between training and validation/test trajectories.
- Do not conflate physical forcing, observation noise, and numerical error.

## Production acceptance checklist

- [ ] Real CFD solver executed and logs preserved
- [ ] S0 spatial/temporal/domain convergence passed
- [ ] S1 source units and momentum balance passed
- [ ] Automated extraction of velocity/pressure/forcing validated
- [ ] Failed cases are flagged rather than silently accepted
- [ ] Independent trajectory-level split verified
