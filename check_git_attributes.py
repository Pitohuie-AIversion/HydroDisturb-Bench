#!/usr/bin/env python3
"""Convenience root wrapper for scripts/check_git_attributes.py"""
import sys
from pathlib import Path

# Add repo root to path and delegate
script_path = Path(__file__).resolve().parent / "scripts" / "check_git_attributes.py"
if script_path.exists():
    import importlib.util
    spec = importlib.util.spec_from_file_location("check_git_attributes", script_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.exit(mod.main())
else:
    print(f"Error: {script_path} not found", file=sys.stderr)
    sys.exit(1)
