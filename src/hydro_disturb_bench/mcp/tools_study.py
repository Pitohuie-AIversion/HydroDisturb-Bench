"""Study Types, Solver Configuration, and Async Solver Execution Tools."""

from typing import Any

from hydro_disturb_bench.comsol_java import run_study as _legacy_run_study

from .executor import execute_java_code
from .safety import format_error, license_lock, validate_cow_paths


def comsol_create_study(
    source_model: str,
    output_model: str,
    study_tag: str,
    study_type: str = "Stationary",
    physics_list: list[str] | None = None,
) -> dict[str, Any]:
    """Creates a Study sequence (Stationary, Transient, Frequency, Eigenfrequency).

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        study_tag: Study identifier (e.g. "std1", "std_transient").
        study_type: "Stationary", "Transient", "Frequency", "Eigenfrequency", "Parametric".
        physics_list: Optional list of physics tags to activate in this study.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    step_type_map = {
        "stationary": "Stationary",
        "transient": "Transient",
        "time": "Transient",
        "timedependent": "Transient",
        "frequency": "Frequency",
        "eigenfrequency": "Eigenfrequency",
    }
    step_type = step_type_map.get(study_type.lower().replace("-", ""), "Stationary")

    lines = [
        f'Study std = model.study().create("{study_tag}");',
        f'StudyStep step = std.create("step1", "{step_type}");',
    ]
    if physics_list:
        for p in physics_list:
            lines.append(f'step.set("physics", "{p}", true);')

    lines.append(f'System.out.println("STUDY_CREATED: {study_tag} ({step_type})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "create_study"
        res["study_tag"] = study_tag
        res["study_type"] = step_type
    return res


def comsol_configure_solver(
    source_model: str,
    output_model: str,
    study_tag: str = "std1",
    tlist: str | None = None,
    rtol: str | None = None,
    maxstep: str | None = None,
) -> dict[str, Any]:
    """Configures transient time stepping, relative tolerance, and solver bounds.

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        study_tag: Target study tag (default "std1").
        tlist: Output time interval expression, e.g. "range(0, 0.05, 10)".
        rtol: Relative convergence tolerance, e.g. "1e-3".
        maxstep: Maximum allowed solver internal time step.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'Study std = model.study("{study_tag}");',
    ]
    if tlist:
        lines.append(f'for (String st : std.feature().tags()) {{ if (std.feature(st).getType().contains("Time")) std.feature(st).set("tlist", "{tlist}"); }}')
    if rtol:
        lines.append(f'for (String st : std.feature().tags()) {{ try {{ std.feature(st).set("rtol", "{rtol}"); }} catch (Throwable t) {{}} }}')
    if maxstep:
        lines.append(f'for (String st : std.feature().tags()) {{ try {{ std.feature(st).set("maxstep", "{maxstep}"); }} catch (Throwable t) {{}} }}')
        lines.append(
            'for (String sTag : model.sol().tags()) { '
            'try { '
            'SolverSequence sSeq = model.sol(sTag); '
            'for (String fTag : sSeq.feature().tags()) { '
            'SolverFeature feat = sSeq.feature(fTag); '
            'if ("Time".equalsIgnoreCase(feat.getType())) { '
            'feat.set("maxstepconstraintbdf", "const"); '
            f'feat.set("maxstepbdf", "{maxstep}"); '
            '} } } catch (Throwable t) {} }'
        )

    lines.append(f'System.out.println("SOLVER_CONFIGURED: {study_tag}");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "configure_solver"
        res["study_tag"] = study_tag
        res["tlist"] = tlist
        res["rtol"] = rtol
        res["maxstep"] = maxstep
    return res


def comsol_solve_study(
    source_model: str,
    output_model: str,
    study_tag: str = "std1",
    tlist_override: str | None = None,
    rtol_override: str | None = None,
    maxstep_override: str | None = None,
    timeout_sec: int = 1800,
) -> dict[str, Any]:
    """Runs the COMSOL solver for the specified study under the license queue lock.

    Guarantees floating license queuing (FileLock), isolates native solver crashes,
    and returns solution metadata and log summaries.
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    # Acquire floating license lock
    with license_lock:
        try:
            return _legacy_run_study(
                model_path=source_model,
                output_path=output_model,
                study_tag=study_tag,
                tlist_override=tlist_override or "",
                rtol_override=rtol_override or "",
                maxstep_override=maxstep_override or "",
                timeout=float(timeout_sec) if timeout_sec else None,
            )
        except Exception as e:
            return format_error("SOLVER_FAILED", f"Study solver failed: {e}")
