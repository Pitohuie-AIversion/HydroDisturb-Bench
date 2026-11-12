from pathlib import Path
import hashlib
import json
import os
import platform
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[2]
work = Path(__file__).resolve().parent
name = sys.argv[1]
logdir = work / name
logdir.mkdir(parents=True, exist_ok=False)
commands = {
    'targeted': ['-m', 'pytest', '-q', 'tests/test_forecast_baselines.py', 'tests/test_forecast_evaluation.py', 'tests/test_forecast_audit.py', 'tests/test_evaluate_m3_baselines.py', '--junitxml=' + str(logdir / 'junit.xml')],
    'regression': ['-m', 'pytest', '-q', '--strict-markers', '-m', 'not integration', '--junitxml=' + str(logdir / 'junit.xml')],
    'evaluation': ['scripts/evaluate_m3_baselines.py', '--output', str(work / 'evaluation_output')],
    'audit': ['scripts/audit_forecast_baselines.py', '--index', 'docs/evidence/data_usage_20261106/index.json', '--index-sha256', '63004756d70b381a51a63106fd81035dbd6f5594ae3865d66ef1daf9543575b5', '--report', str(work / 'evaluation_output/report.json'), '--output', str(work / 'independent_audit.json')],
}
command = [sys.executable, '-X', 'utf8', *commands[name]]
sources = sorted({p for sub in ('src', 'scripts', 'tests') for p in (root / sub).rglob('*.py')} | {root / 'pyproject.toml', root / 'uv.lock', root / 'configs/m3_forecast_baseline.json', Path(__file__)})
def hashes():
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
before = hashes()
start = time.time()
with (logdir / 'stdout.txt').open('xb') as stdout, (logdir / 'stderr.txt').open('xb') as stderr:
    result = subprocess.run(command, cwd=root, stdout=stdout, stderr=stderr, env=dict(os.environ, PYTHONIOENCODING='utf-8'))
after = hashes()
record = dict(command=command, returncode=result.returncode, python=platform.python_version(), started_unix=start, finished_unix=time.time(), elapsed_seconds=time.time()-start, source_sha256_before=before, source_sha256_after=after, sources_unchanged=before == after)
(logdir / 'execution.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
print(json.dumps({k: record[k] for k in ('returncode', 'elapsed_seconds', 'sources_unchanged')}), flush=True)
print((logdir / 'stdout.txt').read_text(encoding='utf-8')[-3000:], flush=True)
print((logdir / 'stderr.txt').read_text(encoding='utf-8')[-1000:], flush=True)
raise SystemExit(result.returncode if before == after else 99)
