"""Offline unit and contract tests for M1 3D propeller COMSOL bridge and baseline verifier."""

from __future__ import annotations

import base64
import json
import math
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hydro_disturb_bench.cli import main as cli_main
from hydro_disturb_bench.comsol_java import (
    ComsolConnectionError,
    InvalidParameterError,
    ModelNotFoundError,
    ModelValidationError,
    SolverFailedError,
    bridge,
    compute_file_hash,
    comsol_solver_lock,
    eval_propeller,
    inspect_propeller,
    run_propeller_baseline,
)
from hydro_disturb_bench.propeller.baseline import (
    REFERENCE_KPIS,
    reproduce_propeller_baselines,
    verify_baseline_kpis,
)


def _wrap_json(payload: dict) -> str:
    return "<<<JSON_START>>>\n" + json.dumps(payload) + "\n<<<JSON_END>>>"


def _make_fake_comsol_root(tmp_path: Path) -> Path:
    root = tmp_path / "COMSOL"
    bin_dir = root / "bin" / "win64"
    bin_dir.mkdir(parents=True)
    for exe in ("comsolcompile.exe", "comsolbatch.exe"):
        (bin_dir / exe).write_bytes(b"fake")
    jre_dir = root / "java" / "win64" / "jre" / "bin"
    jre_dir.mkdir(parents=True)
    for exe in ("javac.exe", "java.exe"):
        (jre_dir / exe).write_bytes(b"fake")
    return root


def _make_inspect_payload(model_path: str, rpm: float = 1400.0) -> dict:
    is_cw = rpm > 0
    return {
        "status": "SUCCESS",
        "operation": "inspect_propeller",
        "model_path": model_path,
        "model_tag": "Model",
        "comsol_version": "6.4",
        "components": ["c"],
        "parameters": {
            "speed_rpm": {"expr": f"{rpm}[rpm]", "value": rpm / 60.0, "unit": "1/s", "descr": ""},
            "rho_water": {"expr": "1025[kg/m^3]", "value": 1025.0, "unit": "kg/m^3", "descr": ""},
            "mu_water": {"expr": "0.00108[Pa*s]", "value": 0.00108, "unit": "Pa*s", "descr": ""},
        },
        "geometry": {
            "tag": "g",
            "sdim": 3,
            "length_unit": "m",
            "bounding_box": [-1.5, 1.5, -1.0, 1.0, -1.0, 1.0],
            "n_domains": 2,
            "n_boundaries": 29,
        },
        "selections": {
            "impeller": {"dimension": 2, "entities": list(range(13, 26))},
            "nozzle": {"dimension": 2, "entities": [4, 5, 6, 7, 8, 9, 27, 28]},
            "far": {"dimension": 2, "entities": [1, 2, 3, 29]},
            "moving": {"dimension": 3, "entities": [2]},
        },
        "rot1": {
            "type": "RotatingDomain",
            "selection": [2],
            "revolutionsPerTime": "speed_rpm",
            "rotationAxis": ["1", "0", "0"],
            "rotationAxisBasePoint": ["0", "0", "0"],
        },
        "coupling_operators": {
            "intR": {"type": "Integration", "opname": "intR", "entities": list(range(13, 26))},
            "intD": {"type": "Integration", "opname": "intD", "entities": [4, 5, 6, 7, 8, 9, 27, 28]},
            "intFar": {"type": "Integration", "opname": "intFar", "entities": [1, 2, 3, 29]},
            "minOp1": {"type": "Minimum", "opname": "minOp1", "entities": [2]},
            "maxOp1": {"type": "Maximum", "opname": "maxOp1", "entities": [2]},
        },
        "physics_bcs": {
            "upstream": {"type": "InletBoundary", "selection_entities": [29] if is_cw else [1]},
            "open": {"type": "OpenBoundary", "selection_entities": [1, 2, 3] if is_cw else [2, 3, 29]},
            "blade": {"type": "WallBC", "selection_entities": list(range(13, 26))},
            "duct": {"type": "WallBC", "selection_entities": [4, 5, 6, 7, 8, 9, 27, 28]},
        },
        "bounding_boxes": {
            "impeller": {"x_min_m": 0.065, "x_max_m": 0.130, "r_min_m": 0.0, "r_max_m": 0.1600004, "d_max_m": 0.3200008},
            "nozzle": {"x_min_m": 0.009966, "x_max_m": 0.202606, "r_min_m": 0.161, "r_max_m": 0.194428, "d_max_m": 0.388856},
            "far_neg_x_cap": [1],
            "far_pos_x_cap": [29],
            "far_lateral": [2, 3],
        },
        "geometry_audit": {
            "d_prop_m": 0.3200008,
            "r_prop_max_m": 0.1600004,
            "duct_outer_max_d_m": 0.388856,
            "duct_r_max_m": 0.194428,
            "d_hub_m": 0.084,
            "hub_x_min_m": 0.065,
            "hub_x_max_m": 0.130,
        },
        "numerical_evaluators": {"summary": {"type": "EvalGlobal", "units": ["N", "N", "N", "N*m", "kW"]}},
    }


def _make_eval_payload(rpm: float = 1400.0, operation: str = "run_propeller_baseline") -> dict:
    is_cw = rpm > 0
    ref = REFERENCE_KPIS["cw"] if is_cw else REFERENCE_KPIS["ccw"]
    return {
        "status": "SUCCESS",
        "operation": operation,
        "model_path": "source.mph",
        "output_model": None,
        "study_tag": "std",
        "solver_tag": "sol1",
        "speed_rpm": rpm,
        "direction": "cw" if is_cw else "ccw",
        "elapsed_ms": 1234,
        "wall_time_s": 1.234,
        "geometry_rebuilt": False,
        "bc_selection": {
            "upstream": [29] if is_cw else [1],
            "open": [1, 2, 3] if is_cw else [2, 3, 29],
            "builder_integrate2_open": [1, 2, 3] if is_cw else [2, 3, 29],
            "builder_integrate3_upstream": [29] if is_cw else [1],
        },
        "kpis": {
            "F_rotor_x_N": ref["F_rotor_x_N"],
            "F_duct_x_N": ref["F_duct_x_N"],
            "F_total_x_N": ref["F_total_x_N"],
            "thrust_N": ref["thrust_N"],
            "drive_torque_x_Nm": ref["drive_torque_x_Nm"],
            "shaft_power_kW": ref["shaft_power_kW"],
            "thrust_per_power_N_per_kW": ref["thrust_per_power_N_per_kW"],
            "y_plus_imp": 36.91 if is_cw else 38.08,
            "y_plus_duct": 13.88 if is_cw else 13.51,
        },
        "flow_audit": {
            "net_mass_flux_kg_s": 1.1943587307 if is_cw else 0.0027250213,
            "gross_mass_flux_kg_s": 4257.110016 if is_cw else 3894.941759,
            "net_vol_flux_m3_s": 0.00116522803 if is_cw else 2.658557392e-6,
            "gross_vol_flux_m3_s": 4.153278065 if is_cw else 3.799943180,
            "momentum_x_N": 1231.059492 if is_cw else -2510.515190,
            "pressure_reaction_x_N": 82.177232 if is_cw else 48.018816,
            "vel_plane_neg_x_m_s": -4.68743 if is_cw else 5.65557,
            "mdot_plane_neg_x_kg_s": -398.4092 if is_cw else 480.6965,
            "mass_imbalance_gross_pct": ref["mass_imbalance_gross_pct"],
            "mass_imbalance_plane_pct": ref["mass_imbalance_plane_pct"],
            "momentum_closure_pct": ref["momentum_closure_pct"],
        },
        "cavitation": {
            "p_min_imp_Pa": ref["p_min_imp_Pa"],
            "p_max_imp_Pa": 222956.2 if is_cw else 50832.6,
            "impeller_area_m2": 0.1348471198,
            "cav_area_99_m2": 0.0006996455268 if is_cw else 0.0,
            "cav_area_150_m2": 9.067085861e-5 if is_cw else 0.0,
            "cav_area_99_pct": ref["cav_area_99_pct"],
            "cav_area_150_pct": 0.0672 if is_cw else 0.0,
        },
        "solver_stats": {
            "control": "user",
            "stol": 0.0025,
            "segcflaa": "off",
            "submincfl": 500.0,
            "subdamp": 0.35,
            "maxiter": 100,
            "use_seed": True,
            "initsol": "seed",
            "iterations": 5 if is_cw else 18,
            "solver_err": [0.0011, 0.0013] if is_cw else [0.001, 0.0016],
            "max_solver_err": 0.0013 if is_cw else 0.0016,
            "converged": True,
            "dof_count": 886027,
        },
        "geometry_audit": {
            "d_prop_m": 0.3200008,
            "r_prop_max_m": 0.1600004,
            "duct_outer_max_d_m": 0.388856,
            "duct_r_max_m": 0.194428,
            "d_hub_m": 0.084,
            "hub_x_min_m": 0.065,
            "hub_x_max_m": 0.130,
        },
    }


def _decode_b64_props(arg: str) -> dict[str, str]:
    assert arg.startswith("base64:")
    text = base64.b64decode(arg[7:]).decode("utf-8")
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def test_ensure_bridge_compiled_includes_propeller_bridge(tmp_path, monkeypatch):
    root = _make_fake_comsol_root(tmp_path)
    java_dir = tmp_path / "bridge"
    java_dir.mkdir()
    for name in (
        "ComsolBridge.java",
        "ConfigurablePulseBranch.java",
        "PropellerBridge.java",
        "ComsolStandalone.java",
    ):
        (java_dir / name).write_text(f"synthetic {name}", encoding="utf-8")
    monkeypatch.setattr(bridge, "__file__", str(java_dir / "bridge.py"))
    monkeypatch.setattr(bridge.sys, "platform", "win32")

    compile_calls = []

    def runner(command, **kwargs):
        compile_calls.append(command)
        assert [Path(p).name for p in command[1:]] == [
            "ComsolBridge.java",
            "ConfigurablePulseBranch.java",
            "PropellerBridge.java",
        ]
        for cname in (
            "ComsolBridge.class",
            "ConfigurablePulseBranch.class",
            "ConfigurablePulseBranch$Spec.class",
            "ConfigurablePulseBranch$StrictProperties.class",
            "ConfigurablePulseBranch$Raw.class",
            *bridge.PROPELLER_CLASS_NAMES,
        ):
            (java_dir / cname).write_bytes(b"bytecode")
        return subprocess.CompletedProcess(command, 0, "", "")

    res = bridge.ensure_bridge_compiled(root, process_runner=runner)
    assert res == java_dir / "ComsolBridge.class"
    assert len(compile_calls) == 1

    # Missing PropellerBridge$EvalSpec.class must reject standalone propeller launch
    (java_dir / "PropellerBridge$EvalSpec.class").unlink()
    with pytest.raises(ComsolConnectionError, match="Propeller bridge helper bytecode is incomplete"):
        bridge._run_standalone(root, res, ["inspect-propeller", "model.mph"], process_runner=runner)

    # Restore class and confirm _run_standalone copies all PropellerBridge*.class files
    (java_dir / "PropellerBridge$EvalSpec.class").write_bytes(b"bytecode")
    seen_work_classes: list[str] = []

    def standalone_runner(command, **kwargs):
        exe = Path(command[0]).name
        if exe == "javac.exe":
            work = Path(kwargs["cwd"])
            seen_work_classes.extend(sorted(p.name for p in work.glob("PropellerBridge*.class")))
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 0, _wrap_json(_make_inspect_payload("model.mph")), "")

    proc = bridge._run_standalone(
        root, res, ["inspect-propeller", "model.mph"], process_runner=standalone_runner
    )
    assert proc.returncode == 0
    assert seen_work_classes == sorted(bridge.PROPELLER_CLASS_NAMES)


def test_inspect_propeller_validates_schema_and_immutability(tmp_path, monkeypatch):
    root = _make_fake_comsol_root(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"class")
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda r, **kw: class_file)

    base = tmp_path / "cw.mph"
    base.write_bytes(b"CW_BASELINE_BYTES")
    expected_sha = compute_file_hash(base)

    def ok_runner(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_inspect_payload(command[-1])), ""
        )

    res = inspect_propeller(base, expected_sha256=expected_sha, root=root, process_runner=ok_runner)
    assert res["sha256"] == expected_sha
    assert res["physics_bcs"]["upstream"]["selection_entities"] == [29]
    assert res["geometry_audit"]["d_prop_m"] == pytest.approx(0.3200008)

    # Schema violation when required key is missing
    def bad_schema_runner(command, **kwargs):
        payload = _make_inspect_payload(command[-1])
        payload.pop("bounding_boxes")
        return subprocess.CompletedProcess(command, 0, _wrap_json(payload), "")

    with pytest.raises(ComsolConnectionError, match="Schema violation"):
        inspect_propeller(base, root=root, process_runner=bad_schema_runner)

    # Missing model raises immediately
    with pytest.raises(ModelNotFoundError):
        inspect_propeller(tmp_path / "missing.mph", root=root, process_runner=ok_runner)

    # Baseline tamper raises ModelValidationError
    def tamper_runner(command, **kwargs):
        base.write_bytes(b"TAMPERED!")
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_inspect_payload(command[-1])), ""
        )

    with pytest.raises(ModelValidationError, match="modified during propeller inspection"):
        inspect_propeller(base, root=root, process_runner=tamper_runner)


def test_eval_propeller_cow_refuses_overwrite_symlink_and_tamper(tmp_path, monkeypatch):
    root = _make_fake_comsol_root(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"class")
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda r, **kw: class_file)

    base = tmp_path / "base.mph"
    base.write_bytes(b"ORIGINAL_MODEL")
    out_dir = tmp_path / "out"

    # Same path refused in run_propeller_baseline
    with pytest.raises(ModelValidationError, match="identical"):
        run_propeller_baseline(base, base, save_model=True, root=root)

    # Symlink refused
    sym = tmp_path / "link.mph"
    with monkeypatch.context() as mp:
        mp.setattr(Path, "is_symlink", lambda self: self.name == "link.mph")
        with pytest.raises(ModelValidationError, match="symbolic link"):
            run_propeller_baseline(base, sym, save_model=True, root=root)

    # Conflict with allow_overwrite=False quarantines staging file and preserves existing output
    existing = tmp_path / "repro.mph"
    existing.write_bytes(b"KEEP_ME")

    def write_staging_runner(command, **kwargs):
        assert Path(command[0]).name in {"comsolbatch.exe", "comsolbatch", "java.exe", "java"}
        staging_arg = Path(command[-2])
        staging_arg.write_bytes(b"NEW_STAGE")
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_eval_payload(1400.0)), ""
        )

    with pytest.raises(FileExistsError):
        run_propeller_baseline(
            base,
            existing,
            direction="cw",
            save_model=True,
            allow_overwrite=False,
            root=root,
            process_runner=write_staging_runner,
        )
    assert existing.read_bytes() == b"KEEP_ME"
    assert list((tmp_path / "quarantine").glob("conflict_*_repro.mph"))

    # Tampering baseline during eval_propeller quarantines and raises ModelValidationError
    def tamper_eval_runner(command, **kwargs):
        Path(command[-2]).write_bytes(b"STAGED_EVAL")
        base.write_bytes(b"CORRUPTED_BASE")
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_eval_payload(-1400.0, "eval_propeller")), ""
        )

    with pytest.raises(ModelValidationError, match="modified during eval_propeller"):
        eval_propeller(
            base,
            out_dir,
            case_id="case_01",
            speed_rpm=-1400.0,
            save_model=True,
            root=root,
            process_runner=tamper_eval_runner,
        )
    assert not (out_dir / "case_01" / "solved.mph").exists()
    assert list((out_dir / "case_01" / "quarantine").glob("failed_*_solved.mph"))

    # Solver error quarantines partial staging file and raises SolverFailedError
    base.write_bytes(b"ORIGINAL_MODEL")

    def solver_fail_runner(command, **kwargs):
        Path(command[-2]).write_bytes(b"PARTIAL_STAGE")
        err = {"status": "ERROR", "error_code": "SOLVER_FAILED", "message": "Diverged", "details": ""}
        return subprocess.CompletedProcess(command, 0, _wrap_json(err), "")

    with pytest.raises(SolverFailedError, match="Diverged"):
        eval_propeller(
            base,
            out_dir,
            case_id="case_02",
            speed_rpm=1400.0,
            save_model=True,
            root=root,
            process_runner=solver_fail_runner,
        )
    assert list((out_dir / "case_02" / "quarantine").glob("failed_*_solved.mph"))


def test_eval_propeller_ascii_staging_path_enforcement(tmp_path, monkeypatch):
    root = _make_fake_comsol_root(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"class")
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda r, **kw: class_file)

    base = tmp_path / "base.mph"
    base.write_bytes(b"MODEL")

    # Reject invalid non-ASCII/space case_id and output filename
    with pytest.raises(InvalidParameterError):
        eval_propeller(base, tmp_path / "out", case_id="case 01_测试", speed_rpm=1400.0, root=root)
    with pytest.raises(InvalidParameterError):
        run_propeller_baseline(base, tmp_path / "测试 model.mph", save_model=True, root=root)

    # Non-ASCII parent directory still gets pure-ASCII staging filename
    unicode_out = tmp_path / "工作区 测试"
    captured_cmds: list[list[str]] = []

    def runner(command, **kwargs):
        captured_cmds.append(list(command))
        Path(command[-2]).write_bytes(b"SOLVED")
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_eval_payload(1400.0, "eval_propeller")), ""
        )

    res = eval_propeller(
        base,
        unicode_out,
        case_id="case_ascii_01",
        speed_rpm=1400.0,
        save_model=True,
        root=root,
        process_runner=runner,
    )
    assert Path(res["output_model"]).is_file()
    staging_arg = Path(captured_cmds[0][-2])
    assert staging_arg.name.isascii()
    assert not any(c.isspace() for c in staging_arg.name)
    assert staging_arg.name.endswith("_case_ascii_01.mph")


@pytest.mark.parametrize("rpm,expected_dir", [(1400.0, "cw"), (-1400.0, "ccw")])
def test_bc_swapping_payload_by_speed_rpm_sign(tmp_path, monkeypatch, rpm, expected_dir):
    root = _make_fake_comsol_root(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"class")
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda r, **kw: class_file)

    base = tmp_path / "base.mph"
    base.write_bytes(b"MODEL")
    captured_props: list[dict[str, str]] = []

    def runner(command, **kwargs):
        captured_props.append(_decode_b64_props(command[-1]))
        return subprocess.CompletedProcess(
            command, 0, _wrap_json(_make_eval_payload(rpm, "eval_propeller")), ""
        )

    res = eval_propeller(
        base,
        tmp_path / "out",
        case_id=f"case_{expected_dir}",
        speed_rpm=rpm,
        save_model=False,
        root=root,
        process_runner=runner,
    )
    assert float(captured_props[0]["speed_rpm"]) == rpm
    assert res["direction"] == expected_dir
    if rpm > 0:
        assert res["bc_selection"]["upstream"] == [29]
        assert res["bc_selection"]["open"] == [1, 2, 3]
    else:
        assert res["bc_selection"]["upstream"] == [1]
        assert res["bc_selection"]["open"] == [2, 3, 29]

    with pytest.raises(InvalidParameterError):
        eval_propeller(base, tmp_path / "out", case_id="zero_rpm", speed_rpm=0.0, root=root)
    with pytest.raises(InvalidParameterError):
        eval_propeller(base, tmp_path / "out", case_id="nan_rpm", speed_rpm=math.nan, root=root)


def test_comsol_solver_lock_serializes_concurrent_threads(tmp_path):
    active = 0
    max_active = 0

    def worker(_idx: int) -> None:
        nonlocal active, max_active
        with comsol_solver_lock(lock_dir=tmp_path / ".locks", timeout_sec=10.0):
            active += 1
            max_active = max(max_active, active)
            time.sleep(0.05)
            active -= 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(4)))
    assert max_active == 1


def test_verify_baseline_kpis_exact_cw_ccw_and_gates():
    cw = _make_eval_payload(1400.0)
    ccw = _make_eval_payload(-1400.0)
    report = verify_baseline_kpis(cw, ccw)
    assert report["passed"] is True
    assert report["eta_sym"] == pytest.approx(0.5099663, rel=1e-5)
    assert report["failures"] == []

    # Reject wrong BC selection
    bad_bc = _make_eval_payload(1400.0)
    bad_bc["bc_selection"]["upstream"] = [1]
    assert verify_baseline_kpis(bad_bc, ccw)["passed"] is False

    # Reject mass imbalance >= 1.0%
    bad_imb = _make_eval_payload(1400.0)
    bad_imb["flow_audit"]["mass_imbalance_plane_pct"] = 1.25
    assert verify_baseline_kpis(bad_imb, ccw)["passed"] is False

    # Reject solver_err > 0.0025
    bad_err = _make_eval_payload(-1400.0)
    bad_err["solver_stats"]["max_solver_err"] = 0.0030
    assert verify_baseline_kpis(cw, bad_err)["passed"] is False

    # Reject NaN
    bad_nan = _make_eval_payload(1400.0)
    bad_nan["kpis"]["thrust_N"] = float("nan")
    assert verify_baseline_kpis(bad_nan, ccw)["passed"] is False


def test_reproduce_propeller_baselines_mocked_and_no_overwrite(tmp_path, monkeypatch):
    root = _make_fake_comsol_root(tmp_path)
    class_file = tmp_path / "ComsolBridge.class"
    class_file.write_bytes(b"class")
    monkeypatch.setattr(bridge, "ensure_bridge_compiled", lambda r, **kw: class_file)

    cw_mph = tmp_path / "cw.mph"
    ccw_mph = tmp_path / "ccw.mph"
    cad_xt = tmp_path / "prop.x_t"
    cw_mph.write_bytes(b"CW_MODEL")
    ccw_mph.write_bytes(b"CCW_MODEL")
    cad_xt.write_bytes(b"CAD_XT")
    out_dir = tmp_path / "baseline_out"

    def runner(command, **kwargs):
        cmd = command[-2] if "inspect-propeller" in command else command[-3]
        if cmd == "inspect-propeller":
            model_arg = Path(command[-1])
            rpm = 1400.0 if "cw" in model_arg.name.lower() else -1400.0
            return subprocess.CompletedProcess(
                command, 0, _wrap_json(_make_inspect_payload(str(model_arg), rpm=rpm)), ""
            )
        props = _decode_b64_props(command[-1])
        rpm = float(props["speed_rpm"])
        return subprocess.CompletedProcess(command, 0, _wrap_json(_make_eval_payload(rpm)), "")

    summary = reproduce_propeller_baselines(
        cw_model=cw_mph,
        ccw_model=ccw_mph,
        cad_path=cad_xt,
        output_dir=out_dir,
        pin_hashes=False,
        allow_overwrite=False,
        root=root,
        process_runner=runner,
    )
    assert summary["status"] == "SUCCESS"
    assert summary["immutability_verified"] is True
    assert summary["verification"]["passed"] is True
    assert (out_dir / "cw_1400rpm_inspect.json").is_file()
    assert (out_dir / "ccw_1400rpm_inspect.json").is_file()
    assert (out_dir / "baseline_summary.json").is_file()

    # Second run without allow_overwrite refuses to overwrite summary/inspect files
    with pytest.raises(FileExistsError):
        reproduce_propeller_baselines(
            cw_model=cw_mph,
            ccw_model=ccw_mph,
            cad_path=cad_xt,
            output_dir=out_dir,
            pin_hashes=False,
            allow_overwrite=False,
            root=root,
            process_runner=runner,
        )


def test_cli_propeller_inspect_and_baseline(tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc1:
        cli_main(["propeller-inspect", "--help"])
    assert exc1.value.code == 0

    with pytest.raises(SystemExit) as exc2:
        cli_main(["propeller-baseline", "--help"])
    assert exc2.value.code == 0

    capsys.readouterr()

    fake_res = _make_inspect_payload("cw.mph")
    fake_res["sha256"] = "ABCD"
    monkeypatch.setattr("hydro_disturb_bench.comsol_java.inspect_propeller", lambda p: fake_res)
    out_json = tmp_path / "inspect.json"
    rc = cli_main(["propeller-inspect", "--model", "cw.mph", "--out", str(out_json)])
    assert rc == 0
    captured = json.loads(capsys.readouterr().out)
    assert captured["operation"] == "inspect_propeller"
    assert out_json.is_file()
