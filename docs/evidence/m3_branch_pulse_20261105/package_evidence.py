"""Archive completed branch evidence without replacing files or changing native bytes.

All ZIP members use repository-relative paths. Extract all ZIPs into one new
directory to restore the run tree. This script never launches COMSOL or creates
the final evidence_manifest.json; reports and regression logs are added later.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

LIMIT = 100_000_000
DEFAULT_RUN = "runs/re100_m3_branch_pulse_20261105_verified_v6"
DEFAULT_SCRATCH = "scratch/m3_branch_pulse_20261105"
DEFAULT_OUTPUT = "docs/evidence/m3_branch_pulse_20261105"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest_stream(handle):
    value = hashlib.sha256()
    for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
        value.update(block)
    return value.hexdigest()


def sha(path):
    with path.open("rb") as handle:
        return digest_stream(handle)


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_new(path, content):
    with path.open("xb") as handle:
        handle.write(content)


def json_bytes(data):
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def relative(path, root):
    require(not path.is_symlink(), f"Symlink is not an evidence source: {path}")
    return path.resolve().relative_to(root).as_posix()


def excluded_model(path):
    return path.suffix.lower() == ".mph" or ".locks" in path.parts or path.name.lower().endswith(".mph.lock")


def files(directory):
    require(directory.is_dir(), f"Missing evidence directory: {directory}")
    return sorted(path for path in directory.rglob("*") if path.is_file() and not excluded_model(path))


def archive(path, sources, root):
    entries = []
    require(sources, f"Empty archive group: {path.name}")
    with path.open("xb") as handle, zipfile.ZipFile(
        handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as bundle:
        for source in sorted(set(sources)):
            member = relative(source, root)
            before = sha(source)
            size = source.stat().st_size
            bundle.write(source, member)
            require(sha(source) == before and source.stat().st_size == size,
                    f"Source changed while archiving: {member}")
            entries.append({"path": member, "bytes": size, "sha256": before})
    require(path.stat().st_size < LIMIT, f"Archive exceeds the 100 MB limit: {path}")
    with zipfile.ZipFile(path) as bundle:
        require(len(bundle.infolist()) == len(entries), "Archive entry count changed")
        for entry in entries:
            require(bundle.getinfo(entry["path"]).file_size == entry["bytes"], "ZIP member size changed")
            with bundle.open(entry["path"]) as handle:
                require(digest_stream(handle) == entry["sha256"], f"ZIP bytes differ: {entry['path']}")
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha(path),
            "entry_count": len(entries), "uncompressed_bytes": sum(entry["bytes"] for entry in entries),
            "all_native_member_bytes_verified": True, "files": entries}


def executed_sources(run, root, scratch):
    snapshots = scratch / "executed_sources_v6"
    snapshot_manifest = read(snapshots / "manifest.json")
    records, source_paths, bindings, expected = {}, {snapshots / "manifest.json"}, [], {}
    for branch in ("zero", "pulse"):
        record = read(run / branch / "execution.json")
        require(record.get("status") == "COMPLETE", f"Incomplete {branch} execution")
        require(sha(run / branch / "readback.json") == record["readback_sha256"], f"Changed {branch} readback")
        implementation = record["implementation"]
        require(implementation["all_source_files_unchanged"], f"Execution sources changed in {branch}")
        for name, evidence in implementation["source_files"].items():
            path = Path(name)
            original_name = relative(path, root)
            snapshot = snapshots / path.name
            require(evidence["sha256_before"] == evidence["sha256_after"] == sha(snapshot)
                    == snapshot_manifest.get(original_name),
                    f"Saved implementation snapshot differs from executed {branch} version: {name}")
            source_paths.add(snapshot)
            expected[original_name] = evidence["sha256_before"]
            bindings.append({"branch": branch, "original_path": original_name,
                             "snapshot_member": relative(snapshot, root), "sha256": sha(snapshot),
                             "evidence": "execution.json:implementation.source_files"})
        compiled = Path(implementation["compiled_class_path"])
        original_name = relative(compiled, root)
        snapshot = snapshots / compiled.name
        require(implementation["compiled_class_sha256_after"] == sha(snapshot) == snapshot_manifest.get(original_name),
                f"Saved class differs from executed {branch} version")
        source_paths.add(snapshot)
        expected[original_name] = implementation["compiled_class_sha256_after"]
        bindings.append({"branch": branch, "original_path": original_name,
                         "snapshot_member": relative(snapshot, root), "sha256": sha(snapshot),
                         "evidence": "execution.json:implementation.compiled_class_sha256_after"})
        records[branch] = record
    require(set(snapshot_manifest) == set(expected), "Executed snapshot manifest has unexpected source paths")
    comparisons, diff = [], ""
    for name, digest in sorted(expected.items()):
        current, saved = root / name, snapshots / Path(name).name
        current_sha = sha(current)
        scope = "unchanged"
        if current_sha != digest:
            require(name == "scripts/run_m3_branch_pulse.py", f"Time implementation changed after solve: {name}")
            before_text, after_text = saved.read_text(encoding="utf-8-sig"), current.read_text(encoding="utf-8-sig")
            trees = [ast.parse(text) for text in (before_text, after_text)]
            for tree in trees:
                functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                             and node.name == "_parse_internal_steps"]
                require(len(functions) == 1, "Expected one step-parser function")
                functions[0].body = [ast.Pass()]
            require(ast.dump(trees[0], include_attributes=False) == ast.dump(trees[1], include_attributes=False),
                    "Final runner differs outside the post-solve step parser")
            scope = "post-solve _parse_internal_steps body only; other runner AST and all Java/bridge/class bytes unchanged"
            diff = "".join(difflib.unified_diff(before_text.splitlines(keepends=True), after_text.splitlines(keepends=True),
                                               fromfile=f"executed/{name}", tofile=f"final/{name}"))
        comparisons.append({"path": name, "executed_sha256": digest, "current_sha256": current_sha,
                            "changed_since_time_solve": current_sha != digest, "change_scope": scope})
    return sorted(source_paths), bindings, records, [root / name for name in sorted(expected)], comparisons, diff


def build_groups(root, run, scratch):
    require(read(run / "analysis/report.json").get("status") in ("PASS", "FAIL"),
            "Analysis must finish before packaging; a failed numerical result can still be archived honestly")
    groups = [("native_runtime.zip", [p for p in files(run)
               if p.relative_to(run).parts[0] not in ("sample_zero", "sample_pulse")])]
    for name in ("v6_zero_native_audit.json", "v6_pulse_native_audit.json",
                 "v6_cross_branch_native_audit.json", "native_precheck_actual.json", "audit_v6_native.py",
                 "audit_v6_cross_branch.py", "step_gate_actual_v6_progress_fix.json",
                 "native_precheck_actual_failure.json", "field_artifact_audit.json"):
        candidates = {directory / name for directory in (scratch, root / "scratch")
                      if (directory / name).is_file()}
        require(len(candidates) == 1, f"Missing or ambiguous final native audit: {name}")
        groups[0][1].extend(candidates)
    field_audit_script = root / "docs/evidence/m3_branch_pulse_20261105/audit_branch_fields.py"
    require(field_audit_script.is_file(), "Missing independent field audit script")
    groups[0][1].append(field_audit_script)
    for branch in ("zero", "pulse"):
        sample = run / f"sample_{branch}"
        sequence = sample / "fields"
        manifest = read(sequence / "sequence_manifest.json")
        require(manifest.get("status") == "PASS", f"Unpublished {branch} field sequence")
        batches = []
        for batch in manifest["batches"]:
            name = batch["path"]
            require(re.fullmatch(r"batch_\d+", name), f"Unsafe native batch path: {name}")
            batches.append(sequence / name)
        require(len(batches) == len(set(batches)), "Duplicate batch paths")
        batch_sources = {p for directory in batches for p in files(directory)}
        groups.append((f"native_{branch}_index.zip", [p for p in files(sample) if p not in batch_sources]))
        for directory in batches:
            groups.append((f"native_{branch}_{directory.name}.zip", files(directory)))
    failures = []
    for number in range(1, 9):
        failures.extend(files(scratch / f"prepare_{number:02d}"))
    for directory in sorted(scratch.glob("native_derivative_audit_*")):
        failures.extend(files(directory))
    for name in ("restart_v5_diagnostics.json", "restart_v5_diagnostics.py", "prepare_probe.py",
                 "inspect_native_derivatives.py", "compare_native_diagnostics.py", "live_model_inspection.json",
                 "history_audit.py", "history_audit.json", "independent_audit_plan.md"):
        path = scratch / name
        require(path.is_file(), f"Missing preparation diagnostic: {path}")
        failures.append(path)
    if (scratch / "history_audit_final.json").is_file():
        failures.append(scratch / "history_audit_final.json")
    for name in ("history_final_execution.json", "history_final_stdout.log", "history_final_stderr.log"):
        if (scratch / name).is_file():
            failures.append(scratch / name)
    old_prefix = root / "runs/re100_m3_branch_pulse_20261105_verified"
    for suffix in ("", "_v2", "_v3", "_v4", "_v5"):
        directory = Path(str(old_prefix) + suffix)
        failures.extend(files(directory))
        require(any(path.name == "failure.json" for path in files(directory)),
                f"Expected retained failure evidence in {directory}")
    # Keep every native archive below the repository's 100 MB upload limit.  The
    # retained failed COMSOL attempts contain large raw stdout/JSON responses,
    # so split them by uncompressed member size while preserving every member.
    failure_paths = sorted(set(failures))
    failure_parts = []
    part = []
    part_bytes = 0
    for path in failure_paths:
        size = path.stat().st_size
        if part and part_bytes + size > 80_000_000:
            failure_parts.append(part)
            part = []
            part_bytes = 0
        part.append(path)
        part_bytes += size
    if part:
        failure_parts.append(part)
    for index, part in enumerate(failure_parts):
        groups.append((f"preparation_failures_{index:02d}.zip", part))
    sources, bindings, records, final_sources, comparisons, diff = executed_sources(run, root, scratch)
    groups.append(("executed_sources.zip", sources))
    groups.append(("final_sources.zip", final_sources))
    members = [relative(path, root) for _, paths in groups for path in paths]
    require(len(members) == len(set(members)), "Same source path assigned to multiple native archives")
    return groups, bindings, records, comparisons, diff


def readback_summary(path, root, archive_name):
    omitted = []
    def reduce(value, location=""):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                current = f"{location}/{key}"
                if key == "base64" or (isinstance(item, str) and
                    (key in ("log", "time_solver_log", "batch_startup_failure") or
                     (key == "message" and len(item) > 4096))):
                    text = item if isinstance(item, str) else json.dumps(item)
                    metadata = {"omitted_from_display_only": True, "character_count": len(text),
                                "text_utf8_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
                    result[key] = metadata
                    omitted.append({"json_path": current, **metadata})
                else:
                    result[key] = reduce(item, current)
            return result
        if isinstance(value, list):
            return [reduce(item, f"{location}/{index}") for index, item in enumerate(value)]
        return value
    summarized = reduce(read(path))
    return json_bytes({"artifact_role": "Readable summary, not the original COMSOL response",
                       "native_archive": archive_name, "native_member": relative(path, root),
                       "native_file_sha256": sha(path),
                       "note": "All original fields and base64/log strings remain byte-exact in the native ZIP.",
                       "omitted_fields": omitted, "readback": summarized})


def display_files(run, root):
    displays = {}
    for branch in ("prepare", "zero", "pulse"):
        path = run / branch / "readback.json"
        displays[f"{branch}_readback_summary.json"] = (
            readback_summary(path, root, "native_runtime.zip"),
            {"source_member": relative(path, root), "source_sha256": sha(path),
             "transformation": "JSON summary; base64 and log fields replaced by explicit omission metadata; UTF-8 LF"})
    for branch in ("zero", "pulse"):
        matches = []
        for path in sorted((run / branch).glob("process_*_stdout.log")):
            raw = path.read_bytes()
            prefix = raw.split(b"<<<JSON_START>>>", 1)[0]
            if re.search(rb"^\s*Step\s+Time\s+Stepsize(?:\s|$)", prefix, re.MULTILINE):
                matches.append((path, prefix))
        require(len(matches) == 1, f"Missing/ambiguous original stdout step table for {branch}")
        path, prefix = matches[0]
        # Successful v6 standalone output is UTF-8. Do not silently repair decoding.
        text = prefix.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        # Keep the complete raw stdout in native_runtime.zip.  The repository
        # display copy removes only horizontal trailing whitespace so the
        # staged evidence passes git diff --check without changing step rows.
        display = "".join(line.rstrip(" \t") + "\n" for line in text.splitlines())
        displays[f"{branch}_solver_stdout.txt"] = (
            display.encode("utf-8"), {"source_member": relative(path, root), "source_sha256": sha(path),
                                  "transformation": "Stdout prefix before JSON envelope, strict UTF-8, LF; horizontal trailing whitespace removed for display only"})
    for name in ("report.json", "field_comparison.json", "native_validation.json", "step_validation.json"):
        path = run / "analysis" / name
        output_name = "analysis_report.json" if name == "report.json" else name
        displays[output_name] = (path.read_bytes().replace(b"\r\n", b"\n"),
                                 {"source_member": relative(path, root), "source_sha256": sha(path),
                                  "transformation": "Display copy with CRLF normalized to LF only"})
    return displays


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-dir", type=Path, default=Path(DEFAULT_RUN))
    parser.add_argument("--scratch-dir", type=Path, default=Path(DEFAULT_SCRATCH))
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT))
    args = parser.parse_args()
    root = args.repo_root.resolve()
    requested = [root / value for value in (args.run_dir, args.scratch_dir, args.output_dir)]
    require(not any(path.is_symlink() for path in requested), "Evidence directories must not be symlinks")
    run, scratch, output = [path.resolve() for path in requested]
    require(root.is_dir(), "Repository root is missing")
    protected = {p.relative_to(output).as_posix(): sha(p) for p in output.rglob("*") if p.is_file()} if output.exists() else {}
    groups, bindings, records, comparisons, source_diff = build_groups(root, run, scratch)
    displays = display_files(run, root)
    displays["post_solve_runner_diff.patch"] = (
        source_diff.encode("utf-8"),
        {"transformation": "Unified text diff from byte-verified executed runner snapshot to final runner; empty if identical"})
    script_target = output / "package_evidence.py"
    copy_script = Path(__file__).resolve() != script_target.resolve()
    planned = [name for name, _ in groups] + list(displays) + ["archive_manifest.json"]
    if copy_script:
        planned.append(script_target.name)
    require(len(planned) == len(set(planned)), "Duplicate output names")
    for name in planned:
        require(not (output / name).exists(), f"Refusing to replace existing evidence: {output / name}")
    output.mkdir(parents=True, exist_ok=True)
    archives = []
    for name, paths in groups:
        result = archive(output / name, paths, root)
        archives.append(result)
        print(f"Archived {name}: {result['entry_count']} files, {result['bytes']} bytes", flush=True)
    display_inventory = []
    for name, (content, provenance) in displays.items():
        path = output / name
        write_new(path, content)
        display_inventory.append({"path": name, "bytes": len(content), "sha256": sha(path), **provenance})
    if copy_script:
        write_new(script_target, Path(__file__).read_bytes())
    for name, digest in protected.items():
        require(sha(output / name) == digest, f"Protected existing evidence changed: {name}")
    for item in comparisons:
        require(sha(root / item["path"]) == item["current_sha256"], "Final source changed while packaging")
    manifest = {"schema_version": 1, "status": "PASS", "created_utc": datetime.now(timezone.utc).isoformat(),
                "scope": "Native byte-preserving archive and separate readable displays; this is not the final evidence manifest",
                "run_directory": relative(run, root), "zip_member_root": "repository root",
                "restore": "Extract every ZIP into one NEW directory, preserving member paths; the restored run is at run_directory.",
                "excluded": "MPH model files and model lock files; models remain in the local immutable run directories",
                "zip_size_limit_exclusive_bytes": LIMIT, "native_entry_count": sum(a["entry_count"] for a in archives),
                "archives": archives, "display_files": display_inventory,
                "executed_implementation_bindings": bindings,
                "executed_vs_final_source_comparison": comparisons,
                "branch_model_identities": {name: {key: value for key, value in record.items()
                    if key in ("source_model", "source_sha256_before", "source_sha256_after", "output_model", "output_sha256", "readback_sha256")}
                    for name, record in records.items()},
                "protected_existing_files_unchanged": protected,
                "packaging_script": {"path": script_target.name, "sha256": sha(script_target)},
                "final_evidence_manifest_created": False}
    write_new(output / "archive_manifest.json", json_bytes(manifest))
    print(json.dumps({"status": "PASS", "archives": len(archives), "native_entries": manifest["native_entry_count"],
                      "display_files": len(display_inventory), "output_dir": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
