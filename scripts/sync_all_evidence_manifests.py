"""Normalize all evidence text files to strict LF binary bytes and synchronize manifests with Git blob bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MANIFEST_DIRS = [
    ROOT / "docs/evidence/s1b2_m3_20261105",
    ROOT / "docs/evidence/s1b3_m3_time_20261105",
    ROOT / "docs/evidence/s1b3_tests_recaptured_20261105_153742",
]


def normalize_dir(ev_dir: Path) -> dict:
    manifest_path = ev_dir / "evidence_manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing {manifest_path}")

    # Read existing manifest structure to preserve original_path references if present
    data = json.loads(manifest_path.read_bytes().decode("utf-8-sig"))

    # 1. Normalize all text files in ev_dir to strict LF bytes
    for item in sorted(ev_dir.iterdir()):
        if not item.is_file() or item.name == "evidence_manifest.json":
            continue
        if item.suffix in (".txt", ".json", ".xml", ".log"):
            raw = item.read_bytes()
            if b"\r\n" in raw:
                norm = raw.replace(b"\r\n", b"\n")
                item.write_bytes(norm)

    # 2. Update manifest file records with exact LF byte length and SHA-256
    files_map = data.get("files", {})
    new_files_map = {}

    for item in sorted(ev_dir.iterdir()):
        if item.name == "evidence_manifest.json" or not item.is_file():
            continue
        content = item.read_bytes()
        sha = hashlib.sha256(content).hexdigest()
        size = len(content)

        old_info = files_map.get(item.name, {})
        new_files_map[item.name] = {
            "archive_sha256": sha,
            "archive_bytes": size,
            "original_path": old_info.get("original_path"),
            "original_sha256": old_info.get("original_sha256"),
            "original_bytes": old_info.get("original_bytes"),
        }

    data["files"] = new_files_map

    manifest_bytes = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    return {
        "dir": str(ev_dir),
        "files_count": len(new_files_map),
    }


def main() -> None:
    for d in MANIFEST_DIRS:
        res = normalize_dir(d)
        print(f"Normalized {d.name}: {res['files_count']} items updated.")


if __name__ == "__main__":
    main()
