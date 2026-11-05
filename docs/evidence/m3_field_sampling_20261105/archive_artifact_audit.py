"""Independently validate native ZIP bytes and audit an extracted sample copy."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import uuid
import zipfile

from sample_artifact_audit import audit, sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--evidence-dir', required=True, type=Path)
    parser.add_argument('--scratch-dir', required=True, type=Path)
    args = parser.parse_args()
    manifest_path = args.evidence_dir / 'native_archive_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    archive_path = args.evidence_dir / manifest['archive']
    assert sha(archive_path) == manifest['archive_sha256']
    extracted = args.scratch_dir / ('independent_native_extract_' + uuid.uuid4().hex)
    extracted.mkdir()
    entries = {entry['path']: entry for entry in manifest['entries']}
    total_bytes = 0
    with zipfile.ZipFile(archive_path) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist())) == len(entries)
        assert set(archive.namelist()) == set(entries)
        for name, entry in entries.items():
            relative = PurePosixPath(name)
            assert not relative.is_absolute() and '..' not in relative.parts
            assert '\\' not in name and ':' not in name
            data = archive.read(name)
            assert len(data) == entry['bytes'] and hashlib.sha256(data).hexdigest() == entry['sha256']
            target = extracted / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            total_bytes += len(data)
    output = args.scratch_dir / 'extracted_sample_artifact_audit.json'
    audit(extracted, output=output)
    result = {'status': 'PASS', 'archive': str(archive_path.resolve()),
              'archive_sha256': sha(archive_path), 'entries_checked': len(entries),
              'uncompressed_bytes_checked': total_bytes,
              'all_entry_bytes_match_manifest': True,
              'extracted_sample_audit': str(output.resolve())}
    (args.scratch_dir / 'archive_artifact_audit.json').write_bytes(
        (json.dumps(result, indent=2) + '\n').encode('utf-8'))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
