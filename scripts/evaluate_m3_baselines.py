"""Evaluate fixed historical-only baselines on the registered M3 window index."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hydro_disturb_bench import (
    data_windows,
    field_sampling,
    field_sequence,
    forecast_baselines,
    forecast_evaluation,
)
from hydro_disturb_bench.data_windows import load_window_index
from hydro_disturb_bench.forecast_evaluation import evaluate_baselines

INDEX_SHA256 = "63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5"
METHODS = ["persistence", "last_two_linear"]


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=True, indent=2, allow_nan=False)
        handle.write("\n")


def run_evaluation(config_path, output):
    config_path = Path(config_path).resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    config_bytes = config_path.read_bytes()
    config = json.loads(config_bytes)
    require(type(config.get("schema_version")) is int and config["schema_version"] == 1
            and config.get("operation") == "m3_forecast_baseline_smoke", "Unknown evaluation config")
    require(config.get("mode") == "pipeline_smoke" and config.get("split") == "train",
            "Registered M3 data support train pipeline_smoke only")
    require(config.get("methods") == METHODS, "Registered baseline methods changed")
    require(config["index"]["sha256"] == INDEX_SHA256, "Unregistered M3 index identity")
    relative = Path(config["index"]["path"])
    require(not relative.is_absolute() and ".." not in relative.parts, "Index path must be repository-relative")
    index_path = (ROOT / relative).resolve()
    require(index_path.is_relative_to(ROOT), "Index path escapes repository")
    index = load_window_index(index_path, expected_sha256=INDEX_SHA256)
    require(index["spec"] == config["expected_window_spec"], "Window specification changed")
    require(all(index["counts"][key] == value for key, value in config["expected_counts"].items()),
            "Window index counts changed")
    output = output.resolve()
    protected = [config_path.parent, index_path.parent,
                 *(Path(item["sequence_dir"]).resolve() for item in index["trajectories"])]
    require(not any(output.is_relative_to(path) for path in protected),
            "Output must not be inside an input sequence, config or index directory")
    output.mkdir(parents=True, exist_ok=False)
    sources = [Path(__file__).resolve(), config_path, index_path,
               *(Path(module.__file__).resolve() for module in (
                   forecast_baselines, forecast_evaluation, data_windows,
                   field_sequence, field_sampling))]
    before = {}
    started = time.time()
    try:
        for path in sources:
            before[str(path)] = file_sha256(path)
        require(before[str(config_path)] == hashlib.sha256(config_bytes).hexdigest(),
                "Config changed during admission")
        require(before[str(index_path)] == INDEX_SHA256, "Index changed during admission")
        report = evaluate_baselines(index, split=config["split"], mode=config["mode"],
                                    methods=tuple(config["methods"]))
        after = {str(path): file_sha256(path) for path in sources}
        require(before == after, "Evaluation code, config or input index changed during execution")
        report["registered_input"] = {"index_sha256": INDEX_SHA256,
                                      "config_sha256": before[str(config_path)]}
        write_json(output / "report.json", report)
        write_json(output / "execution.json", {
            "status": "PASS", "elapsed_seconds": time.time() - started,
            "source_sha256_before": before, "source_sha256_after": after,
            "source_files_unchanged": True, "report_sha256": file_sha256(output / "report.json"),
            "comsol_started": False, "fitted_model": False, "prediction_tensor_files": 0,
        })
        return report
    except Exception as exc:
        for name in ("report.json", "execution.json"):
            (output / name).unlink(missing_ok=True)
        write_json(output / "failure.json", {
            "status": "FAIL", "error": str(exc), "traceback": traceback.format_exc(),
            "source_sha256_before": before, "comsol_started": False,
        })
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs/m3_forecast_baseline.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    report = run_evaluation(args.config, args.output)
    print(json.dumps({"status": report["status"], "mode": report["mode"],
                      "split": report["split"], "output": str(args.output)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
