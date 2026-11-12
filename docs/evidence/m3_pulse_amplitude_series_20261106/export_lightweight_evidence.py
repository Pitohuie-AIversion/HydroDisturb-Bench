"""Export small, traceable amplitude-series evidence without COMSOL or model reads.

Usage: python export_lightweight_evidence.py --run DATA_RUN --output EVIDENCE_DIR
Existing output files are never replaced. Original MPH, NPZ, field TXT, full JSON,
and full process stdout stay in the run directory and are marked local_only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

MAX_JSON_BYTES = 1024 * 1024
MAX_ARRAY_ITEMS = 128
MAX_STRING_CHARS = 8192
SERIES_ID = "m3-pulse-amplitude-series-v1"
BRANCHES = {"a0025": 0.0025, "a0100": 0.010}
STEP_HEADER = re.compile(r"^\s*Step\s+Time\s+Stepsize\b", re.MULTILINE)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def json_bytes(value):
    data = (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    require(len(data) <= MAX_JSON_BYTES, "Lightweight JSON exceeds 1 MiB; refusing to truncate")
    return data


def summarize(value, omitted, path="", key="", parent=None):
    """Hash omissions explicitly; never fabricate replacement numeric values."""
    if key == "base64" and isinstance(value, str):
        data = base64.b64decode(value, validate=True)
        sha = digest(data)
        if isinstance(parent, dict) and "sha256" in parent:
            require(sha == str(parent["sha256"]).lower(), f"Native bytes/hash mismatch: {path}")
        record = {"omitted": True, "kind": "base64", "json_pointer": path,
                  "encoded_characters": len(value), "decoded_bytes": len(data), "sha256": sha,
                  "hash_basis": "decoded original bytes", "full_original": "local_only"}
        omitted.append(record)
        return record
    if isinstance(value, list) and len(value) > MAX_ARRAY_ITEMS:
        record = {"omitted": True, "kind": "array", "json_pointer": path, "count": len(value),
                  "sha256": digest(canonical(value)), "hash_basis": "canonical_json_utf8",
                  "full_original": "local_only"}
        omitted.append(record)
        return record
    if isinstance(value, str) and (key in {"log", "time_solver_log"}
                                   or len(value) > MAX_STRING_CHARS):
        record = {"omitted": True, "kind": "string", "json_pointer": path,
                  "characters": len(value), "utf8_bytes": len(value.encode("utf-8")),
                  "sha256": digest(value.encode("utf-8")), "hash_basis": "original decoded string UTF-8",
                  "full_original": "local_only"}
        omitted.append(record)
        return record
    if isinstance(value, dict):
        return {name: summarize(item, omitted, path + "/" + name.replace("~", "~0").replace("/", "~1"),
                                name, value) for name, item in value.items()}
    if isinstance(value, list):
        return [summarize(item, omitted, path + "/" + str(index))
                for index, item in enumerate(value)]
    return value


class Exporter:
    def __init__(self, run):
        self.run = run
        self.sources = {}
        self.outputs = {}
        self.model_references = []
        self.log_sources = []

    def read(self, path):
        data = path.read_bytes()
        relative = path.relative_to(self.run).as_posix()
        record = {"run_relative_path": relative, "absolute_path": str(path), "bytes": len(data),
                  "sha256": digest(data), "full_original": "local_only",
                  "verification": "actual source bytes read by this exporter"}
        self.sources[relative] = record
        return data, record

    def read_json(self, path):
        data, record = self.read(path)
        return json.loads(data.decode("utf-8-sig")), record

    def add(self, relative, data):
        require(relative not in self.outputs, f"Duplicate output name: {relative}")
        self.outputs[relative] = data

    def summary(self, source, destination):
        value, record = self.read_json(source)
        omitted = []
        content = summarize(value, omitted)
        result = {"evidence_kind": "summary_not_full_original", "source": record,
                  "omitted_fields_count": len(omitted),
                  "array_omission_threshold": MAX_ARRAY_ITEMS,
                  "canonical_json_definition": "sorted keys, compact separators, ensure_ascii=False, allow_nan=False, UTF-8",
                  "content": content}
        self.add(destination, json_bytes(result))
        return value, record

    def solver_stdout(self, stage, name):
        candidates = []
        for path in sorted(stage.glob("process_*_stdout.log")):
            raw, record = self.read(path)
            text = raw.decode("utf-8", errors="strict")
            prefix = text.split("<<<JSON_START>>>", 1)[0]
            if STEP_HEADER.search(prefix):
                candidates.append((path, text, prefix, record))
        require(len(candidates) == 1, f"Expected exactly one original step-table stdout: {name}")
        path, text, prefix, record = candidates[0]
        require("<<<JSON_START>>>" in text and "<<<JSON_END>>>" in text,
                "Original stdout lacks complete final JSON markers")
        require("BRANCH_TIME_START" in prefix and "BRANCH_TIME_FINISHED" in prefix,
                "Original stdout lacks completed Time execution markers")
        process_path = path.with_name(path.name.replace("_stdout.log", "_execution.json"))
        process, _ = self.summary(process_path, f"{name}_solver_process_summary.json")
        require(process["returncode"] == 0 and process["stdout"] == path.name,
                "Solver process did not complete successfully")
        normalized = "\n".join(line.rstrip(" \t") for line in prefix.splitlines()).rstrip("\n") + "\n"
        require(STEP_HEADER.search(normalized), "Normalized stdout lost step-table header")
        destination = f"{name}_solver_stdout.txt"
        self.add(destination, normalized.encode("utf-8"))
        self.log_sources.append({"output": destination, "source": record,
            "selection": "unique original process stdout with Step Time Stepsize before JSON_START",
            "transform": "prefix before first JSON_START; UTF-8 strict decode; LF; strip only horizontal line-end whitespace",
            "has_step_table_header": True, "time_finished_marker": True,
            "getter_used_as_log": False, "exported_bytes": len(normalized.encode("utf-8")),
            "exported_sha256": digest(normalized.encode("utf-8"))})

    def branch(self, name, amplitude):
        stage = self.run / name
        response, response_source = self.summary(stage / "readback.json", f"{name}_readback_summary.json")
        execution, _ = self.summary(stage / "execution.json", f"{name}_execution_summary.json")
        self.summary(stage / "series_execution.json", f"{name}_series_execution_summary.json")
        require(response["status"] == "SUCCESS" and response["operation"] == "run_pulse_branch"
                and response["series_id"] == SERIES_ID and response["amplitude_m_s2"] == amplitude,
                "Actual branch response does not identify the requested amplitude series")
        require(execution["status"] == "COMPLETE"
                and execution["readback_sha256"].lower() == response_source["sha256"],
                "Execution/readback byte hash mismatch")
        require(execution["source_sha256_before"].lower() == execution["source_sha256_after"].lower(),
                "Recorded checkpoint hash changed during solve")
        for role, path_key, hash_key in [("input_checkpoint", "source_model", "source_sha256_after"),
                                         ("output_model", "output_model", "output_sha256")]:
            require(response[path_key] == execution[path_key], "Model path differs from execution record")
            self.model_references.append({"branch": name, "role": role, "path": execution[path_key],
                "sha256": execution[hash_key], "hash_source": f"{name}/execution.json:{hash_key}",
                "verification": "reported existing execution hash; model bytes not reread by this exporter",
                "full_original": "local_only"})
        self.solver_stdout(stage, name)
        fields = self.run / f"sample_{name}" / "fields"
        manifest, _ = self.summary(fields / "sequence_manifest.json", f"sample_{name}/sequence_manifest_summary.json")
        self.summary(fields / "sequence_report.json", f"sample_{name}/sequence_report_summary.json")
        require(manifest["status"] == "PASS", "Sampling manifest has not passed")
        require(manifest["source_sha256"].lower() == execution["output_sha256"].lower(),
                "Sampling source differs from the solved output")
        for batch in manifest["batches"]:
            batch_name = batch["path"]
            require(Path(batch_name).name == batch_name and "/" not in batch_name and "\\" not in batch_name,
                    "Unsafe batch directory")
            batch_dir = fields / batch_name
            for filename, hash_key in [("metadata.json", "metadata_sha256"),
                                       ("bridge_response.json", "response_sha256")]:
                _, actual = self.summary(batch_dir / filename,
                    f"sample_{name}/{batch_name}/{filename.removesuffix('.json')}_summary.json")
                require(actual["sha256"] == batch[hash_key].lower(), "Sampling JSON hash mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run, output = args.run.resolve(strict=True), args.output.resolve()
    require(output != run and run not in output.parents, "Output must be outside the original run")
    exporter = Exporter(run)
    for name, amplitude in BRANCHES.items():
        exporter.branch(name, amplitude)
    for filename in ("reference_precheck.json", "reference_integrity_after.json"):
        data, _ = exporter.read(run / filename)
        require(len(data) <= MAX_JSON_BYTES, f"Reference integrity record exceeds 1 MiB: {filename}")
        require(json.loads(data.decode("utf-8-sig"))["status"] == "PASS",
                f"Reference integrity record has not passed: {filename}")
        exporter.add(filename, data)
    analysis_files = sorted((run / "analysis").rglob("*.json"))
    require(bool(analysis_files), "No completed analysis JSON files found")
    for source in analysis_files:
        data, _ = exporter.read(source)
        require(len(data) <= MAX_JSON_BYTES, f"Analysis JSON exceeds 1 MiB: {source}")
        json.loads(data.decode("utf-8-sig"))
        exporter.add(source.relative_to(run).as_posix(), data)
    manifest = {"schema_version": 1, "series_id": SERIES_ID, "status": "PASS",
        "scope": "Lightweight archive extraction, not a new CFD solve or independent physical acceptance",
        "original_run": str(run), "original_artifacts": "local_only",
        "models_npz_and_raw_field_txt_exported": False, "source_files": list(exporter.sources.values()),
        "model_hash_references": exporter.model_references, "solver_stdout_sources": exporter.log_sources,
        "outputs": [{"path": path, "bytes": len(data), "sha256": digest(data)}
                    for path, data in sorted(exporter.outputs.items())]}
    exporter.add("lightweight_export_manifest.json", json_bytes(manifest))
    for name in exporter.outputs:
        require(not (output / name).exists() and not (output / name).is_symlink(),
                f"Refusing to overwrite existing output: {output / name}")
    for name, data in exporter.outputs.items():
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
    print(json.dumps({"status": "PASS", "output": str(output), "files": len(exporter.outputs),
                      "bytes": sum(map(len, exporter.outputs.values())), "solver_logs": len(exporter.log_sources)},
                     indent=2))


if __name__ == "__main__":
    main()
