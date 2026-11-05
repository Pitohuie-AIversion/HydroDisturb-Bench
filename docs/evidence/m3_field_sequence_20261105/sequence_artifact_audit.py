"""Independent raw TXT/NPZ sequence audit; imports no production helpers."""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import re
import sys
import numpy as np

MODEL_SHA = '15e841fc4240d93db877932c1b371646426f1229bdb40d0335b0ed7609d145b4'
CHANNELS = ['u', 'v', 'p']
UNITS = ['m/s', 'm/s', 'Pa']
EXPECTED_DATASET = {'tag': 'dset1', 'type': 'Solution', 'solution_tag': 'sol1'}
ATOL = 1e-12


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def close(a, b, atol=ATOL):
    a, b = np.asarray(a), np.asarray(b)
    assert a.shape == b.shape and np.isfinite(a).all() and np.isfinite(b).all()
    assert np.allclose(a, b, rtol=0, atol=atol)


def npz(path):
    with np.load(path, allow_pickle=False) as archive:
        assert set(archive.files) == {'values', 'mask', 'cylinder_mask', 'invalid_mask', 'x', 'y', 'times'}
        return {name: archive[name] for name in archive.files}


def raw_tensor(path, x, y, time):
    header, length_unit, lines = None, None, []
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
        lines.append(line)
    assert length_unit == 'm' and header is not None and len(header) == 3
    assert sorted(item[0] for item in header) == sorted(CHANNELS)
    for expr, unit, t in header:
        assert unit == UNITS[CHANNELS.index(expr)]
        close(float(t), time)
    rows = np.loadtxt(lines)
    assert rows.shape == (len(x) * len(y), 5)
    assert np.isfinite(rows[:, :2]).all() and not np.isinf(rows[:, 2:]).any()
    ix = np.rint((rows[:, 0] - x[0]) / (x[1] - x[0])).astype(int)
    iy = np.rint((rows[:, 1] - y[0]) / (y[1] - y[0])).astype(int)
    assert np.all((ix >= 0) & (ix < len(x))) and np.all((iy >= 0) & (iy < len(y)))
    close(x[ix], rows[:, 0]); close(y[iy], rows[:, 1])
    flat = iy * len(x) + ix
    assert np.array_equal(np.sort(flat), np.arange(len(x) * len(y)))
    order = [2 + [item[0] for item in header].index(channel) for channel in CHANNELS]
    result = np.empty((3, len(y), len(x)))
    result[:, iy, ix] = rows[:, order].T
    return result


def audit(directory, reference_dir, output, model_override=None):
    manifest, report = read(directory / 'sequence_manifest.json'), read(directory / 'sequence_report.json')
    assert manifest['operation'] == 'export_full_field_sequence' and manifest['schema_version'] == 1
    assert manifest['status'] == report['status'] == 'PASS'
    assert manifest['solver_called'] is False and manifest['source_unchanged'] is True
    assert manifest['source_sha256'].lower() == MODEL_SHA
    assert manifest['shape'] == [151, 3, 97, 161] and manifest['grid_shape'] == [161, 97]
    assert manifest['tensor_layout'] == '(T, 3, Ny, Nx)' and manifest['channel_order'] == CHANNELS and manifest['units'] == UNITS
    requested = np.arange(850, 1001, dtype=float) / 5
    close(manifest['requested_times'], requested); close(manifest['actual_times'], requested)
    assert manifest['time_step_s'] == .2 and manifest['solution_numbers'] == list(range(851, 1002))
    for key, value in manifest.items():
        assert report[key] == value, key
    model = model_override if model_override is not None else Path(manifest['source_model'])
    model_checked = model.is_file()
    if model_override is not None:
        assert model_checked, 'Explicit model path is missing'
    if model_checked:
        assert sha(model) == MODEL_SHA
    x, y = np.linspace(0., .4, 161), np.linspace(0., .24, 97)
    X, Y = np.meshgrid(x, y, indexing='xy')
    cylinder = np.hypot(X - .1, Y - .12) <= .005 + ATOL
    cursor, batches, blocks, invalid_blocks, all_times, direct_errors = 0, [], [], [], [], []
    first_saved, first_geometry = None, None
    assert len(manifest['batches']) == 5
    names = [entry['path'] for entry in manifest['batches']]
    assert len(set(names)) == 5 and {p.name for p in directory.glob('batch_*') if p.is_dir()} == set(names)
    for entry in manifest['batches']:
        name = entry['path']
        assert Path(name).name == name and '\\' not in name and '/' not in name
        start, stop, count = (entry[key] for key in ('start_index', 'stop_index', 'count'))
        assert start == cursor and stop == start + count and 1 <= count <= 32
        assert count == min(32, 151 - start)
        batch_dir = directory / name
        for filename, hashkey in [('field_sample.npz', 'npz_sha256'), ('metadata.json', 'metadata_sha256'), ('bridge_response.json', 'response_sha256')]:
            assert sha(batch_dir / filename) == entry[hashkey].lower()
        meta, response = read(batch_dir / 'metadata.json'), read(batch_dir / 'bridge_response.json')
        assert meta['source_sha256'].lower() == MODEL_SHA and meta['source_model'] == manifest['source_model'] == response['model_path']
        assert meta['model_tag'] == response['model_tag'] == 'Model'
        assert meta['dataset'] == response['dataset'] == EXPECTED_DATASET
        assert meta['channel_order'] == CHANNELS and meta['units'] == UNITS
        assert meta['geometry'] == response['geometry']
        geometry = response['geometry']
        assert geometry['length_unit'] == 'm'
        close(geometry['bounds'], [0., .4, 0., .24])
        circle = geometry['circle']
        assert circle['unit'] == 'm' and circle['feature_tag'] and circle['source']
        close(circle['center'], [.1, .12]); close(circle['radius'], .005)
        assert response['status'] == 'SUCCESS' and response['batch_mode'] is True
        assert response['operation'] == 'export_full_field_sample'
        saved = np.asarray(response['saved_times'])
        close(saved, np.arange(1001, dtype=float) / 5)
        assert np.all(np.diff(saved) > 0)
        if first_saved is None:
            first_saved, first_geometry = saved, geometry
        else:
            assert np.array_equal(saved, first_saved) and geometry == first_geometry
        data = npz(batch_dir / 'field_sample.npz')
        assert sha(batch_dir / 'field_sample.npz') == meta['npz_sha256'].lower()
        values, times = data['values'], data['times']
        assert values.dtype == np.float64 and values.shape == (count, 3, 97, 161) == tuple(meta['shape'])
        close(data['x'], x); close(data['y'], y)
        close(meta['coordinates']['x'], x); close(meta['coordinates']['y'], y)
        assert meta['coordinates']['unit'] == 'm'
        close(times, requested[start:stop])
        for container in (meta, response):
            close(container['requested_times'], times); close(container['actual_times'], times)
        assert data['cylinder_mask'].dtype == bool and np.array_equal(data['cylinder_mask'], cylinder)
        assert meta['unit_evidence'] == response['exports'] and len(response['exports']) == count
        assert {p.name for p in batch_dir.glob('*.txt')} == set(meta['raw_files'])
        raw = []
        for ti, export in enumerate(response['exports']):
            assert export['export_type'] == 'Data' and export['export_tag']
            assert export['dataset_tag'] == 'dset1' and export['expressions'] == CHANNELS and export['units'] == UNITS
            assert export['innerinput'] == 'manual' and export['solnum'] == 851 + start + ti
            assert export['actual_t'] == saved[export['solnum'] - 1]
            close([export['target_t'], export['actual_t']], [times[ti], times[ti]])
            close(export['gridx'], x); close(export['gridy'], y)
            filename = export['filename']
            assert Path(filename).name == filename
            assert sha(batch_dir / filename) == meta['raw_files'][filename].lower()
            raw.append(raw_tensor(batch_dir / filename, x, y, times[ti]))
        raw = np.stack(raw)
        invalid = ~np.isfinite(raw).all(axis=1)
        mask = invalid | cylinder[None]
        assert not (invalid & ~cylinder[None]).any()
        for key, expected in [('invalid_mask', invalid), ('mask', mask)]:
            assert data[key].dtype == bool and np.array_equal(data[key], expected)
        assert np.array_equal(values, np.where(mask[:, None], np.nan, raw), equal_nan=True)
        node = response['direct_evaluation_node']
        assert node == meta['direct_evaluation_node']
        assert node['type'] == 'EvalPoint' and node['point_dataset_type'] == 'CutPoint2D'
        assert node['source_dataset_tag'] == 'dset1'
        assert node['expressions'] == ['u', 'v', 'p', 'x', 'y', 't']
        assert node['units'] == ['m/s', 'm/s', 'Pa', 'm', 'm', 's']
        assert node['innerinput'] == 'manual' and node['solrepresentation'] == 'solnum'
        seen = [set() for _ in times]
        batch_errors = []
        for check in response['direct_evaluations']:
            ti, ix, iy = (check[key] for key in ('time_index', 'grid_ix', 'grid_iy'))
            assert 0 <= ti < count and not mask[ti, iy, ix]
            assert check['numerical_tag'] == node['tag'] and check['point_dataset_tag'] == node['dataset_tag']
            close([check['requested_x'], check['requested_y'], check['x'], check['y']], [x[ix], y[iy], x[ix], y[iy]])
            close([check['target_t'], check['actual_t']], [times[ti], times[ti]])
            assert (ix, iy) not in seen[ti]
            seen[ti].add((ix, iy))
            direct = np.asarray(check['values'])
            assert direct.shape == (3,) and np.isfinite(direct).all()
            assert np.allclose(values[ti, :, iy, ix], direct, rtol=1e-9, atol=1e-10)
            batch_errors.append(np.abs(values[ti, :, iy, ix] - direct))
        assert all(len(points) == 3 for points in seen)
        direct_errors.extend(batch_errors)
        batches.append({'path': name, 'start_index': start, 'stop_index': stop,
                        'frame_count': count, 'raw_files_checked': count, 'raw_rows_checked': count * len(x) * len(y),
                        'tensor_entries_compared': int(values.size), 'finite_fluid_entries': int(np.isfinite(values).sum()),
                        'raw_invalid_points_per_frame': invalid.sum(axis=(1, 2)).tolist(),
                        'direct_point_checks': len(batch_errors), 'max_direct_absolute_errors': np.max(batch_errors, axis=0).tolist(),
                        'npz_raw_reconstruction_identical': True})
        blocks.append(values); invalid_blocks.append(invalid); all_times.extend(times)
        cursor = stop
    assert cursor == 151
    values, invalid = np.concatenate(blocks), np.concatenate(invalid_blocks)
    times = np.asarray(all_times)
    close(times, requested); close(np.diff(times), np.full(150, .2))
    assert np.all(invalid == invalid[0])
    reference_meta = read(reference_dir / 'metadata.json')
    reference = npz(reference_dir / 'field_sample.npz')
    assert sha(reference_dir / 'field_sample.npz') == reference_meta['npz_sha256'].lower()
    assert reference_meta['source_sha256'].lower() == MODEL_SHA
    assert reference_meta['dataset'] == EXPECTED_DATASET and reference_meta['geometry'] == first_geometry
    close(reference['x'], x); close(reference['y'], y)
    close(reference['times'], [180., 185., 190., 195., 200.])
    indices = [int(np.flatnonzero(np.abs(times - time) <= ATOL)[0]) for time in reference['times']]
    assert indices == [50, 75, 100, 125, 150]
    assert np.array_equal(reference['cylinder_mask'], cylinder)
    assert np.array_equal(reference['invalid_mask'], invalid[indices])
    assert np.array_equal(reference['mask'], invalid[indices] | cylinder[None])
    assert np.allclose(values[indices], reference['values'], rtol=1e-9, atol=1e-10, equal_nan=True)
    overlap_errors = np.nanmax(np.abs(values[indices] - reference['values']), axis=(0, 2, 3))
    direct_max = np.max(direct_errors, axis=0)
    close(report['max_direct_absolute_errors'], direct_max, atol=0)
    assert report['direct_point_checks'] == len(direct_errors) == 453
    assert report['fluid_entries'] == int(np.isfinite(values).sum())
    assert report['masked_entries'] == int(np.isnan(values).sum())
    assert manifest['overlap_check']['status'] == 'PASS' and manifest['overlap_check']['frames_checked'] == 5
    close(manifest['overlap_check']['max_absolute_errors'], overlap_errors, atol=0)
    result = {'status': 'PASS', 'checked_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Independent raw TXT/NPZ/response audit; no production sampling/parser imports, no COMSOL process.',
              'sequence_directory': str(directory.resolve()), 'source_model_sha256': MODEL_SHA,
              'physical_model_bytes_checked': model_checked, 'shape': list(values.shape),
              'frame_count': len(times), 'first_time_s': float(times[0]), 'last_time_s': float(times[-1]),
              'requested_step_s': .2, 'solnum_first_last': [851, 1001],
              'time_order_unique_contiguous': True, 'batches': batches,
              'raw_txt_files_checked': sum(item['raw_files_checked'] for item in batches),
              'raw_rows_checked': sum(item['raw_rows_checked'] for item in batches),
              'tensor_entries_compared': int(values.size), 'finite_fluid_entries_compared': int(np.isfinite(values).sum()),
              'masked_nan_entries_compared': int(np.isnan(values).sum()),
              'cylinder_mask_points': int(cylinder.sum()), 'raw_invalid_points_per_frame': invalid.sum(axis=(1, 2)).tolist(),
              'raw_invalid_mask_constant_across_all_frames': True,
              'direct_point_checks': len(direct_errors), 'direct_channel_value_checks': 3 * len(direct_errors),
              'max_direct_absolute_errors': direct_max.tolist(),
              'overlap_reference_directory': str(reference_dir.resolve()), 'overlap_frame_indices': indices,
              'overlap_tensor_entries_compared': int(reference['values'].size),
              'overlap_finite_fluid_entries_compared': int(np.isfinite(reference['values']).sum()),
              'overlap_max_absolute_errors': overlap_errors.tolist(),
              'limitations': ['Agreement with the same saved solution checks export and assembly, not pointwise CFD error.']}
    if not model_checked:
        result['limitations'].append('Original model bytes absent: only archived model-hash consistency checked; use --model-path to check model bytes.')
    output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8'))
    print(json.dumps({key: value for key, value in result.items() if key not in ('batches', 'raw_invalid_points_per_frame')}, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--sequence-dir', required=True, type=Path)
    parser.add_argument('--reference-dir', required=True, type=Path)
    parser.add_argument('--model-path', type=Path)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('sequence_artifact_audit.json'))
    args = parser.parse_args()
    audit(args.sequence_dir, args.reference_dir, args.output, args.model_path)
