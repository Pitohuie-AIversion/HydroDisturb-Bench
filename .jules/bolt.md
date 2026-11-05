## 2024-11-05 - Initial learning

**Learning:** Optimizing `_cylinder_mask` in `src/hydro_disturb_bench/field_sampling.py` to use NumPy broadcasting instead of `np.meshgrid` is about ~2.5x faster in an offline test, and eliminates the intermediate memory allocation of `(Ny, Nx)` coordinate arrays.

**Action:** Whenever possible, use NumPy broadcasting (e.g. `x[None, :]` and `y[:, None]`) instead of `np.meshgrid` for pairwise/grid operations, particularly inside tight loops or repeated grid calculations like creating masks.
