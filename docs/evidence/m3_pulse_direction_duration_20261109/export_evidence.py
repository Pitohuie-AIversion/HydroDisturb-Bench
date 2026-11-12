"""Export only small evidence; never execute COMSOL or replace an existing file.

Run after all three runs and scratch/comparison.json are complete. Use --seal
after adding the report and test logs. Source models and tensors stay local.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from hydro_disturb_bench.experiments.solver_steps import parse_internal_steps

CASES = ("reverse", "short", "long")
LIMIT = 1024 * 1024
STUDY = "m3-pulse-direction-duration-20261109"
REPORT = "docs/reports/M3_PULSE_DIRECTION_DURATION_20261109.md"


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def compact(value, omitted, pointer="", key="", parent=None):
    """Keep small actual getter values intact; identify every omitted payload."""
    kind = None
    if key == "base64" and isinstance(value, str):
        data, kind = base64.b64decode(value, validate=True), "decoded_binary"
        if isinstance(parent, dict) and "sha256" in parent:
            require(sha(data) == parent["sha256"].lower(), "Native byte identity differs: " + pointer)
    elif isinstance(value, list) and len(value) > 128:
        data = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
        kind = "canonical_json_array"
    elif isinstance(value, str) and (key == "time_solver_log" or len(value) > 8192):
        data, kind = value.encode("utf-8"), "utf8_string"
    if kind:
        record = {"omitted": True, "json_pointer": pointer, "hash_basis": kind,
                  "bytes": len(data), "sha256": sha(data), "storage": "local_only",
                  "items_or_characters": len(value)}
        omitted.append(record)
        return record
    if isinstance(value, dict):
        return {name: compact(item, omitted, pointer + "/" + name.replace("~", "~0").replace("/", "~1"), name, value)
                for name, item in value.items()}
    if isinstance(value, list):
        return [compact(item, omitted, pointer + "/" + str(index)) for index, item in enumerate(value)]
    return value


class Exporter:
    def __init__(self, root):
        self.root, self.outputs, self.sources, self.local_only = root, {}, {}, []
        self.transforms = []

    def read(self, path):
        raw = path.read_bytes()
        record = {"path": path.relative_to(self.root).as_posix(), "bytes": len(raw),
                  "sha256": sha(raw), "storage": "local_only", "verification": "source bytes read"}
        self.sources[record["path"]] = record
        return raw, record

    def add(self, name, raw):
        require(name not in self.outputs and len(raw) <= LIMIT, "Duplicate or oversized export: " + name)
        self.outputs[name] = raw

    def copy(self, path, name):
        raw, source = self.read(path)
        if name.endswith(".json") and b"\r\n" in raw:
            normalized = raw.replace(b"\r\n", b"\n")
            companion = name + ".raw"
            self.add(companion, raw)
            self.add(name, normalized)
            self.transforms.append({"output": name, "source": source, "raw_companion": companion,
                "output_bytes": len(normalized), "output_sha256": sha(normalized),
                "transform": "JSON CRLF converted to LF; exact original bytes retained in raw_companion (-text)"})
        else:
            self.add(name, raw)
        return json.loads(raw) if name.endswith(".json") else raw

    def original_output(self, name):
        return self.outputs[name + ".raw"] if name + ".raw" in self.outputs else self.outputs[name]

    def text_copy(self, path, name):
        raw, source = self.read(path)
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        normalized = text.encode("utf-8")
        self.add(name, normalized)
        self.transforms.append({"output": name, "source": source, "output_bytes": len(normalized),
            "output_sha256": sha(normalized), "transform": "UTF-8 strict decode; CRLF and bare CR converted to LF; all other content preserved"})

    def summary(self, path, name):
        raw, source = self.read(path)
        original = json.loads(raw)
        omitted = []
        content = compact(original, omitted)
        self.add(name, encode({"evidence_kind": "summary_not_full_original", "source": source,
                               "omitted_fields_count": len(omitted), "omitted": omitted, "content": content}))
        return original, source

    def log(self, stage, prefix, validation, plan):
        steps = validation["steps"]
        require(steps["source"].startswith("process_") and steps["source"].endswith("_stdout.log"),
                "Expected authoritative original stdout; getter fallback needs explicit review")
        path = stage / steps["source"]
        raw, source = self.read(path)
        require(sha(raw) == steps["source_bytes_sha256"], "Step-validation log source changed")
        text = raw.decode("utf-8")
        require("<<<JSON_START>>>" in text and "<<<JSON_END>>>" in text, "Incomplete bridge stdout")
        text = text.split("<<<JSON_START>>>", 1)[0]
        require("TIME_START" in text and "TIME_FINISHED" in text, "Missing solver run boundary markers")
        normalized = "\n".join(line.rstrip(" \t") for line in text.splitlines()).rstrip("\n") + "\n"
        analysis = plan["analysis_plan"]
        parsed = parse_internal_steps(normalized, analysis["branch_time_s"], analysis["stop_s"],
                                      analysis["solver"]["maxstepbdf_s"])
        require(all(steps.get(key) == value for key, value in parsed.items()), "Exported step table differs")
        log = normalized.encode("utf-8")
        self.add(prefix + "/solver_stdout.txt", log)
        self.add(prefix + "/solver_log_extraction.json", encode({
            "source": source, "transform": "UTF-8 strict; prefix before first JSON_START; LF; strip horizontal line-end whitespace",
            "output_sha256": sha(log), "output_bytes": len(log), "parsed_steps": parsed,
            "getter_used_as_log": False}))
        process = self.copy(path.with_name(path.name.replace("_stdout.log", "_execution.json")),
                            prefix + "/process_execution.json")
        require(process["returncode"] == 0 and process["stdout"] == path.name, "Solver process failed")

    def case(self, name, specification):
        run = self.root / specification["run_dir"]
        prefix = "cases/" + name
        for filename in ("plan.json", "config.input.json", "event.properties"):
            self.copy(run / filename, prefix + "/" + filename)
        plan = json.loads(self.outputs[prefix + "/plan.json"])
        require(sha(self.original_output(prefix + "/plan.json")) == specification["run_plan_sha256"], "Run plan changed")
        for mode in ("inspect", "solve"):
            stage, target = run / mode, prefix + "/" + mode
            response, identity = self.summary(stage / "readback.json", target + "/readback_summary.json")
            execution = self.copy(stage / "execution.json", target + "/execution.json")
            validation = self.copy(stage / "validation.json", target + "/validation.json")
            require(execution["status"] == "COMPLETE" and validation["status"] == "PASS"
                    and identity["sha256"] == execution["readback_sha256"], "Incomplete native stage")
            require(response["source_model"] == execution["source_model"]
                    and response["output_model"] == execution["output_model"], "Native model binding differs")
            for role, pathkey, hashkey in (("input_checkpoint", "source_model", "source_sha256_after"),
                                          ("output_model", "output_model", "output_sha256")):
                path = Path(execution[pathkey])
                self.local_only.append({"path": path.relative_to(self.root).as_posix(), "bytes": path.stat().st_size,
                    "sha256": execution[hashkey], "storage": "local_only", "role": name + "/" + mode + "/" + role,
                    "verification": "hash from execution; size stat only, model not reread by exporter"})
            if mode == "solve":
                self.log(stage, target, validation, plan)
        sampled = self.copy(run / "sample_solve/execution.json", prefix + "/sample_execution.json")
        if "publication_recovery_sha256" in sampled:
            recovery = self.copy(run / "sample_solve/publication_recovery.json", prefix + "/recovery/publication_recovery.json")
            require(recovery["status"] == "PASS" and sampled["publication_recovery_sha256"]
                    == sha(self.original_output(prefix + "/recovery/publication_recovery.json")), "Recovery identity differs")
            for filename in ("publication_retry_admission.json", "failure.json"):
                self.copy(run / "sample_solve" / filename, prefix + "/recovery/" + filename)
            self.copy(run / "sample_solve/fields/failure.json", prefix + "/recovery/stage_failure.json")
        manifest = self.copy(run / "sample_solve/fields/sequence_manifest.json", prefix + "/sequence_manifest.json")
        self.copy(run / "sample_solve/fields/sequence_report.json", prefix + "/sequence_report.json")
        self.copy(run / "analysis.json", prefix + "/analysis.json")
        for batch in manifest["batches"]:
            path = run / "sample_solve/fields" / batch["path"] / "field_sample.npz"
            self.local_only.append({"path": path.relative_to(self.root).as_posix(), "bytes": path.stat().st_size,
                "sha256": batch["npz_sha256"].lower(), "storage": "local_only", "role": name + "/field_batch",
                "verification": "hash from validated sequence manifest; size stat only"})

    def publish(self, output):
        self.add("export_manifest.json", encode({"schema_version": 1, "study_id": STUDY,
            "sources": list(self.sources.values()), "local_only": self.local_only, "text_transforms": self.transforms,
            "outputs": [{"path": name, "bytes": len(raw), "sha256": sha(raw)}
                        for name, raw in sorted(self.outputs.items())]}))
        require(all(not (output / name).exists() and not (output / name).is_symlink() for name in self.outputs),
                "Refusing to replace existing evidence")
        for name, raw in self.outputs.items():
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(raw)


def seal(root, output):
    require(output.resolve().is_relative_to(root.resolve()), "Evidence directory must be inside repository")
    export = json.loads((output / "export_manifest.json").read_bytes())
    files = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        require(not path.is_symlink(), "Symlink in evidence")
        raw = path.read_bytes()
        require(len(raw) <= LIMIT and path.suffix.lower() not in {".mph", ".npz", ".bin", ".zip"}, "Large artifact in Git delivery")
        if path.suffix.lower() in {".txt", ".json", ".xml", ".log", ".md", ".py"}:
            require(b"\r\n" not in raw, "Git text normalization would change frozen evidence: " + str(path))
        files.append({"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": sha(raw), "storage": "git"})
    report = (root / REPORT).read_bytes()
    require(len(report) <= LIMIT and b"\r\n" not in report, "Report exceeds lightweight limit or contains CRLF")
    files.append({"path": REPORT, "bytes": len(report), "sha256": sha(report), "storage": "git"})
    target = output / "delivery_manifest.json"
    require(not target.exists(), "Delivery already sealed")
    parent = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    value = {"schema_version": 1, "study_id": STUDY, "delivery_parent_commit": parent,
             "files": files, "local_only": export["sources"] + export["local_only"],
             "scope": "Git evidence identity and cross-file provenance; full native vectors and field values require local data"}
    with target.open("xb") as stream:
        stream.write(encode(value))


def copy_run_logs(exporter, directory):
    """Copy the study's run wrappers, excluding unrelated pytest raw streams."""
    for case in CASES:
        for stage in ("all", "analyze"):
            log_dir = directory / (case + "_" + stage)
            if stage == "analyze" and not log_dir.exists():
                continue
            for filename in ("execution.json", "stdout.txt", "stderr.txt"):
                copier = exporter.text_copy if filename.endswith(".txt") else exporter.copy
                copier(log_dir / filename, "logs/" + log_dir.name + "/" + filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path, default=ROOT / "scratch/pulse_direction_duration_20261109")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--seal", action="store_true")
    args = parser.parse_args()
    if args.seal:
        seal(ROOT, args.output.resolve())
        return
    exporter = Exporter(ROOT)
    for name in ("preregistered_plan.json", "freeze_record.json", "protected_before.json", "protected_after.json", "comparison.json"):
        exporter.copy(args.scratch / name, name)
    for name in ("readback.json", "source_identity.json"):
        exporter.copy(args.scratch / "checkpoint_inspection_v2" / name, "checkpoint_inspection/" + name)
    exporter.copy(ROOT / "docs/evidence/m3_pulse_amplitude_series_20261106/preregistered_plan.json", "reference_registry.json")
    plan = json.loads(exporter.outputs["preregistered_plan.json"])
    for name in CASES:
        exporter.case(name, plan["cases"][name])
    copy_run_logs(exporter, args.scratch / "logs")
    if any(name.endswith("/recovery/publication_recovery.json") for name in exporter.outputs):
        exporter.copy(args.scratch / "recover_publication.py", "recovery_source.py")
    exporter.publish(args.output.resolve())


if __name__ == "__main__":
    main()
