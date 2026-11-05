"""Independent read-only audit of COMSOL raw TXT and NPZ; no production helpers."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import re
import sys
import numpy as np

OUT = Path(__file__).with_name('sample_artifact_audit.json')
EXPECTED_MODEL = '15e841fc4240d93db877932c1b371646426f1229bdb40d0335b0ed7609d145b4'
CHANNELS = ['u', 'v', 'p']
UNITS = ['m/s', 'm/s', 'Pa']


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def close(left, right, atol=1e-12):
    a, b = np.asarray(left), np.asarray(right)
    assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all()
    assert np.allclose(a, b, atol=atol, rtol=0), (a, b)


def raw_tensor(path, x, y, expected_t):
    header = None
    body_lines = []
    length_unit = None
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip():
            continue
        if line.startswith('%'):
            body = line[1:].strip()
            if body.lower().startswith('length unit:'):
                length_unit = body.split(':', 1)[1].strip()
            if re.match(r'^x\s+y\s+', body):
                assert header is None
                header = re.findall(r'([^\s()@]+)\s+\(([^()]*)\)\s+@\s+t=([^\s]+)', re.sub(r'^x\s+y\s+', '', body))
            continue
        body_lines.append(line)
    assert length_unit == 'm' and header is not None and len(header) == 3
    assert sorted(item[0] for item in header) == sorted(CHANNELS)
    for expression, unit, time in header:
        assert unit == UNITS[CHANNELS.index(expression)]
        close(float(time), expected_t)
    rows = np.loadtxt(body_lines)
    assert rows.shape == (len(x) * len(y), 5) and np.isfinite(rows[:, :2]).all()
    assert not np.isinf(rows[:, 2:]).any()
    arrays = np.full((3, len(y), len(x)), np.nan)
    seen = np.zeros((len(y), len(x)), dtype=bool)
    reorder = [2 + [h[0] for h in header].index(channel) for channel in CHANNELS]
    for row in rows:
        ix, iy = int(np.abs(x - row[0]).argmin()), int(np.abs(y - row[1]).argmin())
        close([x[ix], y[iy]], row[:2])
        assert not seen[iy, ix]
        seen[iy, ix] = True
        arrays[:, iy, ix] = row[reorder]
    assert seen.all()
    return arrays


def audit(directory, model_override=None, output=OUT):
    report = read(directory / 'sample_report.json')
    assert report['status'] == 'PASS' and report['source_unchanged'] is True and report['solver_called'] is False
    assert report['source_sha256'].lower() == EXPECTED_MODEL
    model = Path(report['source_model'])
    physical_model = model_override if model_override is not None else model
    model_bytes_checked = physical_model.is_file()
    if model_override is not None:
        assert model_bytes_checked, f'Model override does not exist: {physical_model}'
    if model_bytes_checked:
        assert sha(physical_model) == EXPECTED_MODEL
    results, tensors = [], []
    for item in report['grids']:
        grid = directory / item['path']
        meta, response = read(grid / 'metadata.json'), read(grid / 'bridge_response.json')
        assert meta['source_sha256'].lower() == EXPECTED_MODEL and Path(meta['source_model']).resolve() == model.resolve()
        assert Path(response['model_path']).resolve() == model.resolve()
        assert meta['model_tag'] == response['model_tag'] == 'Model'
        assert meta['channel_order'] == CHANNELS and meta['units'] == UNITS
        assert meta['dataset'] == response['dataset']
        assert response['dataset'] == {'tag': 'dset1', 'type': 'Solution', 'solution_tag': 'sol1'}
        assert response['geometry']['length_unit'] == 'm'
        close(response['geometry']['bounds'], [0., .4, 0., .24])
        circle = response['geometry']['circle']
        assert circle['feature_tag'] and circle['unit'] == 'm' and circle['source']
        close(circle['center'], [.1, .12]); close(circle['radius'], .005)
        binary = grid / 'field_sample.npz'
        assert sha(binary) == meta['npz_sha256'].lower() == item['npz_sha256'].lower()
        with np.load(binary, allow_pickle=False) as archive:
            assert set(archive.files) == {'values', 'mask', 'cylinder_mask', 'invalid_mask', 'x', 'y', 'times'}
            data = {key: archive[key] for key in archive.files}
        values, x, y, times = (data[key] for key in ('values', 'x', 'y', 'times'))
        assert values.dtype == np.float64 and values.shape == (len(times), 3, len(y), len(x)) == tuple(meta['shape'])
        close(x, np.linspace(0., .4, len(x))); close(y, np.linspace(0., .24, len(y)))
        close(times, report['requested_times']); close(times, meta['requested_times']); close(times, meta['actual_times'])
        close(times, response['requested_times']); close(times, response['actual_times'])
        assert np.all(np.diff(x) > 0) and np.all(np.diff(y) > 0) and np.all(np.diff(times) > 0)
        saved = np.asarray(response['saved_times'])
        assert len(saved) == 1001 and np.isfinite(saved).all() and np.all(np.diff(saved) > 0)
        close(saved, np.round(np.arange(1001) * .2, 10))
        X, Y = np.meshgrid(x, y, indexing='xy')
        cylinder = np.hypot(X - .1, Y - .12) <= .005 + 1e-12
        assert np.array_equal(cylinder, data['cylinder_mask'])
        assert len(response['exports']) == len(times)
        raw = []
        for ti, export in enumerate(response['exports']):
            assert export['export_type'] == 'Data' and export['export_tag']
            assert export['dataset_tag'] == 'dset1' and export['expressions'] == CHANNELS and export['units'] == UNITS
            close(export['gridx'], x); close(export['gridy'], y)
            assert export['innerinput'] == 'manual'
            assert saved[export['solnum'] - 1] == export['actual_t']
            close([export['target_t'], export['actual_t']], [times[ti], times[ti]])
            filename = export['filename']
            assert Path(filename).name == filename and sha(grid / filename) == meta['raw_files'][filename].lower()
            raw.append(raw_tensor(grid / filename, x, y, times[ti]))
        raw = np.stack(raw)
        invalid = ~np.isfinite(raw).all(axis=1)
        assert not (invalid & ~cylinder[None]).any()
        assert np.array_equal(data['invalid_mask'], invalid)
        mask = cylinder[None] | invalid
        assert np.array_equal(data['mask'], mask)
        reconstructed = np.where(mask[:, None], np.nan, raw)
        assert np.array_equal(values, reconstructed, equal_nan=True)
        node = response['direct_evaluation_node']
        assert node['type'] == 'EvalPoint' and node['point_dataset_type'] == 'CutPoint2D' and node['source_dataset_tag'] == 'dset1'
        assert node['expressions'] == ['u', 'v', 'p', 'x', 'y', 't'] and node['units'] == ['m/s', 'm/s', 'Pa', 'm', 'm', 's']
        assert node['innerinput'] == 'manual' and node['solrepresentation'] == 'solnum'
        check_counts = [set() for _ in times]
        errors = []
        for check in response['direct_evaluations']:
            ti, ix, iy = check['time_index'], check['grid_ix'], check['grid_iy']
            assert not mask[ti, iy, ix]
            assert check['numerical_tag'] == node['tag'] and check['point_dataset_tag'] == node['dataset_tag']
            close([check['requested_x'], check['requested_y'], check['x'], check['y']], [x[ix], y[iy], x[ix], y[iy]])
            close([check['target_t'], check['actual_t']], [times[ti], times[ti]])
            assert (ix, iy) not in check_counts[ti]
            check_counts[ti].add((ix, iy))
            direct = np.asarray(check['values'])
            assert np.isfinite(direct).all() and np.allclose(values[ti, :, iy, ix], direct, rtol=1e-9, atol=1e-10)
            errors.append(np.abs(values[ti, :, iy, ix] - direct))
        assert all(len(points) >= 3 for points in check_counts)
        results.append({'path': item['path'], 'shape': list(values.shape), 'times': times.tolist(),
                        'raw_txt_file_count': len(response['exports']),
                        'raw_rows_per_file': len(x) * len(y),
                        'raw_rows_total': len(times) * len(x) * len(y),
                        'tensor_entries_compared_including_masked_nan': int(values.size),
                        'finite_fluid_tensor_entries_compared': int(np.isfinite(values).sum()),
                        'masked_nan_tensor_entries_compared': int(np.isnan(values).sum()),
                        'bounds_m': [float(x[0]), float(x[-1]), float(y[0]), float(y[-1])],
                        'units': UNITS, 'cylinder_points': int(cylinder.sum()),
                        'raw_invalid_points_per_time': invalid.sum(axis=(1, 2)).tolist(),
                        'fluid_entries_finite': bool(np.isfinite(values[np.broadcast_to(~mask[:, None], values.shape)]).all()),
                        'npz_raw_reconstruction_identical': True,
                        'direct_checks_per_time': [len(points) for points in check_counts],
                        'max_direct_absolute_error_per_channel': np.max(errors, axis=0).tolist(),
                        'npz_sha256': sha(binary)})
        tensors.append(data)
    assert len(tensors) == 2
    coarse, fine = sorted(tensors, key=lambda value: len(value['x']) * len(value['y']))
    close(coarse['x'], fine['x'][::2]); close(coarse['y'], fine['y'][::2]); close(coarse['times'], fine['times'])
    subset = fine['values'][..., ::2, ::2]
    assert np.array_equal(np.isnan(coarse['values']), np.isnan(subset))
    delta = np.abs(coarse['values'] - subset)
    assert np.allclose(coarse['values'], subset, rtol=1e-9, atol=1e-10, equal_nan=True)
    result = {'status': 'PASS', 'checked_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Independent raw TXT / NPZ / actual COMSOL response audit; no production sampling or parsing helpers imported, no COMSOL process started.',
              'source_model_sha256': EXPECTED_MODEL, 'sample_directory': str(directory.resolve()),
              'physical_model_bytes_checked': model_bytes_checked,
              'raw_txt_files_total': sum(item['raw_txt_file_count'] for item in results),
              'raw_rows_total': sum(item['raw_rows_total'] for item in results),
              'tensor_entries_compared_including_masked_nan': sum(item['tensor_entries_compared_including_masked_nan'] for item in results),
              'finite_fluid_tensor_entries_compared': sum(item['finite_fluid_tensor_entries_compared'] for item in results),
              'direct_point_checks_total': sum(sum(item['direct_checks_per_time']) for item in results),
              'direct_channel_value_checks_total': 3 * sum(sum(item['direct_checks_per_time']) for item in results),
              'grids': results, 'nested_grid_shared_point_max_abs_difference_per_channel': np.nanmax(delta, axis=(0, 2, 3)).tolist(),
              'nested_grid_shared_spatial_points_per_time': len(coarse['x']) * len(coarse['y']),
              'nested_grid_compared_entries_including_masked_nan': int(coarse['values'].size),
              'nested_grid_finite_fluid_entries_compared': int(np.isfinite(coarse['values']).sum()),
              'nested_grid_shared_values_match': True,
              'limitations': ['Checks sampling provenance and numerical agreement at the same saved solution; not a new CFD accuracy or pointwise error estimate.']}
    if not model_bytes_checked:
        result['limitations'].append('Original model bytes are unavailable here; model SHA is checked for consistency across archived evidence only. Use --model-path to additionally verify model bytes.')
    output.write_bytes((json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8'))
    print(json.dumps(result, indent=2, ensure_ascii=True))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--sample-dir', required=True, type=Path, help='Published sample directory, including sample_report.json')
    parser.add_argument('--model-path', type=Path, help='Optional relocated original M3 model; bytes are SHA-256 verified if present')
    parser.add_argument('--output', type=Path, default=OUT, help='Audit JSON destination')
    args = parser.parse_args()
    audit(args.sample_dir, args.model_path, args.output)
