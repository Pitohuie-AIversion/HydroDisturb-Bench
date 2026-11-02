"""Quick static check, independent of COMSOL."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
for rel in ["README.md", "configs/base_case.json", "docs/RESEARCH_PLAN.md", "docs/VALIDATION.md", "src/hydro_disturb_bench/cli.py"]:
    assert (ROOT/rel).is_file(), f"Missing: {rel}"
print("Repository scaffold is complete (COMSOL baseline/results NOT validated).")
