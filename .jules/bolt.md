
## 2025-02-12 - Vectorizing IO with NumPy
**Learning:** Pure python iteration parsing over file streams (especially large numerical timeseries output by scientific models) creates an I/O and object-creation bottleneck that scales poorly. `np.loadtxt` offers significant C-level performance improvements at the cost of losing some strict granular error logging per-line.
**Action:** Always prefer vectorized library methods over line-by-line manual string processing loops. Ensure arguments like `ndmin=2` are supplied to matrix loaders in edge cases where files might unexpectedly have only 1 row (to avoid silent dimensions loss).
