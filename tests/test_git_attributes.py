"""
test_git_attributes.py - Regression test verifying that .gitattributes enforces:
1. Binary protection (-text) for docs/evidence/** by default.
2. Safe LF normalization for explicit text formats (.txt, .json, .xml, .log, .md, .py).
3. Real git staging in an isolated temporary repo preserves PNG bytes while normalizing text CRLF.
"""

from pathlib import Path

from scripts.check_git_attributes import (
    verify_check_attr_in_workspace,
    verify_git_blobs_against_manifests,
    verify_isolated_git_staging,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_gitattributes_file_rules():
    """Verify that .gitattributes exists and has safe rules."""
    attr_path = REPO_ROOT / ".gitattributes"
    assert attr_path.exists(), ".gitattributes must exist at repo root"
    content = attr_path.read_text(encoding="utf-8")
    assert "docs/evidence/** -text" in content, "Must default docs/evidence/** to -text"
    assert "docs/evidence/**/*.txt text eol=lf" in content
    assert "docs/evidence/**/*.json text eol=lf" in content


def test_isolated_git_staging_preserves_binary_and_normalizes_text():
    """Test git staging in a temporary repo using current .gitattributes."""
    attr_content = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    verify_isolated_git_staging(attr_content)


def test_workspace_git_check_attr():
    """Test that workspace git check-attr assigns unset to PNG and set to text."""
    verify_check_attr_in_workspace(REPO_ROOT)


def test_git_blobs_match_all_manifests():
    """Test that all 82 files in evidence manifests match Git blob objects in HEAD."""
    verify_git_blobs_against_manifests(REPO_ROOT)
