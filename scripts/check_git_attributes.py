#!/usr/bin/env python3
"""
check_git_attributes.py - Directed verification of Git attribute rules and evidence integrity.

Validates that:
1. Binary formats (e.g. PNG) in docs/evidence/** are protected from line ending conversions (-text).
2. Explicit text formats (.txt, .json, .xml, .log, .md, .py) are normalized with LF line endings.
3. Git staging in an isolated repository preserves binary bytes identically while converting CRLF to LF in text.
4. All 82 files registered in the 3 evidence manifests match Git blob bytes and hashes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x02\x00\x00\x00\x02\x08\x02\x00\x00\x00\xfd\xd4\x9as"
    b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)


def verify_isolated_git_staging(attributes_content: str) -> None:
    """Simulate git add and check staged object bytes in an isolated repository."""
    print("--- [Test 1: Isolated Git staging with synthetic files] ---")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)

        (root / ".gitattributes").write_text(attributes_content, encoding="utf-8")

        test_dir = root / "docs" / "evidence" / "synthetic_test"
        test_dir.mkdir(parents=True, exist_ok=True)

        # 1. Binary PNG with CRLF in magic header (\x89PNG\r\n\x1a\n)
        png_path = test_dir / "sample.png"
        png_path.write_bytes(VALID_PNG_BYTES)

        # 2. Text formats with CRLF line endings
        text_samples = {
            "sample.txt": b"Line 1\r\nLine 2\r\n",
            "sample.json": b'{\r\n  "test": true\r\n}\r\n',
            "sample.xml": b"<root>\r\n  <item>1</item>\r\n</root>\r\n",
            "sample.log": b"2026-11-05 18:00:00 [INFO] log line\r\n",
            "sample.md": b"# Header\r\n\r\nText paragraph\r\n",
            "sample.py": b"# Python code\r\nx = 1\r\n",
        }
        for name, content in text_samples.items():
            (test_dir / name).write_bytes(content)

        # Run git add
        subprocess.run(["git", "add", "docs/evidence/"], cwd=root, check=True)

        # Check PNG
        staged_png = subprocess.check_output(
            ["git", "show", ":docs/evidence/synthetic_test/sample.png"], cwd=root
        )
        if staged_png != VALID_PNG_BYTES:
            has_sig = (staged_png[:8] == b"\x89PNG\r\n\x1a\n")
            raise AssertionError(
                f"PNG bytes corrupted during staging! Expected {len(VALID_PNG_BYTES)} bytes, "
                f"got {len(staged_png)} bytes. Signature match: {has_sig}"
            )
        print(f"  [PASS] sample.png: staged {len(staged_png)} bytes identical to original (header \\r\\n preserved)")

        # Check text formats
        for name, original in text_samples.items():
            staged = subprocess.check_output(
                ["git", "show", f":docs/evidence/synthetic_test/{name}"], cwd=root
            )
            expected_lf = original.replace(b"\r\n", b"\n")
            if staged != expected_lf:
                raise AssertionError(
                    f"Text file {name} failed LF normalization! Got {repr(staged)}, expected {repr(expected_lf)}"
                )
            if b"\r" in staged:
                raise AssertionError(f"Text file {name} contains carriage returns in Git object!")
            print(f"  [PASS] {name}: CRLF normalized to LF ({len(original)} -> {len(staged)} bytes)")


def verify_check_attr_in_workspace(repo_root: Path) -> None:
    """Check git check-attr text eol on representative project evidence files."""
    print("\n--- [Test 2: Workspace git check-attr output] ---")
    checks = [
        ("docs/evidence/s1b3_m3_time_20261105/vorticity_t195s_comsol.png", "text", "unset"),
        ("docs/evidence/s1b3_m3_time_20261105/vorticity_t195s_comsol.png", "eol", "unspecified"),
        ("docs/evidence/s1b3_tests_recaptured_20261105_153742/full_regression.txt", "text", "set"),
        ("docs/evidence/s1b3_tests_recaptured_20261105_153742/full_regression.txt", "eol", "lf"),
        ("docs/evidence/s1b3_m3_time_20261105/evidence_manifest.json", "text", "set"),
        ("docs/evidence/s1b3_m3_time_20261105/evidence_manifest.json", "eol", "lf"),
    ]
    for rel_path, attr, expected in checks:
        out = subprocess.check_output(
            ["git", "check-attr", attr, "--", rel_path], cwd=repo_root, text=True
        ).strip()
        # Output format: <path>: <attr>: <value>
        val = out.split(":")[-1].strip()
        if val != expected:
            raise AssertionError(f"check-attr mismatch for {rel_path} ({attr}): expected '{expected}', got '{val}'")
        print(f"  [PASS] {rel_path} -> {attr}: {val}")


def verify_git_blobs_against_manifests(repo_root: Path) -> None:
    """Check all manifest files against Git blob objects in HEAD (or HEAD^ if uncommitted)."""
    print("\n--- [Test 3: Read-only check of manifest files against Git blobs] ---")
    manifests = [
        "docs/evidence/s1b2_m3_20261105/evidence_manifest.json",
        "docs/evidence/s1b3_m3_time_20261105/evidence_manifest.json",
        "docs/evidence/s1b3_tests_recaptured_20261105_153742/evidence_manifest.json",
    ]

    checked = 0
    png_checked = 0
    failures = []

    for manifest_path in manifests:
        try:
            content = subprocess.check_output(
                ["git", "cat-file", "blob", f"HEAD:{manifest_path}"], cwd=repo_root
            )
        except subprocess.CalledProcessError:
            content = (repo_root / manifest_path).read_bytes()

        manifest = json.loads(content.decode("utf-8-sig"))
        base = manifest_path.rsplit("/", 1)[0]

        for name, expected in manifest["files"].items():
            path = f"{base}/{name}"
            blob = subprocess.check_output(
                ["git", "cat-file", "blob", f"HEAD:{path}"], cwd=repo_root
            )
            sha = hashlib.sha256(blob).hexdigest()
            checked += 1

            if name.endswith(".png"):
                png_checked += 1
                if blob[:8] != b"\x89PNG\r\n\x1a\n":
                    failures.append(f"{path}: Corrupt PNG header in Git blob!")

            if len(blob) != expected["archive_bytes"] or sha != expected["archive_sha256"]:
                failures.append(
                    f"{path}: bytes expected {expected['archive_bytes']} != actual {len(blob)}, "
                    f"sha expected {expected['archive_sha256']} != actual {sha}"
                )

    print(f"  Checked {checked} evidence files in Git blobs (including {png_checked} PNG images)")
    if failures:
        for f in failures:
            print(f"  [FAIL] {f}")
        raise AssertionError(f"Manifest verification failed with {len(failures)} mismatch(es)")
    print(f"  [PASS] All {checked} files match manifest archive_bytes and archive_sha256 identically.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify .gitattributes and evidence integrity.")
    parser.add_argument(
        "--attributes",
        default=".gitattributes",
        help="Path to .gitattributes file to validate (default: .gitattributes)",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent if Path(__file__).parent.name == "scripts" else Path.cwd()
    attr_file = Path(args.attributes)
    if not attr_file.is_absolute():
        attr_file = repo_root / attr_file

    if not attr_file.exists():
        print(f"ERROR: Attributes file not found: {attr_file}", file=sys.stderr)
        return 1

    attr_content = attr_file.read_text(encoding="utf-8")
    try:
        display_path = attr_file.relative_to(repo_root)
    except ValueError:
        display_path = attr_file.name
    print(f"Testing attributes from: {display_path}")

    try:
        verify_isolated_git_staging(attr_content)
        verify_check_attr_in_workspace(repo_root)
        verify_git_blobs_against_manifests(repo_root)
    except AssertionError as e:
        print(f"\nFAILURE: {e}", file=sys.stderr)
        return 1

    print("\nSUCCESS: All Git attribute and evidence blob integrity checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
