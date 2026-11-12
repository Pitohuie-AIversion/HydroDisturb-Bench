"""Offline parameter admission and independent source-integral checks."""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest
from numpy.polynomial.legendre import leggauss

from hydro_disturb_bench.comsol_java.errors import InvalidParameterError
from hydro_disturb_bench.pulse_parameters import (
    DENSITY_KG_M3,
    load_pulse_config,
    properties_payload,
    pulse_direction,
    source_integral_reference,
    validate_pulse_config,
)

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs/m3_local_pulse.json"


@pytest.fixture
def config():
    return json.loads(DEFAULT_CONFIG.read_text(encoding="utf-8"))


def test_registered_default_preserves_accepted_pulse_and_detaches(config):
    original = copy.deepcopy(config)
    validated = load_pulse_config(DEFAULT_CONFIG)
    assert validated == config == original
    assert validated["pulse"] == {"center_m": [.15, .12], "direction_deg": 90,
                                  "acceleration_m_s2": .005, "sigma_m": .01,
                                  "on_s": 181, "off_s": 183}
    validated["pulse"]["center_m"][0] = .25
    assert config == original


@pytest.mark.parametrize("key", ["solver", "rho", "geometry", "checkpoint", "unknown"])
def test_unknown_top_level_controls_rejected(config, key):
    config[key] = 1
    with pytest.raises(InvalidParameterError, match="exactly"):
        validate_pulse_config(config)


@pytest.mark.parametrize("key", ["radius_m", "dirx", "expression", "rtol"])
def test_unknown_pulse_controls_rejected(config, key):
    config["pulse"][key] = 1
    with pytest.raises(InvalidParameterError, match="unknown"):
        validate_pulse_config(config)


@pytest.mark.parametrize("key", ["schema_version", "event_id", "mechanism", "pulse"])
def test_missing_top_level_fields_rejected(config, key):
    del config[key]
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("value", [True, 1.0, 2, "1"])
def test_schema_version_is_exact_integer(config, value):
    config["schema_version"] = value
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("value", ["", "../event", "hello world", "a\nb=1", "a=b", "脉冲", "_event", "a" * 65, None])
def test_event_id_cannot_be_path_or_properties_injection(config, value):
    config["event_id"] = value
    with pytest.raises(InvalidParameterError, match="event_id"):
        validate_pulse_config(config)


@pytest.mark.parametrize("value", ["a", "M3-pulse_01", "a" * 64])
def test_safe_event_id_boundaries(config, value):
    config["event_id"] = value
    assert validate_pulse_config(config)["event_id"] == value


@pytest.mark.parametrize("key", ["direction_deg", "acceleration_m_s2", "sigma_m", "on_s", "off_s"])
@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), float("-inf"), "0.005[m/s^2]"])
def test_every_pulse_number_rejects_nonfinite_bool_and_expression(config, key, value):
    config["pulse"][key] = value
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("center", [[True, .12], [.15, float("nan")], [.15], [.15, .12, 0], (.15, .12), "0.15,0.12"])
def test_center_is_two_finite_json_numbers(config, center):
    config["pulse"]["center_m"] = center
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("key,value", [("acceleration_m_s2", -.0001), ("acceleration_m_s2", .010001),
                                       ("sigma_m", .004999), ("sigma_m", .020001),
                                       ("direction_deg", -180.001), ("direction_deg", 180.001)])
def test_engineering_envelope_is_not_extended(config, key, value):
    config["pulse"][key] = value
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("amplitude,sigma", [(0, .005), (.01, .02)])
def test_closed_amplitude_sigma_endpoints(config, amplitude, sigma):
    config["pulse"].update(acceleration_m_s2=amplitude, sigma_m=sigma, center_m=[.25, .12])
    assert validate_pulse_config(config) == config


@pytest.mark.parametrize("center", [[.0299999, .18], [.370001, .18], [.25, .02999], [.25, .210001],
                                     [.1, .12], [.13499, .12], [.135, .12]])
def test_three_sigma_disk_cannot_cross_wall_or_touch_cylinder(config, center):
    config["pulse"]["center_m"] = center
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


def test_three_sigma_disk_may_touch_rectangle_and_sigma_is_not_support_radius(config):
    config["pulse"].update(center_m=[.06, .18], sigma_m=.02)
    assert validate_pulse_config(config) == config
    config["pulse"].update(center_m=[.25, .12], sigma_m=.01)
    area = source_integral_reference(config)["gaussian_area_m2"]
    mass_inside_three_sigma_disk = 2 * math.pi * .01**2 * (1 - math.exp(-4.5))
    assert area > mass_inside_three_sigma_disk


@pytest.mark.parametrize("on,off", [(180, 181.2), (199.6, 200), (183, 181), (181, 181),
                                    (181.1, 183.1), (181, 183.2), (181.00000000000003, 183)])
def test_event_time_and_saved_peak_admission(config, on, off):
    config["pulse"].update(on_s=on, off_s=off)
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)


@pytest.mark.parametrize("on,off", [(180.2, 180.6), (199.4, 199.8), (181.2, 183.6)])
def test_aligned_times_preserve_values_and_contain_peak(config, on, off):
    config["pulse"].update(on_s=on, off_s=off)
    assert validate_pulse_config(config) == config
    assert source_integral_reference(config)["temporal_integral_s"] == pytest.approx((off - on) / 2)


@pytest.mark.parametrize("angle,expected", [(0, (1., 0.)), (90, (0., 1.)),
                                            (-90, (0., -1.)), (180, (-1., 0.)), (-180, (-1., 0.))])
def test_cardinal_directions_have_exact_zero_components(config, angle, expected):
    config["pulse"]["direction_deg"] = angle
    assert pulse_direction(config) == expected


def test_arbitrary_direction_is_unit_length_and_preserves_sign(config):
    config["pulse"]["direction_deg"] = -37.5
    x, y = pulse_direction(config)
    assert x > 0 and y < 0
    assert math.hypot(x, y) == pytest.approx(1, abs=1e-15)
    reference = source_integral_reference(config)
    assert reference["peak_force_vector_N_per_m"] == pytest.approx(
        [reference["peak_force_per_depth_N_m"] * x, reference["peak_force_per_depth_N_m"] * y])
    assert reference["impulse_vector_Ns_per_m"] == pytest.approx(reference["peak_force_vector_N_per_m"])


def test_default_integral_matches_accepted_independent_reference(config):
    reference = source_integral_reference(config)
    assert reference["gaussian_area_m2"] == pytest.approx(.0006283179832803473, abs=2e-15)
    assert reference["peak_force_density_N_m3"] == pytest.approx(4.991)
    assert reference["peak_force_vector_N_per_m"] == pytest.approx([0, .0031359350545522137], abs=1e-14)
    assert reference["impulse_vector_Ns_per_m"] == reference["peak_force_vector_N_per_m"]
    assert 0 <= reference["cylinder_quadrature_absolute_error_m2"] < 1e-14


def test_area_against_independent_rectangle_and_polar_gauss_quadrature(config):
    config["pulse"]["center_m"] = [.136, .12]
    reference = source_integral_reference(config)
    nodes, weights = leggauss(160)
    x, y = .2 * (nodes + 1), .12 * (nodes + 1)
    gaussian = np.exp(-((x[:, None] - .136)**2 + (y[None, :] - .12)**2) / (2 * .01**2))
    rectangle = float(.2 * .12 * np.einsum("i,j,ij->", weights, weights, gaussian))
    radius, theta = .0025 * (nodes + 1), math.pi * (nodes + 1)
    cx = .1 + radius[:, None] * np.cos(theta[None, :])
    cy = .12 + radius[:, None] * np.sin(theta[None, :])
    disk = np.exp(-((cx - .136)**2 + (cy - .12)**2) / (2 * .01**2)) * radius[:, None]
    cylinder = float(.0025 * math.pi * np.einsum("i,j,ij->", weights, weights, disk))
    assert reference["gaussian_cylinder_area_m2"] == pytest.approx(cylinder, rel=1e-9, abs=1e-16)
    assert reference["gaussian_area_m2"] == pytest.approx(rectangle - cylinder, abs=1e-15)


def test_amplitude_and_duration_affect_only_corresponding_source_factors(config):
    base = source_integral_reference(config)
    config["pulse"].update(acceleration_m_s2=.01, off_s=183.4)
    changed = source_integral_reference(config)
    assert changed["gaussian_area_m2"] == base["gaussian_area_m2"]
    assert changed["peak_force_per_depth_N_m"] == 2 * base["peak_force_per_depth_N_m"]
    assert changed["impulse_per_depth_N_s_m"] == pytest.approx(2.4 * base["impulse_per_depth_N_s_m"])
    assert changed["peak_force_density_N_m3"] == .01 * DENSITY_KG_M3
    config["pulse"]["acceleration_m_s2"] = 0
    zero = source_integral_reference(config)
    assert zero["peak_force_vector_N_per_m"] == [0, 0]
    assert zero["impulse_vector_Ns_per_m"] == [0, 0]


def test_properties_are_ascii_numeric_literals_and_original_values(config):
    config["pulse"]["direction_deg"] = -37.5
    payload = properties_payload(config)
    assert payload.isascii() and "\r" not in payload and payload.endswith("\n")
    values = dict(line.split("=") for line in payload.splitlines())
    assert list(values) == ["event_id", "amplitude_m_s2", "xc_m", "yc_m", "sigma_m", "direction_deg", "on_s", "off_s"]
    assert values.pop("event_id") == config["event_id"]
    assert {key: float(value) for key, value in values.items()} == {
        "amplitude_m_s2": .005, "xc_m": .15, "yc_m": .12, "sigma_m": .01,
        "direction_deg": -37.5, "on_s": 181, "off_s": 183}


@pytest.mark.parametrize("text", ['{"schema_version":1,"schema_version":1}', '{"pulse":{"on_s":181,"on_s":183}}',
                                  '{bad json}', '[]'])
def test_load_rejects_duplicate_keys_malformed_json_and_nonobjects(tmp_path, text):
    path = tmp_path / "pulse.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(InvalidParameterError):
        load_pulse_config(path)


def test_invalid_mechanism_and_missing_pulse_field(config):
    config["mechanism"] = "moving_boundary"
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)
    config["mechanism"] = "gaussian_body_acceleration"
    del config["pulse"]["off_s"]
    with pytest.raises(InvalidParameterError):
        validate_pulse_config(config)
