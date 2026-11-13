"""Compare a native-state restart and a controlled pulse with a frozen baseline.

This is a field comparison, not a claim about exact-solution error. Native-state,
physics-node and model provenance gates belong to the experiment runner.
"""
from __future__ import annotations

import numpy as np

from . import field_sampling as sampling
from .branch_evidence import validate_native_branch_evidence as validate_native_branch_evidence


def _weights(axis):
    intervals = np.diff(axis)
    return np.r_[intervals[0] / 2, (intervals[:-1] + intervals[1:]) / 2,
                 intervals[-1] / 2]


def _validate(data, grid_shape):
    require = sampling._require
    values, times = np.asarray(data["values"]), np.asarray(data["times"])
    x, y = np.asarray(data["x"]), np.asarray(data["y"])
    for name, axis in (("x", x), ("y", y), ("time", times)):
        require(axis.ndim == 1 and len(axis) >= 2 and np.isfinite(axis).all()
                and np.all(np.diff(axis) > 0), f"Invalid increasing {name} axis")
    require([len(x), len(y)] == list(grid_shape), "Grid differs from the frozen plan")
    require(values.dtype == np.float64 and values.shape == (len(times), 3, len(y), len(x)),
            "Invalid float64 (T,3,Ny,Nx) field tensor")
    meta = data["metadata"]
    require(meta.get("tensor_layout") == sampling.LAYOUT
            and meta.get("channel_order") == list(sampling.CHANNELS)
            and meta.get("units") == list(sampling.UNITS), "Invalid field channel/unit contract")
    require(meta.get("shape") == list(values.shape)
            and meta.get("grid_shape") == [len(x), len(y)], "Field metadata shape mismatch")
    require(sampling._same(times, meta.get("actual_times"))
            and sampling._same(times, meta.get("requested_times")), "Actual/requested field times differ")
    mask, cylinder, invalid = (np.asarray(data[key]) for key in ("mask", "cylinder_mask", "invalid_mask"))
    require(mask.dtype == bool and invalid.dtype == bool and cylinder.dtype == bool
            and mask.shape == invalid.shape == (len(times), len(y), len(x))
            and cylinder.shape == (len(y), len(x)), "Invalid field masks")
    require(np.array_equal(mask, invalid | cylinder[None]) and np.all(mask == mask[0]),
            "Inconsistent or time-varying validity mask")
    require(not (invalid & ~cylinder[None]).any(), "Invalid raw samples occur in the fluid domain")
    expanded_mask = np.broadcast_to(mask[:, None], values.shape)
    require(np.isnan(values[expanded_mask]).all() and np.isfinite(values[~expanded_mask]).all(),
            "Masked values must be NaN and fluid values must be finite")


def _velocity(a, b, weights, scale):
    delta = np.where(weights[None, None] > 0, a[:, :2] - b[:, :2], 0)
    return np.sqrt(np.sum(np.sum(delta**2, axis=1) * weights, axis=(1, 2)) / weights.sum()) / scale


def _pressure(a, b, weights, scale):
    left = np.where(weights > 0, a[:, 2], 0)
    right = np.where(weights > 0, b[:, 2], 0)
    mean_left = np.sum(left * weights, axis=(1, 2)) / weights.sum()
    mean_right = np.sum(right * weights, axis=(1, 2)) / weights.sum()
    delta = np.where(weights > 0, left - right - (mean_left - mean_right)[:, None, None], 0)
    rms = np.sqrt(np.sum(delta**2 * weights, axis=(1, 2)) / weights.sum()) / scale
    return rms, mean_left - mean_right


def _summary(per_frame, times):
    return {"per_frame": per_frame.tolist(), "max": float(np.max(per_frame)),
            "time_rms": float(np.sqrt(np.sum(per_frame**2 * _weights(times)) / (times[-1] - times[0])))}


def compare_branch_fields(baseline, zero, pulse, plan):
    """Apply predeclared restart and response gates to three validated sequences.

    Saved times are matched only within the sampling contract's 1e-12 s tolerance;
    there is no nearest-frame substitution. Numerical gate failures return FAIL;
    incompatible data contracts raise ModelValidationError.
    """
    require = sampling._require
    for data in (baseline, zero, pulse):
        _validate(data, plan["grid_shape_nx_ny"])
    times = zero["times"]
    expected = np.linspace(plan["branch_time_s"], plan["stop_s"], plan["frames"])
    require(sampling._same(times, expected) and sampling._same(pulse["times"], times)
            and sampling._same(np.diff(times), np.full(len(times)-1, plan["output_step_s"])),
            "Branch does not cover the frozen contiguous time grid")
    indices = []
    for time in times:
        hits = np.flatnonzero(np.abs(baseline["times"] - time) <= sampling.TIME_ATOL)
        require(len(hits) == 1, "Baseline lacks a unique matching saved time")
        indices.append(int(hits[0]))
    require(np.all(np.diff(indices) == 1), "Baseline matching frames are not contiguous")
    for data in (baseline, pulse):
        for key in ("x", "y", "cylinder_mask"):
            require(np.array_equal(data[key], zero[key]), f"Branch {key} differs from baseline")
        require(np.array_equal(data["mask"][0], zero["mask"][0]), "Branch fluid mask differs")
    weights = _weights(zero["y"])[:, None] * _weights(zero["x"])[None]
    weights = np.where(zero["mask"][0], 0, weights)
    require(weights.sum() > 0, "No fluid area in comparison")
    bounds = plan["response_acceptance"]["roi_m"]
    eps = sampling.COORD_ATOL
    # ⚡ Bolt: using NumPy broadcasting instead of np.meshgrid to avoid (Ny, Nx) memory allocation
    x_mask = (zero["x"] >= bounds[0] - eps) & (zero["x"] <= bounds[1] + eps)
    y_mask = (zero["y"] >= bounds[2] - eps) & (zero["y"] <= bounds[3] + eps)
    roi_weights = weights * (x_mask[None, :] & y_mask[:, None])
    require(roi_weights.sum() > 0, "No fluid area in the frozen ROI")
    U = float(plan["scales"]["U_inf_m_s"])
    pressure_scale = float(plan["scales"]["rho_f_kg_m3"]) * U**2
    require(np.isfinite([U, pressure_scale]).all() and U > 0 and pressure_scale > 0,
            "Invalid physical comparison scales")
    b, z, p = baseline["values"][indices], zero["values"], pulse["values"]
    velocity = _summary(_velocity(z, b, weights, U), times)
    pressure_rms, pressure_offset = _pressure(z, b, weights, pressure_scale)
    pressure = _summary(pressure_rms, times)
    restart_roi = _velocity(z, b, roi_weights, U)
    limits = plan["restart_acceptance"]
    gates = {
        "velocity_time_rms": velocity["time_rms"] <= limits["velocity_time_rms_over_U_max"],
        "velocity_frame_rms": velocity["max"] <= limits["velocity_frame_rms_over_U_max"],
        "pressure_time_rms": pressure["time_rms"] <= limits["centered_pressure_time_rms_over_rhoU2_max"],
        "pressure_frame_rms": pressure["max"] <= limits["centered_pressure_frame_rms_over_rhoU2_max"],
    }
    restart = {"passed": all(gates.values()), "gates": gates, "velocity": velocity,
               "pressure_centered": pressure, "pressure_mean_offset_Pa": pressure_offset.tolist(),
               "roi_velocity_error_per_frame": restart_roi.tolist()}
    response_roi = _velocity(p, z, roi_weights, U)
    response_pressure, response_pressure_offset = _pressure(p, z, weights, pressure_scale)
    rules = plan["response_acceptance"]
    eps = sampling.TIME_ATOL
    pre_event = times <= rules["pre_event_end_s"] + eps
    left, right = rules["response_window_s"]
    in_window = ((times > left + eps) if rules["response_window_left_open"] else (times >= left - eps)) & (times <= right + eps)
    require(pre_event.any() and in_window.any(), "Missing pre-event or response-window frames")
    noise = float(np.max(restart_roi))
    pre_noise = float(np.max(restart_roi[pre_event]))
    pre_bound = max(rules["pre_event_floor_over_U"], rules["pre_event_noise_multiplier"] * pre_noise)
    pre_peak = float(np.max(response_roi[pre_event]))
    peak = float(np.max(response_roi[in_window]))
    required_peak = max(rules["response_floor_over_U"], rules["response_noise_multiplier"] * noise)
    pre_passed = pre_peak <= pre_bound
    detectable = peak >= required_peak
    response = {"passed": bool(restart["passed"] and pre_passed and detectable),
                "pre_event_passed": bool(pre_passed), "above_noise_floor": bool(detectable),
                "peak_roi_over_U": peak, "noise_floor_over_U": noise,
                "required_peak_over_U": required_peak, "pre_event_peak_over_U": pre_peak,
                "pre_event_bound_over_U": pre_bound, "pre_event_noise_over_U": pre_noise,
                "roi_velocity_per_frame_over_U": response_roi.tolist(),
                "velocity": _summary(_velocity(p, z, weights, U), times),
                "pressure_centered": _summary(response_pressure, times),
                "pressure_mean_offset_Pa": response_pressure_offset.tolist(),
                "peak_time_s": float(times[np.flatnonzero(in_window)[np.argmax(response_roi[in_window])]])}
    return {"schema_version": 1, "status": "PASS" if response["passed"] else "FAIL",
            "scope": "Sampled-field gates; native initialization and applied-force evidence are checked separately",
            "times": times.tolist(), "baseline_frame_indices": indices,
            "scales": {"velocity_m_s": U, "pressure_Pa": pressure_scale},
            "fluid_quadrature_area_m2": float(weights.sum()), "roi_quadrature_area_m2": float(roi_weights.sum()),
            "restart": restart, "response": response}
