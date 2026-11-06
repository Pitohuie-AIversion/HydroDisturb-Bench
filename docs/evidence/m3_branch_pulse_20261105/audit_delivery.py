"""Verify the lightweight delivery; optionally include local-only raw archives.

Run from the repository root. This does not launch COMSOL or require MPH files.
"""
import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check(raw, record):
    assert len(raw) == record["bytes"], record["path"]
    assert digest(raw) == record["sha256"], record["path"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", default="HEAD", help="Use INDEX for staged blobs")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-local-archives", action="store_true",
                        help="Also verify the 14 large archives that are not pushed to Git")
    args = parser.parse_args()
    assert not args.output.exists(), "Refusing to replace an existing audit"
    root = Path.cwd()
    folder = root / "docs/evidence/m3_branch_pulse_20261105"
    manifest = read(folder / "archive_manifest.json")
    regression = read(folder / "regression_summary.json")
    all_records = manifest["archives"] + [regression["archive"]]
    records = [item for item in all_records
               if args.include_local_archives or item.get("storage", "git") == "git"]
    native = {}
    count = 0
    archive_bytes = 0
    for record in records:
        path = folder / record["path"]
        check(path.read_bytes(), record)
        assert path.stat().st_size < 100_000_000
        archive_bytes += path.stat().st_size
        with zipfile.ZipFile(path) as archive:
            assert len(archive.namelist()) == len(set(archive.namelist()))
            assert set(archive.namelist()) == {item["path"] for item in record["files"]}
            for item in record["files"]:
                raw = archive.read(item["path"])
                check(raw, item)
                count += 1
                native[item["path"]] = (path, item)
    for item in manifest["display_files"]:
        check((folder / item["path"]).read_bytes(), item)
    for item in regression["displays"]:
        check((folder / item["path"]).read_bytes(), item)
    script = manifest["packaging_script"]
    assert digest((folder / script["path"]).read_bytes()) == script["sha256"]
    for path in folder.glob("*.json"):
        read(path)

    steps = {}
    for branch in ("zero", "pulse"):
        item = next(x for x in manifest["display_files"] if x["path"] == f"{branch}_solver_stdout.txt")
        expected = (folder / item["path"]).read_bytes()
        if args.include_local_archives:
            archive_path, record = native[item["source_member"]]
            with zipfile.ZipFile(archive_path) as archive:
                source = archive.read(record["path"])
            assert digest(source) == item["source_sha256"]
            text = source.split(b"<<<JSON_START>>>", 1)[0].decode("utf-8")
            derived = "".join(line.rstrip(" \t") + "\n" for line in text.splitlines()).encode("utf-8")
            assert derived == expected
        else:
            text = expected.decode("utf-8")
        rows = re.findall(r"^\s*(\d+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)(?:\s|$)", text, re.MULTILINE)
        rows = [(int(n), float(t), float(dt)) for n, t, dt in rows if int(n) > 0]
        assert [x[0] for x in rows] == list(range(1, 458))
        assert max(x[2] for x in rows) <= 0.05
        assert rows[-1][1] == 200.0
        steps[branch] = {"accepted_steps": len(rows), "max_step_s": max(x[2] for x in rows),
                         "end_time_s": rows[-1][1], "display_lines": len(expected.splitlines()),
                         "rederived_from_native_stdout": args.include_local_archives}

    report = root / "docs/STAGE_M3_BRANCH_PULSE_20261105.md"
    links = re.findall(r"\]\(([^)]+)\)", report.read_text(encoding="utf-8-sig"))
    local_links = [link for link in links if not link.startswith("https://")]
    assert all((report.parent / link).is_file()
               or (report.parent / link).resolve() == args.output.resolve() for link in local_links)
    evidence = read(folder / "evidence_manifest.json")
    git_names = []
    for item in evidence["files"]:
        path = folder / item["path"]
        check(path.read_bytes(), item)
        assert item["bytes"] <= 1_048_576, f"Remote delivery contains a large file: {path}"
        name = path.relative_to(root).as_posix()
        spec = ":" + name if args.git_ref == "INDEX" else args.git_ref + ":" + name
        raw = subprocess.check_output(["git", "show", spec])
        check(raw, item)
        git_names.append(name)
    result = {"status": "PASS", "git_ref": args.git_ref, "git_blob_count": len(git_names),
              "mode": "including_local_archives" if args.include_local_archives else "portable",
              "zip_count": len(records), "native_members": count, "total_zip_bytes": archive_bytes,
              "local_only_archives_excluded": len(all_records) - len(records),
              "report_local_links": len(local_links), "steps": steps,
              "scope": "Selected ZIP bytes and members, current display hashes, JSON syntax, report links and Git blobs; native stdout rederived only with --include-local-archives. Portable mode does not reproduce full-vector or full-field checks; no CFD solve."}
    with args.output.open("xb") as stream:
        stream.write((json.dumps(result, indent=2) + "\n").encode("utf-8"))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
