# M3 continuous baseline: 151 frames

[Report and reproduction commands](../../STAGE_M3_FIELD_SEQUENCE_20261105.md).

The full tensor has shape `(151,3,97,161)`, channels `[u,v,p]`, float64, saved times 170–200 s at 0.2 s. Five batches contain 32/32/32/32/23 consecutive frames. Masked values are NaN. This is sampling of an existing M3 solution, with no CFD solve or native-mesh change.

Download/extract **all six native ZIP files into one new directory**:

| ZIP | Content |
|---|---|
| [native_index.zip](native_index.zip) | Original sequence manifest, report, progress and model inspection |
| [native_batch_0000.zip](native_batch_0000.zip) | Frames 0–31, 170.0–176.2 s |
| [native_batch_0001.zip](native_batch_0001.zip) | Frames 32–63, 176.4–182.6 s |
| [native_batch_0002.zip](native_batch_0002.zip) | Frames 64–95, 182.8–189.0 s |
| [native_batch_0003.zip](native_batch_0003.zip) | Frames 96–127, 189.2–195.4 s |
| [native_batch_0004.zip](native_batch_0004.zip) | Frames 128–150, 195.6–200.0 s |

Each data ZIP contains native TXT, NPZ, metadata and COMSOL response. [native_archive_manifest.json](native_archive_manifest.json) binds all six ZIP hashes and all 170 original entry hashes. The original files total 346,828,436 bytes; the ZIPs total 132,710,355 bytes.

The separate JSON/log files in this repository are readable LF-normalized display copies. The sequence loader validates metadata/response byte hashes: use the fully extracted **native** directory, not a mixture of these display copies and native files. `evidence_manifest.json` hashes repository display files separately. Existing Git attributes remain unchanged.

`execution_logs.zip` separately preserves original execution logs, XML and command records before display normalization; it is not a sequence data package. `execution_logs_manifest.json` verifies its entries. The sampling execution record's `stdout_sha256` refers to the original CRLF stdout in this ZIP; `display_stdout_sha256` refers to the readable LF copy.

Key records:

- `sequence_report.json`, `sequence_manifest.json`: requested/actual times, contiguous solution numbers, batch coverage, shape and hashes.
- `batch_0000/` through `batch_0004/bridge_response.json`: actual units, Dataset–Solution links, saved solution numbers, coordinate arrays and direct evaluation. Adjacent `comsol_sampling.log` files preserve result-evaluation/export output, not new solver steps.
- `sequence_artifact_audit.json`: independent reconstruction of all 7,074,501 entries; 453 direct points; five complete overlapping frames.
- `archive_sequence_audit_final.json`, `archive_sequence_audit_final_sequence.json`: native archive byte checks, independent audit after extraction and production-loader readback.
- `history_audit.json`, `history_audit_final.json`: 712 protected historical files unchanged.
- `sampling_regression.log/.xml`: 91 sampling tests passed (32 new + 59 existing).
- `offline_regression.log/.xml`: 340 passed, 30 integration tests deselected to avoid rerunning CFD.
- `*_execution.json`: actual commands, exit codes and timing.
- `archive_sequence_audit.log` / `_stderr.log`: preserved first audit attempt, which completed independent checks but failed to import the uninstalled project package; final attempt uses repository source discovery and passes.

`archive_sequence_audit.py` validates and extracts all packages into a new directory, independently audits the sequence against the previous five-frame sample, then calls the production loader and compares all seven arrays. It never starts COMSOL. See the report for the exact command. Without the original `.mph`, independent audits explicitly report `physical_model_bytes_checked=false` instead of claiming to verify model bytes.
