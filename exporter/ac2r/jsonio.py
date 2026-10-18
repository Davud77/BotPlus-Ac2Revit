"""Bounded JSON output and file hashing for large model exchanges."""
import hashlib
import json
import os
from pathlib import Path
import tempfile


def write_json(path, data, *, compact=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if compact is None:
        compact = path.name in ('manifest.json', 'snapshot.json')
    temp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='\n',
                dir=path.parent, prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
            temp = Path(stream.name)
            # dump uses iterencode: no complete JSON string/UTF-8 copy in memory.
            json.dump(data, stream, ensure_ascii=False, indent=None if compact else 2,
                      separators=(',', ':') if compact else None, allow_nan=False)
        os.replace(temp, path)
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def file_sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def canonical_digest(value):
    """Same bytes/hash as the original canonical dumps, without its giant buffer."""
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=False, allow_nan=False,
                               separators=(',', ':'))
    parts, size = [], 0
    for part in encoder.iterencode(value):
        parts.append(part)
        size += len(part)
        if size >= 65536:
            digest.update(''.join(parts).encode('utf-8'))
            parts.clear()
            size = 0
    if parts:
        digest.update(''.join(parts).encode('utf-8'))
    return digest.hexdigest()


def archive_reference(path, root, pointer):
    path, root = Path(path).resolve(), Path(root).resolve()
    return {'format': 'ac2r.raw-json/1', 'relativeTo': 'sourceExport',
            'path': path.relative_to(root).as_posix(), 'jsonPointer': pointer,
            'sha256': file_sha256(path), 'bytes': path.stat().st_size}
