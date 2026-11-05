"""Safely extract and independently audit all native sequence archives."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import sys
import time
import zipfile
import numpy as np

from sequence_artifact_audit import audit, read, sha, npz, MODEL_SHA


def safe_parts(name):
    path = PurePosixPath(name)
    assert name and not path.is_absolute() and '..' not in path.parts
    assert '\\' not in name and ':' not in name and str(path) == name
    return path.parts


def run(args):
    manifest = read(args.evidence_dir / 'native_archive_manifest.json')
    assert manifest['status'] == 'PASS' and manifest['source_model_sha256'].lower() == MODEL_SHA
    assert len(manifest['archives']) == 6 and manifest['native_entries'] == 170
    assert not args.output_dir.exists() and not args.output_dir.is_symlink(), 'Extraction directory must be new'
    assert not args.output.exists(), 'Audit JSON must be new'
    independent_output = args.output.with_name(args.output.stem + '_sequence.json')
    assert not independent_output.exists(), 'Independent audit JSON must be new'
    args.output_dir.mkdir(parents=True, exist_ok=False)
    names, archive_names, archives = set(), set(), []
    total_bytes = 0
    for archive_info in manifest['archives']:
        archive_name = archive_info['path']
        assert len(safe_parts(archive_name)) == 1 and archive_name not in archive_names
        archive_names.add(archive_name)
        source = args.evidence_dir / archive_name
        assert source.stat().st_size == archive_info['bytes'] and sha(source) == archive_info['sha256']
        expected = {item['path']: item for item in archive_info['entries']}
        assert len(expected) == len(archive_info['entries'])
        with zipfile.ZipFile(source) as archive:
            assert len(archive.namelist()) == len(set(archive.namelist())) == len(expected)
            assert set(archive.namelist()) == set(expected)
            for name, item in expected.items():
                safe_parts(name)
                assert name not in names
                names.add(name)
                info = archive.getinfo(name)
                assert not info.is_dir() and not stat.S_ISLNK(info.external_attr >> 16)
                data = archive.read(name)
                assert len(data) == item['bytes'] and hashlib.sha256(data).hexdigest() == item['sha256']
                if args.runtime_dir is not None:
                    assert sha(args.runtime_dir / name) == item['sha256']
                target = args.output_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as handle:
                    handle.write(data)
                total_bytes += len(data)
        archives.append({'path': archive_name, 'bytes': archive_info['bytes'], 'sha256': archive_info['sha256'], 'entries_checked': len(expected)})
    assert len(names) == 170
    if args.runtime_dir is not None:
        actual_names = {p.relative_to(args.runtime_dir).as_posix() for p in args.runtime_dir.rglob('*') if p.is_file()}
        assert names == actual_names, 'Archived membership differs from published runtime directory'
    audit(args.output_dir, args.reference_dir, independent_output, args.model_path)
    try:
        from hydro_disturb_bench.field_sequence import load_full_field_sequence
    except ModuleNotFoundError as exc:
        if exc.name != 'hydro_disturb_bench':
            raise
        # A repository checkout need not be installed as a package. Discover its
        # src tree relative to this archived script, never a private absolute path.
        for parent in Path(__file__).resolve().parents:
            if (parent / 'src' / 'hydro_disturb_bench').is_dir():
                sys.path.insert(0, str(parent / 'src'))
                break
        else:
            raise
        from hydro_disturb_bench.field_sequence import load_full_field_sequence
    started = time.perf_counter()
    loaded = load_full_field_sequence(args.output_dir)
    source_manifest = read(args.output_dir / 'sequence_manifest.json')
    direct_batches = [npz(args.output_dir / entry['path'] / 'field_sample.npz') for entry in source_manifest['batches']]
    for key in ('values', 'mask', 'invalid_mask', 'times'):
        direct = np.concatenate([batch[key] for batch in direct_batches])
        assert np.array_equal(loaded[key], direct, equal_nan=True), key
    for key in ('x', 'y', 'cylinder_mask'):
        assert np.array_equal(loaded[key], direct_batches[0][key]), key
    loader_elapsed = time.perf_counter() - started
    result = {'status': 'PASS', 'checked_utc': datetime.now(timezone.utc).isoformat(),
              'archives': archives, 'archives_checked': len(archives), 'entries_checked': len(names),
              'total_compressed_bytes': sum(item['bytes'] for item in archives),
              'total_native_uncompressed_bytes': total_bytes,
              'all_archive_and_entry_hashes_match': True,
              'all_entries_match_published_runtime_bytes': args.runtime_dir is not None,
              'extracted_directory': str(args.output_dir.resolve()),
              'independent_sequence_audit': str(independent_output.resolve()),
              'production_loader_check': {'status': 'PASS', 'function': 'hydro_disturb_bench.field_sequence.load_full_field_sequence',
                                          'shape': list(loaded['values'].shape), 'compared_tensor_entries': int(loaded['values'].size),
                                          'all_seven_arrays_match_direct_npz_assembly': True, 'elapsed_seconds': loader_elapsed},
              'scope': 'ZIP bytes and extracted files independently validated; separate production-loader readback checks all seven arrays. No COMSOL process started.'}
    with args.output.open('xb') as handle:
        handle.write((json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path, help='New directory; refuses overwrite')
    parser.add_argument('--reference-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New archive audit JSON')
    parser.add_argument('--runtime-dir', type=Path, help='Optional original runtime directory for independent byte comparison')
    parser.add_argument('--model-path', type=Path)
    run(parser.parse_args())
