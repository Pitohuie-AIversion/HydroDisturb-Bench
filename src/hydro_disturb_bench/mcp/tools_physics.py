"""Physics Interfaces, Boundary Conditions, and Multiphysics Couplings Tools."""

from typing import Any, Dict, List, Optional, Union

from .executor import execute_java_code
from .safety import validate_cow_paths


def comsol_add_physics(
    source_model: str,
    output_model: str,
    physics_type: str,
    physics_tag: str,
    geom_tag: str = "geom1",
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds ANY physics interface to the model component.

    Supported physics types include (but are not limited to):
        - Fluid Flow (CFD): "LaminarFlow", "TurbulentFlowkeps", "TurbulentFlowkomega", "TwoPhaseFlowPhaseField"
        - Heat Transfer: "HeatTransferInSolids", "HeatTransferInFluids", "ConjugateHeatTransfer"
        - Structural Mechanics: "SolidMechanics", "Shell", "Beam", "Piezoelectricity"
        - Electromagnetics: "Electrostatics", "ElectricCurrents", "MagneticFields", "ElectromagneticWavesFrequencyDomain"
        - Acoustics: "PressureAcousticsFrequencyDomain", "PressureAcousticsTransient"
        - Chemical: "TransportOfDilutedSpecies", "Chemistry", "ReactingFlow"
        - Mathematics: "CoefficientFormPDE", "GeneralFormPDE", "WeakFormPDE"

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        physics_type: COMSOL internal physics type name.
        physics_tag: Identifier tag for this physics node (e.g. "spf", "ht", "solid").
        geom_tag: Associated geometry sequence tag (default "geom1").
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    snippet = f"""
        Physics p = model.component("{comp_tag}").physics().create("{physics_tag}", "{physics_type}", "{geom_tag}");
        System.out.println("PHYSICS_ADDED: " + p.tag() + " (" + p.getType() + ")");
    """
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "add_physics"
        res["physics_tag"] = physics_tag
        res["physics_type"] = physics_type
    return res


def comsol_add_physics_feature(
    source_model: str,
    output_model: str,
    physics_tag: str,
    feature_type: str,
    feature_tag: str,
    selection: Optional[List[int]] = None,
    properties: Optional[Dict[str, Union[str, List[str], float, int]]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Adds a boundary condition, domain feature, or source to a physics interface.

    Examples:
        - Inlet (CFD): feature_type="Inlet", properties={"U0": "1.5[m/s]"}
        - Outlet (CFD): feature_type="OpenBoundary" or "Outlet", properties={"p0": "0"}
        - Wall: feature_type="Wall", properties={"BoundaryCondition": "NoSlip"}
        - Heat Flux: feature_type="HeatFlux", properties={"q0": "1000[W/m^2]"}
        - Fixed Constraint (Solid): feature_type="Fixed"
        - Boundary Load (Solid): feature_type="BoundaryLoad", properties={"Fp": "10000[N/m^2]"}
        - Voltage (Electrostatics): feature_type="ElectricPotential", properties={"V0": "100[V]"}
        - Volume Force: feature_type="VolumeForce", properties={"F": ["0", "-9.81*spf.rho", "0"]}
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'Physics phys = model.component("{comp_tag}").physics("{physics_tag}");',
        f'PhysicsFeature f = phys.create("{feature_tag}", "{feature_type}");',
    ]
    if selection:
        sel_str = ", ".join(str(s) for s in selection)
        lines.append(f'f.selection().set(new int[]{{{sel_str}}});')

    if properties:
        for k, v in properties.items():
            if isinstance(v, (list, tuple)):
                arr_str = ", ".join(f'"{x}"' for x in v)
                lines.append(f'f.set("{k}", new String[]{{{arr_str}}});')
            else:
                lines.append(f'f.set("{k}", "{v}");')

    lines.append(f'System.out.println("PHYSICS_FEATURE_ADDED: {physics_tag}/{feature_tag} ({feature_type})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "add_physics_feature"
        res["physics_tag"] = physics_tag
        res["feature_tag"] = feature_tag
        res["feature_type"] = feature_type
    return res


def comsol_add_multiphysics(
    source_model: str,
    output_model: str,
    coupling_type: str,
    coupling_tag: str,
    coupled_physics: Optional[List[str]] = None,
    comp_tag: str = "comp1",
) -> Dict[str, Any]:
    """Establishes multiphysics couplings (e.g. FSI, Non-Isothermal Flow, Joule Heating, Thermal Stress).

    Args:
        source_model: Input .mph path.
        output_model: Output .mph path.
        coupling_type: Coupling feature type (e.g. "FluidStructureInteraction", "NonIsothermalFlow", "ElectromagneticHeating").
        coupling_tag: Identifier tag for this coupling (e.g. "nitf1", "fsi1").
        coupled_physics: List of physics tags being coupled (e.g. ["spf", "ht"]).
    """
    cow_err = validate_cow_paths(source_model, output_model)
    if cow_err:
        return cow_err

    lines = [
        f'MultiphysicsFeature mp = model.component("{comp_tag}").multiphysics().create("{coupling_tag}", "{coupling_type}");',
    ]
    if coupled_physics:
        for i, phys_tag in enumerate(coupled_physics):
            lines.append(f'mp.set("Physics{i+1}", "{phys_tag}");')

    lines.append(f'System.out.println("MULTIPHYSICS_COUPLING_ADDED: {coupling_tag} ({coupling_type})");')
    snippet = "\n        ".join(lines)
    res = execute_java_code(
        java_snippet=snippet,
        source_model=source_model,
        output_model=output_model,
    )
    if res.get("status") == "SUCCESS":
        res["operation"] = "add_multiphysics"
        res["coupling_tag"] = coupling_tag
        res["coupling_type"] = coupling_type
    return res
