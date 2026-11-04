"""Offline admission and fault tests for the production Case D entry point.

External COMSOL calls are replaced in entry tests; settings, log, metric,
isolation, and preservation gates execute unchanged. Real COMSOL evidence is
produced separately by the Case D run, not by these synthetic fixtures.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
for location in (ROOT_DIR, ROOT_DIR / "src"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from scripts import run_re100_s1b1_case_d as case_d


def _accepted_rows():
    """Cover 200 s with adaptive initial steps followed by the D step cap."""
    rows = []
    elapsed = 0.0
    for index, step in enumerate([0.01, 0.015] + [0.025] * 7999, start=1):
        elapsed += step
        rows.append((index, elapsed, step))
    return rows


def _step_log(rows):
    return "Step Time Stepsize Res Jac Sol Order Tfail NLfail\n" + "\n".join(
        f"{index:6d} {time:.9e} {step:.9e} 2 1 2 2 0 0"
        for index, time, step in rows
    )


def test_case_d_log_accepts_adaptive_steps_below_cap(tmp_path):
    log_path = tmp_path / "solver.log"
    log_path.write_text(_step_log(_accepted_rows()), encoding="utf-8")

    result = case_d.check_step_log(log_path)

    assert result["cap_verified"] is True
    assert result["last_logged_time"] == pytest.approx(200.0)
    assert result["logged_rows"] == result["last_step_number"] == 8001
    assert result["all_dt_range"] == pytest.approx([0.01, 0.025])


@pytest.mark.parametrize("fault", ["over_cap", "gap", "duplicate", "time_reversal", "truncated", "short_tail"])
def test_case_d_log_rejects_invalid_internal_step_evidence(tmp_path, fault):
    rows = _accepted_rows()
    index, time, step = rows[100]
    if fault == "over_cap":
        rows[100] = index, time, 0.02501
    elif fault == "gap":
        del rows[100]
    elif fault == "duplicate":
        rows.insert(100, rows[100])
    elif fault == "time_reversal":
        rows[100] = index, rows[99][1] - 0.001, step
    elif fault == "short_tail":
        rows = rows[:-1]
    else:
        rows = rows[:-10]
    log_path = tmp_path / "solver.log"
    log_path.write_text(_step_log(rows), encoding="utf-8")

    with pytest.raises(RuntimeError):
        case_d.check_step_log(log_path)


@pytest.fixture
def readbacks():
    before = {
        "solver_details": {
            "rtol": "1e-4",
            "maxstepconstraintbdf": "const",
            "maxstepbdf": "0.05",
            "atolmethod": "comp1_p, scaled, comp1_u, global",
            "atolfactor": "comp1_p, 1, comp1_u, 0.1",
            "tlist": "range(0, 0.2, 200)",
            "timemethod": "bdf",
            "initialstepbdf": "0.001",
            "initialstepbdfactive": "off",
        },
        "mesh_details": {"num_elem": 7493, "num_vertex": 3944},
        "physics_details": {"initial_conditions": {"u": "0", "v": "0"}},
    }
    after = deepcopy(before)
    after["solver_details"]["maxstepbdf"] = "0.025"
    tree = {
        "parameters": {"U_inf": "0.010058104588[m/s]", "rho": "1000[kg/m^3]"},
        "components": [{"tag": "comp1", "physics": [{"tag": "spf", "selection": [1]}]}],
        "studies": [{"tag": "transient_case", "features": [{"type": "Transient"}]}],
    }
    return before, after, tree, deepcopy(tree)


def test_case_d_actual_settings_accept_only_target_cap_change(readbacks):
    result = case_d.check_settings(*readbacks)
    assert result["status"] == "PASS"
    assert result["case_c_solver"]["maxstepbdf"] == "0.05"
    assert result["case_d_solver"]["maxstepbdf"] == "0.025"


@pytest.mark.parametrize(
    "path,value",
    [
        (("solver_details", "maxstepbdf"), "0.05"),
        (("solver_details", "maxstepconstraintbdf"), "auto"),
        (("solver_details", "rtol"), "1e-3"),
        (("solver_details", "atolfactor"), "comp1_p, 1, comp1_u, 1"),
        (("solver_details", "tlist"), "range(0, 0.1, 200)"),
        (("mesh_details", "num_elem"), 14986),
        (("physics_details", "initial_conditions", "u"), "U_inf"),
    ],
    ids=["wrong_cap", "auto_mode", "rtol", "atol", "output_schedule", "mesh", "initial_condition"],
)
def test_case_d_rejects_actual_noncompliant_settings(readbacks, path, value):
    destination = readbacks[1]
    for key in path[:-1]:
        destination = destination[key]
    destination[path[-1]] = value

    with pytest.raises(RuntimeError):
        case_d.check_settings(*readbacks)


def test_case_d_rejects_changed_model_parameter(readbacks):
    readbacks[3]["parameters"]["rho"] = "998[kg/m^3]"
    with pytest.raises(RuntimeError, match="Non-target setting mismatch"):
        case_d.check_settings(*readbacks)


def _stable_stats(name):
    return {
        "name": name,
        "is_stable": True,
        "status": "STABLE",
        "num_cycles": 5,
        "St_zc_mean": 0.16,
        "St_peaks_mean": 0.16,
        "Cd_mean": 1.346,
        "Cl_amp_mean": 0.3,
        "Cl_demeaned_rms": 0.21,
        "step_info": {"step_count": 8001},
        "solve_wall_clock_sec": 10.0,
    }


@pytest.mark.parametrize("metric", ["St_zc_mean", "Cd_mean", "Cl_amp_mean", "Cl_demeaned_rms"])
def test_case_d_does_not_relax_any_of_the_four_one_percent_thresholds(metric):
    stats_c, stats_d = _stable_stats("C"), _stable_stats("D")
    stats_d[metric] *= 1.02

    result = case_d.classify_comparison(stats_c, stats_d)

    assert result["acceptance_status"] == "SENSITIVE"
    assert result["phase_can_close"] is False
    assert set(result["thresholds_pct"].values()) == {1.0}


@pytest.mark.parametrize("case_index", [0, 1], ids=["C", "D"])
@pytest.mark.parametrize("fault", ["unstable", "insufficient_cycles"])
def test_case_d_requires_both_cases_stable_with_five_complete_cycles(case_index, fault):
    stats = [_stable_stats("C"), _stable_stats("D")]
    if fault == "unstable":
        stats[case_index]["is_stable"] = False
        stats[case_index]["stability_message"] = "Cycle amplitude is still drifting"
        expected_status = "UNSTABLE"
    else:
        stats[case_index]["num_cycles"] = 4
        expected_status = "INSUFFICIENT_CYCLES"

    result = case_d.classify_comparison(*stats)

    assert result["acceptance_status"] == expected_status
    assert result["can_compare"] is False
    assert result["phase_can_close"] is False


@pytest.fixture
def offline_run(tmp_path, monkeypatch, readbacks):
    """Keep real production gates and filesystem isolation; replace only COMSOL/analysis I/O."""
    source_dir = tmp_path / "accepted_c"
    source_dir.mkdir()
    source = source_dir / "case_c.mph"
    source.write_bytes(b"accepted Case C solution")
    historical = tmp_path / "case_a.mph"
    historical.write_bytes(b"historical solution; never solve or overwrite")
    before, after, tree_c, tree_d = readbacks
    state = {
        "source": source,
        "historical": historical,
        "before": before,
        "after": after,
        "precheck_status": "PASS",
        "stats_c": _stable_stats("C"),
        "stats_d": _stable_stats("D"),
        "output": tmp_path / "new_d",
    }
    monkeypatch.setattr(case_d, "CASE_C_DIR", source_dir)
    monkeypatch.setattr(case_d, "CASE_C_MODEL", source)
    monkeypatch.setattr(case_d, "PROTECTED_MODELS", (source, historical))

    def inspect(model_path):
        return deepcopy(tree_c if model_path == source else tree_d)

    def precheck(**kwargs):
        is_source = kwargs["model_path"] == source
        return {
            "overall_prechecks_status": state["precheck_status"] if is_source else "PASS",
            "precheck_b_solver_model": deepcopy(before if is_source else after),
        }

    def solve(**kwargs):
        destination = kwargs["output_path"]
        destination.write_bytes(b"new Case D solution")
        return {
            "source_sha256": case_d.compute_file_hash(source),
            "output_sha256": case_d.compute_file_hash(destination),
            "log": _step_log(_accepted_rows()),
        }

    def analyze(name, directory):
        return deepcopy(state["stats_c"] if directory == source_dir else state["stats_d"])

    for name, callback in (
        ("inspect_model", inspect),
        ("run_prechecks", precheck),
        ("run_study", solve),
        ("export_s1_diagnostics", lambda **kwargs: {"status": "SUCCESS"}),
        ("analyze_case", analyze),
    ):
        state[name] = Mock(side_effect=callback)
        monkeypatch.setattr(case_d, name, state[name])
    return state


def test_case_d_entry_solves_only_c_into_new_directory_with_only_cap_override(offline_run):
    state = offline_run
    report = case_d.run_case_d(state["output"])

    state["run_study"].assert_called_once_with(
        model_path=state["source"],
        output_path=state["output"] / "re100_s1b1_case_d_solved.mph",
        study_tag="transient_case",
        maxstep_override="0.025",
        allow_overwrite=False,
    )
    assert state["run_prechecks"].call_count == 2
    assert state["analyze_case"].call_count == 2
    assert state["source"].read_bytes() == b"accepted Case C solution"
    assert state["historical"].read_bytes() == b"historical solution; never solve or overwrite"
    assert report["status"] == "PASS"
    assert report["time_sensitivity_phase_can_close"] is True
    assert report["protected_models_unchanged"] is True
    saved = json.loads((state["output"] / "case_d_report.json").read_text(encoding="utf-8"))
    assert saved == report
    export_args = state["export_s1_diagnostics"].call_args.kwargs
    assert export_args["expected_steps"] == 1001
    assert export_args["expected_dt"] == 0.20
    assert export_args["expected_t_final"] == 200.0


def test_case_d_entry_refuses_existing_output_before_external_calls(offline_run):
    state = offline_run
    state["output"].mkdir()
    marker = state["output"] / "historical_evidence.txt"
    marker.write_text("preserve this result", encoding="utf-8")

    with pytest.raises(FileExistsError):
        case_d.run_case_d(state["output"])

    assert marker.read_text(encoding="utf-8") == "preserve this result"
    state["inspect_model"].assert_not_called()
    state["run_prechecks"].assert_not_called()
    state["run_study"].assert_not_called()


@pytest.mark.parametrize("fault", ["precheck", "source_cap", "source_mode", "source_rtol"])
def test_case_d_entry_rejects_bad_c_evidence_before_solving(offline_run, fault):
    state = offline_run
    if fault == "precheck":
        state["precheck_status"] = "FAIL"
    else:
        key, value = {
            "source_cap": ("maxstepbdf", "0.1"),
            "source_mode": ("maxstepconstraintbdf", "auto"),
            "source_rtol": ("rtol", "1e-3"),
        }[fault]
        state["before"]["solver_details"][key] = value

    with pytest.raises(RuntimeError):
        case_d.run_case_d(state["output"])

    state["run_study"].assert_not_called()
    saved = json.loads((state["output"] / "case_d_report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "FAIL"
    assert saved["protected_models_unchanged"] is True


def test_case_d_entry_reports_sensitive_result_without_promoting_to_pass(offline_run):
    state = offline_run
    state["stats_d"]["Cl_amp_mean"] *= 1.02

    report = case_d.run_case_d(state["output"])

    assert report["status"] == "SENSITIVE"
    assert report["time_sensitivity_phase_can_close"] is False
    saved = json.loads((state["output"] / "case_d_report.json").read_text(encoding="utf-8"))
    assert saved["physical_comparison"]["conclusion"] == "SENSITIVE"
    assert saved["status"] == "SENSITIVE"


def test_case_d_entry_persists_failed_stability_gate(offline_run):
    state = offline_run
    state["stats_d"]["is_stable"] = False

    with pytest.raises(RuntimeError, match="stability"):
        case_d.run_case_d(state["output"])

    saved = json.loads((state["output"] / "case_d_report.json").read_text(encoding="utf-8"))
    assert saved["status"] == "FAIL"
    assert saved["physical_comparison"]["acceptance_status"] == "UNSTABLE"
    assert saved["physical_comparison"]["phase_can_close"] is False
