# M3 full-field sampling evidence

Entry report: [STAGE_M3_FIELD_SAMPLING_20261105.md](../../STAGE_M3_FIELD_SAMPLING_20261105.md).

- `native_sample.zip`: byte-preserved complete native run (18 files); extract into a new directory for raw TXT review.
- `native_archive_manifest.json`: original ZIP and entry SHA-256 hashes. `metadata.raw_files` hashes refer to these original TXT bytes.
- `grid_81x49/`, `grid_161x97/`: portable NPZ + metadata; readable actual COMSOL responses and export logs. Text display copies use LF.
- `sample_artifact_audit.json`: independent reconstruction of all 293,790 tensor entries and checks against actual EvalPoint results.
- `archive_artifact_audit.json`, `extracted_sample_artifact_audit.json`: ZIP verification and independent audit of its extracted contents.
- `inspection_audit.json`: source model hash and 646 historical files checked before/after.
- `negative_time_probe/`: actual rejection of non-saved time 180.1 s; raw response, processes and exit codes.
- `sampling_tests_final2.log/.xml`: final 59 sampling tests passed.
- `offline_regression_final2.log/.xml`: final 308 offline tests passed, 30 integration tests deselected to avoid rerunning CFD.
- `*_execution.json`: executed commands, elapsed times and actual process return codes.
- `sampling_tests_final.log`: preserved earlier Windows rename failure (58 passed, 1 failed); final2 reran unchanged code successfully.
- `sampling_tests.log`, `offline_regression.log`: earlier intermediate 57 / 306 passing results, retained with their timestamps and command records.
- `field_preview.png`: actual t=200 s, two sampling grids, units from metadata and shared limits per channel.
- `evidence_manifest.json`: hashes of repository display files after LF normalization, separate from native raw-byte hashes.

No new solver step log exists because this operation only evaluated/exported an existing M3 solution. Agreement with the same stored solution does not establish pointwise CFD accuracy.
