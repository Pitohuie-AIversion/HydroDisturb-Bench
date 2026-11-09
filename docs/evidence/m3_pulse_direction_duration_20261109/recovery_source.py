"""One-off recovery of an already complete, validated sample after WinError 5.

No COMSOL call and no export. Retains both original failure records, verifies
every staged file and formal loader output before/after a no-overwrite rename.
"""
from pathlib import Path
import datetime
import json
import os
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from hydro_disturb_bench.comsol_java import bridge
from hydro_disturb_bench.experiments.branch_execution import completed_stage
from hydro_disturb_bench.experiments.evidence_io import file_sha256 as sha, read_json as read, require, write_json as write
from hydro_disturb_bench.field_sequence import load_full_field_sequence

HERE = Path(__file__).resolve().parent
key = sys.argv[1]
plan = read(HERE / 'preregistered_plan.json')
case = plan['cases'][key]
run = (ROOT / case['run_dir']).resolve()
require(run.parent == (ROOT / 'runs').resolve(), 'Unexpected run location')
_, model, solved = completed_stage(run / 'solve')
directory = run / 'sample_solve'
target = directory / 'fields'
require(not (directory / 'execution.json').exists() and not target.exists() and not target.is_symlink(),
        'Recovery cannot replace completed work')
failed = read(directory / 'failure.json')
require(failed['status'] == 'FAIL' and '[WinError 5]' in failed['error']
        and 'os.rename(stage, output_dir)' in failed['traceback'], 'Not the known publication failure')
stages = list(directory.glob('.staging_field_sequence_*'))
require(len(stages) == 1, 'Expected exactly one preserved staging directory')
stage = stages[0].resolve()
require(stage.parent == directory.resolve() and not stages[0].is_symlink(), 'Staging escaped run directory')
stage_failure = read(stage / 'failure.json')
require(stage_failure['source_unchanged'] and Path(stage_failure['intended_output']).resolve() == target.resolve(),
        'Failure does not bind this output')
require(sha(model) == solved['output_sha256'] == failed['source_sha256_after'], 'Solved model changed')
report = read(stage / 'sequence_report.json')
require(report['status'] == 'PASS' and report['readback_passed'] and report['solver_called'] is False,
        'Export was not fully validated before publication')
before = load_full_field_sequence(stage)
require(before['values'].shape == (101, 3, 97, 161), 'Unexpected sample contract')

def inventory(path):
    rows = []
    for file in sorted(path.rglob('*')):
        require(not file.is_symlink(), 'Symlink inside sample')
        if file.is_file():
            rows.append({'path': file.relative_to(path).as_posix(), 'bytes': file.stat().st_size, 'sha256': sha(file)})
    return rows

files = inventory(stage)
started = time.time()
write(directory / 'publication_retry_admission.json', {
    'status': 'ADMITTED', 'started_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'script_sha256': sha(Path(__file__)), 'source_model_sha256': sha(model),
    'original_failure_sha256': sha(directory / 'failure.json'), 'staging_directory': str(stage),
    'target_directory': str(target), 'staged_files': files,
    'scope': 'Only retry publication of the complete validated sample. No COMSOL, export or CFD solve.'})
with bridge.file_publish_lock(directory / '.locks/fields.lock'):
    require(not target.exists() and not target.is_symlink(), 'Output appeared before publication')
    os.rename(stage, target)
after = load_full_field_sequence(target)
require(inventory(target) == files, 'Published bytes differ from the preserved stage')
for field in ('values', 'x', 'y', 'times', 'mask', 'invalid_mask', 'cylinder_mask'):
    require(np.array_equal(before[field], after[field], equal_nan=True), 'Published arrays differ: ' + field)
require(sha(model) == solved['output_sha256'], 'Model changed during recovery')
implementation = failed['implementation']
require(implementation['all_source_files_unchanged'] and all(
    sha(Path(path)) == row['sha256_before'] == row['sha256_after']
    for path, row in implementation['source_files'].items()), 'Original export source code changed')
write(directory / 'publication_recovery.json', {
    'status': 'PASS', 'completion_method': 'Validated publication retry after original WinError 5',
    'time_solver_executed': False, 'export_repeated': False, 'source_model_unchanged': True,
    'original_failure_sha256': sha(directory / 'failure.json'),
    'original_stage_failure_sha256': sha(target / 'failure.json'),
    'admission_sha256': sha(directory / 'publication_retry_admission.json'),
    'script_sha256': sha(Path(__file__)), 'staged_files': files, 'all_file_bytes_unchanged': True,
    'array_fields_rechecked': ['values', 'x', 'y', 'times', 'mask', 'invalid_mask', 'cylinder_mask'],
    'elapsed_seconds': time.time() - started})
write(directory / 'execution.json', {
    'status': 'COMPLETE', 'source_model': str(model), 'source_sha256': sha(model),
    'manifest_sha256': sha(target / 'sequence_manifest.json'), 'shape': report['shape'],
    'implementation': implementation, 'original_failed_export_seconds': failed['elapsed_seconds'],
    'elapsed_seconds': failed['elapsed_seconds'] + time.time() - started,
    'completion_method': 'Validated publication recovery; original failure retained',
    'publication_recovery_sha256': sha(directory / 'publication_recovery.json')})
print(json.dumps({'status': 'PASS', 'case': key, 'files_unchanged': len(files), 'shape': report['shape']}))
