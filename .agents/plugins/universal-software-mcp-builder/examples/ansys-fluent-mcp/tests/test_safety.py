"""Unit and safety tests for ANSYS Fluent MCP server."""
import pytest
from pathlib import Path
from src.safety.immutability import compute_sha256, assert_cow

def test_cow_prevents_in_place_overwrite(tmp_path):
    p = tmp_path / "model.dat"
    p.write_text("baseline_content")
    with pytest.raises(ValueError, match="Copy-on-Write violation"):
        assert_cow(p, p)

def test_source_immutability(tmp_path):
    src = tmp_path / "baseline.dat"
    src.write_text("original")
    initial_hash = compute_sha256(src)
    assert compute_sha256(src) == initial_hash
