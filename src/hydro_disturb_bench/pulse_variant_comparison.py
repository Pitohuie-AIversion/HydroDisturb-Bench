"""Aligned sampled-field comparisons for the M3 direction/duration study.

The caller verifies native models, configuration identities and per-case gates.
This helper fixes one spatial ROI and response window across all four cases;
pairwise differences are descriptive, with no sign-reversal or linearity gate.
"""
from __future__ import annotations

from . import branch_pulse, field_sampling

CASE_NAMES = ("default", "reverse", "short", "long")
COMMON_ROI_M = (0.12, 0.18, 0.09, 0.15)
COMMON_WINDOW_S = (181.0, 188.0)


def _check_plan(plan):
    require = field_sampling._require
    require(isinstance(plan, dict) and isinstance(plan.get("response_acceptance"), dict),
            "Missing shared comparison plan")
    rules = plan["response_acceptance"]
    require(field_sampling._same(rules.get("roi_m"), COMMON_ROI_M),
            "Common comparison ROI differs from the registered study")
    require(field_sampling._same(rules.get("response_window_s"), COMMON_WINDOW_S)
            and rules.get("response_window_left_open") is True
            and field_sampling._same(rules.get("pre_event_end_s"), COMMON_WINDOW_S[0]),
            "Common comparison window must be (181,188] with pre-event end 181 s")
    require(field_sampling._same(plan.get("branch_time_s"), 180.0)
            and field_sampling._same(plan.get("stop_s"), 200.0)
            and field_sampling._same(plan.get("output_step_s"), 0.2)
            and type(plan.get("frames")) is int and plan["frames"] == 101,
            "Common comparison requires the unchanged 180..200 s / 0.2 s schedule")


def _metrics(response):
    return {
        "common_window_peak_roi_over_U": response["peak_roi_over_U"],
        "common_window_peak_time_s": response["peak_time_s"],
        "roi_velocity_per_frame_over_U": response["roi_velocity_per_frame_over_U"],
        "full_time_velocity_rms_over_U": response["velocity"]["time_rms"],
        "full_time_max_velocity_rms_over_U": response["velocity"]["max"],
        "full_time_centered_pressure_rms_over_rhoU2": response["pressure_centered"]["time_rms"],
        "full_time_max_centered_pressure_rms_over_rhoU2": response["pressure_centered"]["max"],
        "velocity_per_frame_over_U": response["velocity"]["per_frame"],
        "centered_pressure_per_frame_over_rhoU2": response["pressure_centered"]["per_frame"],
        "pressure_mean_offset_Pa": response["pressure_mean_offset_Pa"],
    }


def compare_pulse_variants(baseline, zero, variants, analysis_plan):
    """Compare four validated field sequences using exactly one common setup.

    ``variants`` contains ``default``, ``reverse``, ``short`` and ``long``.
    Every case is first checked against the same baseline/zero with the existing
    branch comparator. NEW-minus-default uses that comparator's weighted norms
    with default as both reference arguments; its detection gates are not used.
    ``status`` describes common-window field gates only, not native evidence or
    the separately required per-case response-window acceptance.
    """
    _check_plan(analysis_plan)
    field_sampling._require(isinstance(variants, dict) and set(variants) == set(CASE_NAMES),
                            "Comparison requires exactly default, reverse, short and long cases")
    cases = {}
    for name in CASE_NAMES:
        report = branch_pulse.compare_branch_fields(baseline, zero, variants[name], analysis_plan)
        response = report["response"]
        noise = response["noise_floor_over_U"]
        cases[name] = {
            **_metrics(response),
            "common_window_field_status": report["status"],
            "restart_passed": report["restart"]["passed"],
            "pre_event_passed": response["pre_event_passed"],
            "above_noise_floor": response["above_noise_floor"],
            "noise_floor_over_U": noise,
            "peak_to_noise_ratio": response["peak_roi_over_U"] / noise if noise > 0 else None,
            "peak_to_noise_ratio_defined": noise > 0,
            "pre_event_peak_over_U": response["pre_event_peak_over_U"],
            "pre_event_bound_over_U": response["pre_event_bound_over_U"],
        }
    reference = variants["default"]
    against_default = {}
    for name in CASE_NAMES[1:]:
        pair = branch_pulse.compare_branch_fields(reference, reference, variants[name], analysis_plan)
        against_default[name] = _metrics(pair["response"])
    return {
        "schema_version": 1,
        "status": "PASS" if all(case["common_window_field_status"] == "PASS"
                                 for case in cases.values()) else "FAIL",
        "scope": "Common-window field comparison only; native provenance and per-case gates are checked separately. Pairwise differences carry no sign-reversal or impulse-linearity assumption.",
        "common_roi_m": list(COMMON_ROI_M),
        "common_response_window_s": list(COMMON_WINDOW_S),
        "response_window_left_open": True,
        "full_time_window_s": [180.0, 200.0],
        "times": report["times"],
        "scales": report["scales"],
        "fluid_quadrature_area_m2": report["fluid_quadrature_area_m2"],
        "roi_quadrature_area_m2": report["roi_quadrature_area_m2"],
        "restart": report["restart"],
        "cases": cases,
        "against_default": against_default,
    }
