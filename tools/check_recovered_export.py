"""Independently compare a recovered export with archived geometry and GDL IDs."""
import argparse
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.jsonio import file_sha256, write_json


def check(source):
    with (source/'manifest.json').open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    elements = {e['sourceGuid']: e for e in manifest['elements']}
    geometry, gdl, archives = set(), set(), collections.defaultdict(list)
    for element in elements.values():
        reference = element['raw'].get('gdlReference')
        if reference:
            archives[reference['path']].append((element['sourceGuid'], reference))
    for path in sorted((source/'raw').glob('*_GetDetailsOfElements.json')):
        with path.open(encoding='utf-8') as stream:
            item = json.load(stream)
        params = item['request']['parameters']['addOnCommandParameters']
        rows = item['response']['result']['addOnCommandResponse']['detailsOfElements']
        if len(params['elements']) != len(rows):
            raise ValueError('Geometry count mismatch: '+path.name)
        for elem, raw in zip(params['elements'], rows):
            key = elem['elementId']['guid'].upper()
            if key in geometry or key not in elements:
                raise ValueError('Unknown/duplicate geometry GUID: '+key)
            target = elements[key]['raw']
            if any(target.get(k) != v for k, v in raw.items()):
                raise ValueError('Geometry changed: '+key)
            geometry.add(key)
    if geometry != set(elements):
        raise ValueError('Missing source geometry')
    root = source.resolve()
    for name, refs in archives.items():
        path = (source/name).resolve()
        if not path.is_relative_to(root):
            raise ValueError('GDL reference outside source export')
        sha = file_sha256(path)
        with path.open(encoding='utf-8') as stream:
            item = json.load(stream)
        inputs = item['request']['parameters']['addOnCommandParameters']['elements']
        rows = item['response']['result']['addOnCommandResponse']['gdlParametersOfElements']
        if len(inputs) != len(rows) or len(refs) != len(rows):
            raise ValueError('GDL reference count mismatch: '+name)
        for key, ref in refs:
            prefix, ordinal = ref['jsonPointer'].rsplit('/', 1)
            if prefix != '/response/result/addOnCommandResponse/gdlParametersOfElements':
                raise ValueError('Invalid GDL JSON pointer')
            if sha != ref['sha256'] or path.stat().st_size != ref['bytes'] or inputs[int(ordinal)]['elementId']['guid'].upper() != key:
                raise ValueError('GDL identity/hash changed: '+key)
            if key in gdl:
                raise ValueError('Duplicate GDL GUID')
            gdl.add(key)
    return {'status': 'passed', 'source': str(root), 'manifestSha256': file_sha256(source/'manifest.json'),
            'geometryComparedWithApi': len(geometry), 'geometryDifferences': 0,
            'gdlReferencesVerified': len(gdl), 'gdlArchivesVerified': len(archives),
            'counts': dict(collections.Counter(e['kind'] for e in elements.values())),
            'ifcExported': (source/'model.ifc').is_file(), 'revitBuildVerified': False}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    result = check(args.source)
    write_json(args.out, result)
    print(json.dumps(result, ensure_ascii=False))
