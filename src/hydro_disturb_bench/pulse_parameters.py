"""Engineering admission for a configurable pulse on the frozen M3 baseline.

The Gaussian width is sigma, not a hard support radius. Only its three-sigma
disk is used for admission; source integrals include the complete fluid domain.
These checks establish a parameter and source contract, not CFD accuracy over
the admitted range. Geometry, material, checkpoint and solver settings remain
outside the user configuration.
"""
from __future__ import annotations

import copy
import json
import math
import re
from decimal import Decimal, localcontext
from pathlib import Path

from .comsol_java.errors import InvalidParameterError

DOMAIN_BOUNDS_M = (0.0, 0.4, 0.0, 0.24)
CYLINDER_CENTER_M = (0.1, 0.12)
CYLINDER_RADIUS_M = 0.005
DENSITY_KG_M3 = 998.2
BRANCH_TIME_S = 180.0
STOP_TIME_S = 200.0
OUTPUT_STEP_S = 0.2

_TOP_KEYS = {"schema_version", "event_id", "mechanism", "pulse"}
_PULSE_KEYS = {"center_m", "direction_deg", "acceleration_m_s2", "sigma_m", "on_s", "off_s"}
_EVENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", flags=re.ASCII)


def _require(condition, message):
    if not condition:
        raise InvalidParameterError(message)


def _number(value, label):
    _require(type(value) in (int, float), f"{label} must be a real JSON number, not bool or an expression")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, f"{label} must be finite")
    return Decimal(str(value))


def validate_pulse_config(config):
    """Return a detached, unchanged configuration after strict admission.

    The 3-sigma disk may touch the rectangle but must not touch the cylinder.
    Event endpoints must lie on the saved 0.2 s schedule and the duration on
    a 0.4 s grid, so the sin-squared peak also has a saved output time. Decimal
    checks deliberately reject near-grid values rather than rounding them.
    """
    _require(isinstance(config, dict) and set(config) == _TOP_KEYS,
             "Pulse config must contain exactly schema_version, event_id, mechanism and pulse")
    _require(type(config["schema_version"]) is int and config["schema_version"] == 1,
             "schema_version must be integer 1")
    _require(isinstance(config["event_id"], str) and _EVENT_ID.fullmatch(config["event_id"]),
             "event_id must be an ASCII slug of 1 to 64 letters, digits, underscores or hyphens, starting with a letter or digit")
    _require(config["mechanism"] == "gaussian_body_acceleration", "Unsupported pulse mechanism")
    pulse = config["pulse"]
    _require(isinstance(pulse, dict) and set(pulse) == _PULSE_KEYS,
             "pulse contains missing or unknown parameters")
    center = pulse["center_m"]
    _require(isinstance(center, list) and len(center) == 2, "center_m must be a two-number JSON array")
    x, y = (_number(value, f"center_m[{index}]") for index, value in enumerate(center))
    angle = _number(pulse["direction_deg"], "direction_deg")
    amplitude = _number(pulse["acceleration_m_s2"], "acceleration_m_s2")
    sigma = _number(pulse["sigma_m"], "sigma_m")
    on, off = (_number(pulse[key], key) for key in ("on_s", "off_s"))
    _require(Decimal(-180) <= angle <= Decimal(180), "direction_deg must be in [-180, 180]")
    _require(0 <= amplitude <= Decimal("0.01"), "acceleration_m_s2 must be in [0, 0.01]")
    _require(Decimal("0.005") <= sigma <= Decimal("0.02"), "sigma_m must be in [0.005, 0.02]")
    _require(Decimal("180") < on < off < Decimal("200"), "Pulse timing must satisfy 180 < on_s < off_s < 200")
    with localcontext() as context:
        context.prec = 60
        step = Decimal("0.2")
        _require(on % step == 0 and off % step == 0, "Pulse endpoints must align exactly to saved 0.2 s times")
        _require((off - on) % Decimal("0.4") == 0, "Pulse duration must be an integer multiple of 0.4 s")
        radius = 3 * sigma
        _require(radius <= x <= Decimal("0.4") - radius
                 and radius <= y <= Decimal("0.24") - radius,
                 "The complete three-sigma disk must lie within the fixed rectangle")
        distance_squared = (x - Decimal("0.1")) ** 2 + (y - Decimal("0.12")) ** 2
        _require(distance_squared > (radius + Decimal("0.005")) ** 2,
                 "The three-sigma disk must not intersect or touch the cylinder")
    return copy.deepcopy(config)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def load_pulse_config(path):
    """Read strict JSON without duplicate-key replacement or expression parsing."""
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidParameterError(f"Cannot read pulse configuration: {exc}") from exc
    return validate_pulse_config(config)


def pulse_direction(config):
    """Return the unit direction for degrees counterclockwise from positive x."""
    pulse = validate_pulse_config(config)["pulse"]
    angle = math.radians(pulse["direction_deg"])
    return tuple(0.0 if abs(value) < 1e-15 else value
                 for value in (math.cos(angle), math.sin(angle)))


def source_integral_reference(config):
    """Integrate the untruncated Gaussian over rectangle minus cylinder.

    Rectangle integration uses erf exactly; the excluded cylinder is an
    independent one-dimensional scipy.quad integral with analytic y slices.
    The compact sin-squared time integral is (off_s-on_s)/2. Force and impulse
    are per unit out-of-plane depth, not the fluid's net momentum change.
    """
    from scipy.integrate import quad

    pulse = validate_pulse_config(config)["pulse"]
    x, y = pulse["center_m"]
    sigma = pulse["sigma_m"]
    root_two_sigma = math.sqrt(2) * sigma
    factor = sigma * math.sqrt(math.pi / 2)

    def interval(low, high, center):
        return factor * (math.erf((high - center) / root_two_sigma)
                         - math.erf((low - center) / root_two_sigma))

    x_min, x_max, y_min, y_max = DOMAIN_BOUNDS_M
    rectangle = interval(x_min, x_max, x) * interval(y_min, y_max, y)
    cylinder_x, cylinder_y = CYLINDER_CENTER_M

    def cylinder_slice(offset):
        height = math.sqrt(max(0.0, CYLINDER_RADIUS_M**2 - offset**2))
        return math.exp(-((cylinder_x + offset - x) / sigma) ** 2 / 2) * interval(
            cylinder_y - height, cylinder_y + height, y)

    cylinder, error = quad(cylinder_slice, -CYLINDER_RADIUS_M, CYLINDER_RADIUS_M,
                           epsabs=1e-16, epsrel=1e-11, limit=200)
    area = rectangle - cylinder
    _require(all(math.isfinite(value) for value in (rectangle, cylinder, error, area))
             and 0 <= cylinder < rectangle and area > 0, "Gaussian area integration failed")
    direction = pulse_direction(config)
    peak_density = DENSITY_KG_M3 * pulse["acceleration_m_s2"]
    peak = peak_density * area
    duration_integral = float((Decimal(str(pulse["off_s"])) - Decimal(str(pulse["on_s"]))) / 2)
    impulse = peak * duration_integral
    return {
        "method": "Analytic rectangular Gaussian integral minus scipy.quad cylinder slices; compact sin-squared time integral",
        "gaussian_rectangle_area_m2": rectangle,
        "gaussian_cylinder_area_m2": cylinder,
        "cylinder_quadrature_absolute_error_m2": error,
        "gaussian_area_m2": area,
        "direction": list(direction),
        "peak_force_density_N_m3": peak_density,
        "peak_force_per_depth_N_m": peak,
        "peak_force_vector_N_per_m": [peak * value for value in direction],
        "temporal_integral_s": duration_integral,
        "impulse_per_depth_N_s_m": impulse,
        "impulse_vector_Ns_per_m": [impulse * value for value in direction],
        "scope": "Applied source per unit out-of-plane depth, not net fluid momentum change",
    }


def properties_payload(config):
    """Return deterministic ASCII Java properties containing numeric literals only."""
    config = validate_pulse_config(config)
    pulse = config["pulse"]
    values = {
        "amplitude_m_s2": pulse["acceleration_m_s2"],
        "xc_m": pulse["center_m"][0], "yc_m": pulse["center_m"][1],
        "sigma_m": pulse["sigma_m"], "direction_deg": pulse["direction_deg"],
        "on_s": pulse["on_s"], "off_s": pulse["off_s"],
    }
    lines = [f"event_id={config['event_id']}"]
    lines.extend(f"{key}={value!r}" for key, value in values.items())
    return "\n".join(lines) + "\n"
