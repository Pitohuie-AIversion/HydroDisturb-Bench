"""Audit this lightweight delivery directly from Git blobs, without local CFD files.

Run from any location in the repository: --git-ref HEAD (default) or INDEX,
--output a new path outside the evidence folder. No manifest is generated here.
Manifest files use repository-relative paths, byte lengths and Git-byte SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

FOLDER = "docs/evidence/m3_pulse_amplitude_series_20261106"
MANIFEST = FOLDER + "/delivery_manifest.json"
SERIES_ID = "m3-pulse-amplitude-series-v1"
LIMIT = 1024 * 1024
REPORT = "docs/reports/M3_PULSE_AMPLITUDE_SERIES_20261106.md"
CODE = {"src/hydro_disturb_bench/comsol_java/ComsolBridge.java",
        "scripts/run_m3_pulse_amplitude_series.py", "tests/test_pulse_amplitude_series.py",
        FOLDER + "/export_lightweight_evidence.py", FOLDER + "/audit_delivery.py"}
GIT_ROOT = None


def require(condition, message):
    if not condition:
        raise ValueError(message)


def git(*args):
    prefix = ["git", "-C", str(GIT_ROOT)] if GIT_ROOT else ["git"]
    return subprocess.check_output([*prefix, *args])


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value,
            "Manifest path must be repository-relative POSIX text")
    parsed = PurePosixPath(value)
    require(not parsed.is_absolute() and str(parsed) == value and ".." not in parsed.parts,
            f"Unsafe manifest path: {value}")
    return value


def tree(ref):
    records = {}
    if ref == "INDEX":
        for record in git("ls-files", "--stage", "-z").split(b"\0"):
            if not record:
                continue
            info, path = record.split(b"\t", 1)
            mode, oid, stage = info.decode("ascii").split()
            require(stage == "0", "Cannot audit an unmerged index")
            records[path.decode("utf-8")] = (mode, oid)
    else:
        for record in git("ls-tree", "-r", "-z", ref).split(b"\0"):
            if not record:
                continue
            info, path = record.split(b"\t", 1)
            mode, _, oid = info.decode("ascii").split()
            records[path.decode("utf-8")] = (mode, oid)
    return records


def blob(records, path):
    require(path in records, f"Missing Git blob: {path}")
    mode, oid = records[path]
    require(mode in {"100644", "100755"}, f"Expected regular Git file: {path}")
    size = int(git("cat-file", "-s", oid))
    require(size <= LIMIT, f"Git file exceeds 1 MiB: {path}")
    raw = git("cat-file", "blob", oid)
    require(len(raw) == size, f"Git blob size mismatch: {path}")
    return raw


def json_blob(records, path):
    return json.loads(blob(records, path).decode("utf-8-sig"))


def delivery_parent(manifest, base_ref, ref):
    parent = git("rev-parse", "--verify", "--end-of-options",
                 manifest.get("delivery_parent_commit", base_ref) + "^{commit}").decode("ascii").strip()
    require(git("merge-base", base_ref, parent).decode("ascii").strip() == base_ref,
            "Delivery parent is not a descendant of the frozen physical baseline")
    if ref == "INDEX":
        head = git("rev-parse", "HEAD").decode("ascii").strip()
        require(head == parent, "Index delivery parent must equal the current Git HEAD")
    else:
        parents = git("show", "--no-patch", "--format=%P", ref).decode("ascii").split()
        require(parent in parents, "Declared delivery parent is not an actual parent of the audited commit")
    return parent


def required_paths():
    paths = CODE | {REPORT}
    evidence = {"preregistered_plan.json", "plan_freeze_record.json", "lightweight_export_manifest.json",
                "reference_precheck.json", "reference_integrity_after.json", "analysis/report.json"}
    for branch in ("a0025", "a0100"):
        evidence.update(f"{branch}_{suffix}" for suffix in
                        ("readback_summary.json", "execution_summary.json", "series_execution_summary.json",
                         "solver_process_summary.json", "solver_stdout.txt"))
        evidence.update(f"sample_{branch}/{name}_summary.json" for name in
                        ("sequence_manifest", "sequence_report"))
        evidence.update(f"analysis/{branch}/{name}.json" for name in
                        ("native_validation", "field_comparison", "step_validation", "report"))
    for suite in ("entrypoint_tests_final", "offline_regression"):
        evidence.update(f"logs/{suite}/{name}" for name in
                        ("stdout.txt", "stderr.txt", "execution.json", "junit.xml"))
    return paths | {FOLDER + "/" + path for path in evidence}


def check_summary(value, path):
    require(value.get("evidence_kind") == "summary_not_full_original"
            and value.get("source", {}).get("full_original") == "local_only",
            f"Summary misrepresents the full original: {path}")
    source = value["source"]
    require(isinstance(source.get("bytes"), int) and re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "")),
            f"Missing source hash/byte count: {path}")

    def walk(item):
        if isinstance(item, dict):
            if item.get("omitted"):
                require(item.get("full_original") == "local_only"
                        and re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", ""))
                        and any(key in item for key in ("count", "characters", "decoded_bytes")),
                        f"Omitted content lacks explicit identity: {path}")
                return 1
            return sum(walk(value) for value in item.values())
        if isinstance(item, list):
            return sum(walk(value) for value in item)
        return 0

    require(walk(value["content"]) == value["omitted_fields_count"], f"Omission count mismatch: {path}")


def main():
    global GIT_ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--git-ref", default="HEAD", help="Commit/ref, or INDEX for staged Git blobs")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    repo = Path(git("rev-parse", "--show-toplevel").decode("utf-8").strip()).resolve()
    GIT_ROOT = repo
    output = args.output.resolve()
    require(not output.exists() and not output.is_symlink(), "Refusing to overwrite an audit")
    require(not output.is_relative_to(repo / "docs/evidence"), "Write audit output outside historical evidence")
    ref = "INDEX" if args.git_ref.upper() == "INDEX" else git(
        "rev-parse", "--verify", "--end-of-options", args.git_ref + "^{commit}").decode("ascii").strip()
    current = tree(ref)
    manifest_raw = blob(current, MANIFEST)
    manifest = json.loads(manifest_raw.decode("utf-8-sig"))
    require(manifest.get("schema_version") == 1 and manifest.get("series_id") == SERIES_ID,
            "Unknown delivery manifest schema/series")
    base_ref = git("rev-parse", "--verify", "--end-of-options",
                   manifest["base_commit"] + "^{commit}").decode("ascii").strip()
    baseline = tree(base_ref)
    parent_ref = delivery_parent(manifest, base_ref, ref)
    parent_tree = tree(parent_ref)
    changed = {path for path, item in current.items() if parent_tree.get(path) != item}
    require(not (set(parent_tree) - set(current)), "Delivery deletes previously tracked files")
    require(not any(path.startswith("docs/evidence/") and not path.startswith(FOLDER + "/")
                    for path in changed), "Delivery modifies historical evidence")
    records = {}
    for item in manifest["files"]:
        path = safe_path(item["path"])
        require(path not in records and path != MANIFEST, "Duplicate/self-referencing manifest entry")
        records[path] = item
        raw = blob(current, path)
        require(len(raw) == item["bytes"] and digest(raw) == item["sha256"].lower(),
                f"Manifest differs from actual Git bytes: {path}")
    require(required_paths() <= set(records), "Missing mandatory code/plan/readback/log/analysis evidence: "
            + str(sorted(required_paths() - set(records))))
    require(changed - {MANIFEST} <= set(records), "Changed Git files omitted from manifest: "
            + str(sorted(changed - {MANIFEST} - set(records))))
    for path in changed:
        blob(current, path)  # Enforce 1 MiB even for the manifest itself.
    for group, paths in manifest.get("required_groups", {}).items():
        require(isinstance(paths, list) and all(safe_path(p) in records for p in paths),
                f"Required delivery group is incomplete: {group}")
    plan_raw = blob(current, FOLDER + "/preregistered_plan.json")
    plan = json.loads(plan_raw.decode("utf-8-sig"))
    freeze = json_blob(current, FOLDER + "/plan_freeze_record.json")
    require(plan["base_commit"] == base_ref and freeze["plan_sha256"].lower() == digest(plan_raw)
            and freeze["new_series_time_solves_started"] == 0, "Frozen plan binding failed")
    historical = []
    for name in ("base_plan", "restart_protocol"):
        record = plan[name]
        path = safe_path(record["path"])
        raw = blob(baseline, path)
        require(digest(raw) == record["sha256"].lower(), f"Fixed parent reference differs: {name}")
        historical.append({"role": name, "git_ref": base_ref, "path": path, "sha256": digest(raw)})
    summary_count = 0
    for path in records:
        if path.startswith(FOLDER + "/") and path.endswith("_summary.json"):
            value = json_blob(current, path)
            if "evidence_kind" in value:
                check_summary(value, path)
                summary_count += 1
    for branch in ("a0025", "a0100"):
        check_summary(json_blob(current, FOLDER + f"/{branch}_readback_summary.json"), branch)
        log = blob(current, FOLDER + f"/{branch}_solver_stdout.txt")
        text = log.decode("utf-8")
        require(b"\r" not in log and not re.search(r"[ \t]+$", text, re.MULTILINE), "Solver log is not clean LF text")
        require(re.search(r"^\s*Step\s+Time\s+Stepsize\b", text, re.MULTILINE)
                and "BRANCH_TIME_START" in text and "BRANCH_TIME_FINISHED" in text
                and "<<<JSON_START>>>" not in text, "Solver stdout prefix lacks a complete Time run")
    for filename in ("reference_precheck.json", "reference_integrity_after.json"):
        require(json_blob(current, FOLDER + "/" + filename)["status"] == "PASS", "Reference integrity did not pass")
    report = {"status": "PASS", "git_ref": ref, "base_commit": base_ref,
              "delivery_parent_commit": parent_ref,
              "manifest_git_sha256": digest(manifest_raw), "manifest_file_count": len(records),
              "changed_git_file_count": len(changed), "maximum_file_bytes": max(
                  int(git("cat-file", "-s", current[p][1])) for p in changed),
              "summary_count": summary_count, "fixed_parent_references": historical,
              "local_large_files_required": False, "comsol_started": False,
              "scope": "Actual Git blob identity/size, delivery completeness, frozen parent references, summary disclosures and solver stdout structure. No new CFD solve or full native-vector/field revalidation."}
    with output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
