import copy
import json
from pathlib import Path
import pytest
from hydro_disturb_bench.config import base_quantities, read_config, validate_config
from hydro_disturb_bench.sampling import sample_cases
from hydro_disturb_bench.cli import main
from hydro_disturb_bench.comsol_adapter import _parameter_values, solve_single_case

CFG=Path(__file__).resolve().parents[1]/"configs"/"base_case.json"


def test_reynolds_velocity_consistent():
    q=base_quantities(read_config(CFG))
    assert q["Uin_m_s"] == pytest.approx(q["Re"]*q["nu_m2_s"]/q["D_m"])
    assert q["Uin_m_s"]==pytest.approx(0.01004)


def test_sampling_reproducible():
    cfg=read_config(CFG)
    first=sample_cases(cfg,count=5,seed=42)
    assert first==sample_cases(cfg,count=5,seed=42)
    assert first!=sample_cases(cfg,count=5,seed=43)
    assert all(c["status"]=="PLANNED_NOT_SIMULATED" for c in first)


def test_units_conversion_and_direction():
    cases=sample_cases(read_config(CFG),count=5,seed=2)
    for c in cases:
        p,h=c["physical"],c["hidden_truth"]
        assert h["A_accel_m_s2"]==pytest.approx(h["a_star"]*p["Uin_m_s"]**2/p["D_m"])
        assert h["dirx"]**2+h["diry"]**2==pytest.approx(1)
        vals=_parameter_values(c)
        assert vals["rho"].endswith("[kg/m^3]")
        assert vals["A_accel"].endswith("[m/s^2]")
        assert vals["Tend"].endswith("[s]")
        assert vals["dt_out"].endswith("[s]")


def test_invalid_ranges_rejected():
    cfg=read_config(CFG)
    cfg=copy.deepcopy(cfg)
    cfg["disturbance"]["sigma_D"]=[-0.1,0.1]
    with pytest.raises(ValueError): validate_config(cfg)


def test_future_forcing_outside_domain_rejected():
    cfg=read_config(CFG)
    cfg["disturbance"]["t0_star"]=[135,138]
    with pytest.raises(ValueError):validate_config(cfg)


def test_plan_writes_manifest_and_cases(tmp_path):
    folder=tmp_path/"pilot"
    assert main(["plan","--config",str(CFG),"--count","3","--seed","7","--out",str(folder)])==0
    m=json.loads((folder/"manifest.json").read_text())
    assert m["total"]==3 and m["status"]=="PLANNED_NOT_SIMULATED"
    assert len(list((folder/"cases").glob("*.json")))==3
    with pytest.raises(FileExistsError):
        main(["plan","--config",str(CFG),"--count","3","--out",str(folder)])


def test_no_simulator_claim_or_baseline_overwrite(tmp_path):
    fake=tmp_path/"base.mph"
    fake.write_bytes(b"not a COMSOL model")
    c=sample_cases(read_config(CFG),count=1,seed=1)[0]
    with pytest.raises(ValueError,match="original baseline"):
        solve_single_case(fake,c,fake)
    with pytest.raises(FileNotFoundError):
        solve_single_case(tmp_path/"missing.mph",c,tmp_path/"result.mph")


def test_comsol_adapter_mock_contract(tmp_path, monkeypatch):
    """Integration protocol only: this is NOT a substitute for a real COMSOL solve."""
    import sys
    from types import SimpleNamespace
    from hydro_disturb_bench.comsol_adapter import REQUIRED_PARAMETERS
    import hydro_disturb_bench.comsol_adapter as api

    calls = []
    class FakeModel:
        def parameters(self):
            return {name:"1" for name in REQUIRED_PARAMETERS}
        def parameter(self, key, value):
            calls.append(("set",key,value))
        def solve(self):
            calls.append(("solve",))
        def save(self,path):
            calls.append(("save",path))
    fake=FakeModel()
    monkeypatch.setitem(sys.modules,"mph",SimpleNamespace(start=lambda cores:SimpleNamespace(load=lambda path:fake)))
    model_file=tmp_path/"base.mph"
    model_file.write_bytes(b"fake")
    case=sample_cases(read_config(CFG),count=1,seed=12)[0]
    output=tmp_path/"result.mph"
    api.solve_single_case(model_file,case,output,cores=2)
    assert calls[-2:]==[("solve",),("save",str(output))]
    assert len([x for x in calls if x[0]=="set"])==len(REQUIRED_PARAMETERS)
    assert ("set","Re","100") in calls


def test_adapter_missing_parameter_fails_closed(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from hydro_disturb_bench.comsol_adapter import solve_single_case

    class FakeIncomplete:
        def parameters(self):
            return {"Re":"100"}
        def parameter(self, key, val):
            raise AssertionError("must not modify incomplete model")
        def solve(self):
            raise AssertionError("must not run")
    monkeypatch.setitem(sys.modules,"mph",SimpleNamespace(start=lambda cores:SimpleNamespace(load=lambda path:FakeIncomplete())))
    model_file=tmp_path/"base.mph"
    model_file.write_bytes(b"fake")
    case=sample_cases(read_config(CFG),count=1,seed=12)[0]
    with pytest.raises(ValueError,match="missing parameters"):
        solve_single_case(model_file,case,tmp_path/"new.mph")
