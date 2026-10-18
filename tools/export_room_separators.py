"""Read source 2D zone-boundary flags; never derive them from room polygons."""
import argparse
import collections
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'exporter'))
from ac2r.client import TapirClient, aligned, chunks


def same_source(expected, actual):
    """Compare existing fields, allowing only API numeric serialization noise."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and same_source(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(same_source(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, bool):
        return expected is actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isfinite(actual) and abs(expected-actual) <= 1e-8
    return expected == actual


def extract(manifest_path, project_path, out, allowed_missing=(), record_wall_differences=False, resume=False, port=19723):
    out.mkdir(parents=True, exist_ok=resume)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    api = TapirClient(port, archive=out/'raw', timeout=90, resume=resume)
    project = api.call('GetProjectInfo')
    if Path(project.get('projectPath', '')).resolve() != project_path.resolve():
        raise ValueError('Unexpected active Archicad project')
    verified, missing_source, wall_differences = {}, {}, []
    allowed_missing = {g.upper() for g in allowed_missing}
    # A 2D supplement must belong to the same physical model and source zones.
    for kind in ('Wall', 'Column', 'Zone'):
        expected = {e['sourceGuid'].upper(): e for e in manifest['elements'] if e['kind'] == kind}
        ids = api.call('GetElementsByType', {'elementType': kind})['elements']
        actual_ids = [i['elementId']['guid'].upper() for i in ids]
        missing=set(expected)-set(actual_ids)
        if len(set(actual_ids)) != len(actual_ids) or set(actual_ids)-set(expected) or not missing <= allowed_missing:
            raise ValueError('Source GUID inventory changed: '+kind)
        missing_source[kind]=sorted(missing)
        for batch in chunks(ids, 500):
            details = api.call('GetDetailsOfElements', {'elements': batch, 'fields': ['type', 'floorIndex', 'details']})
            for item, row in aligned(details, 'detailsOfElements', batch):
                guid = item['elementId']['guid'].upper()
                old = expected[guid]['raw']
                if row.get('type') != kind or row.get('floorIndex') != old['floorIndex'] or not same_source(old['details'], row.get('details')):
                    if kind=='Wall' and record_wall_differences:
                        wall_differences.append({'sourceGuid':guid,'expected':old,'actual':row})
                    else:raise ValueError('Source geometry/details changed: '+guid)
        verified[kind] = len(ids)
        print('Verified source:', kind, len(ids), flush=True)
    rows, inventory, flags, circle_guids = [], {}, {}, set()
    for kind in ('Line', 'PolyLine', 'Arc', 'Circle', 'Spline'):
        ids = api.call('GetElementsByType', {'elementType': kind})['elements']
        inventory[kind] = len(ids)
        if kind=='Circle':circle_guids={i['elementId']['guid'].upper() for i in ids}
        print('2D inventory:', kind, len(ids), flush=True)
        for n,batch in enumerate(chunks(ids, 500)):
            details = api.call('GetDetailsOfElements', {'elements': batch, 'fields': ['type', 'id', 'floorIndex', 'layerIndex', 'details']})
            for item,row in aligned(details, 'detailsOfElements', batch):
                guid=item['elementId']['guid'].upper()
                flag=row.get('details',{}).get('roomSeparator')
                if guid in flags and flags[guid] is not flag:raise ValueError('Conflicting flags for aliased element: '+guid)
                flags[guid]=flag
                if flag is True:rows.append(dict(row,sourceGuid=guid))
            if n%20==0:print('Read',kind,min((n+1)*500,len(ids)),'/',len(ids),'confirmed separators',len(rows),flush=True)
    unique={}
    for row in rows:
        guid=row['sourceGuid']
        if guid in unique and unique[guid]!=row:raise ValueError('Conflicting aliased 2D source GUID: '+guid)
        unique[guid]=row
    rows=list(unique.values())
    for row in rows:row['isCircleFromInventory']=row['sourceGuid'] in circle_guids
    report = {'projectId': manifest['source']['projectId'], 'sourceProject': project,
              'verifiedAgainstManifest': str(manifest_path.resolve()), 'verifiedSourceCounts': verified,
              'missingSourceGuidsPreservedInRevit':missing_source,
              'wallDifferencesPreservedInRevit':wall_differences,
              'physicalSourceIdentical':not wall_differences and not any(missing_source.values()),
              'inventory': inventory, 'trueFlags': dict(collections.Counter(r.get('type') for r in rows if r.get('details', {}).get('roomSeparator') is True)),
              'unique2DElements':len(flags), 'aliasedInventoryOccurrences':sum(inventory.values())-len(flags),
              'falseFlags':sum(f is False for f in flags.values()),'missingFlags':sum(f is None for f in flags.values())}
    for name, data in [('elements.json', {'projectId': manifest['source']['projectId'], 'elements': rows}), ('export-report.json', report)]:
        (out/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--project', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--allow-missing-source', action='append', default=[])
    p.add_argument('--record-wall-differences', action='store_true')
    p.add_argument('--resume', action='store_true')
    a = p.parse_args()
    result=extract(a.manifest,a.project,a.out,a.allow_missing_source,a.record_wall_differences,a.resume)
    print(json.dumps({k:v for k,v in result.items() if k!='wallDifferencesPreservedInRevit'},ensure_ascii=True,indent=2))
    print('Wall source differences retained:',len(result['wallDifferencesPreservedInRevit']))
