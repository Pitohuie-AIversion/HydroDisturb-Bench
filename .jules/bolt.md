## 2024-11-05 - Initial learning

**Learning:** Optimizing `_cylinder_mask` in `src/hydro_disturb_bench/field_sampling.py` to use NumPy broadcasting instead of `np.meshgrid` is about ~2.5x faster in an offline test, and eliminates the intermediate memory allocation of `(Ny, Nx)` coordinate arrays.

**Action:** Whenever possible, use NumPy broadcasting (e.g. `x[None, :]` and `y[:, None]`) instead of `np.meshgrid` for pairwise/grid operations, particularly inside tight loops or repeated grid calculations like creating masks.

## 2025-02-12 - Vectorizing IO with NumPy

**Learning:** Pure python iteration parsing over file streams (especially large numerical timeseries output by scientific models) creates an I/O and object-creation bottleneck that scales poorly. `np.loadtxt` offers significant C-level performance improvements at the cost of losing some strict granular error logging per-line.

**Action:** Always prefer vectorized library methods over line-by-line manual string processing loops. Ensure arguments like `ndmin=2` are supplied to matrix loaders in edge cases where files might unexpectedly have only 1 row (to avoid silent dimensions loss).
