"""Read-only live wall census, IFC identifiers and bounds; archive every API response."""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'exporter'))
from ac2r.client import TapirClient, aligned, chunks
from ac2r.normalize import guid


def capture(out, port=19723):
    out.mkdir(parents=True, exist_ok=False)
    api = TapirClient(port, out/'raw', timeout=180)
    project = api.call('GetProjectInfo')
    inventory = api.call('GetElementsByType', {'elementType': 'Wall'}, tapir=False)['elements']
    keys = [guid(e) for e in inventory]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError('Invalid or duplicate source wall identifiers')
    rows = {key: {'sourceGuid': key} for key in keys}
    for done, batch in enumerate(chunks(inventory, 100)):
        for command, result_key, field in (
            ('GetIFCIdsOfElements', 'elementIFCIds', 'ifc'),
            ('GetDetailsOfElements', 'detailsOfElements', 'details'),
            ('Get3DBoundingBoxes', 'boundingBoxes3D', 'bounds')):
            parameters = {'elements': batch}
            if field == 'details':
                parameters['fields'] = ['type', 'id', 'floorIndex', 'layerIndex', 'details']
            response = api.call(command, parameters)
            for request, row in aligned(response, result_key, batch):
                key = guid(request)
                returned = guid(row.get('elementId', {}))
                if returned and returned != key:
                    raise ValueError(f'{command}: response identity differs for {key}')
                rows[key][field] = row
        checkpoint = {'complete': False, 'project': project, 'wallCount': len(keys), 'walls': list(rows.values())}
        (out/'wall-identity.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Captured', min((done+1)*100, len(keys)), '/', len(keys), flush=True)
    after = api.call('GetElementsByType', {'elementType': 'Wall'}, tapir=False)['elements']
    if set(guid(e) for e in after) != set(keys):
        raise ValueError('Wall inventory changed during capture; repeat with an unchanged model')
    ids = [r['ifc'].get('ifcId') for r in rows.values() if 'error' not in r['ifc']]
    checkpoint.update(complete=True, capturedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                      missingIfcIds=sum(not i for i in ids), duplicateIfcIds=len([i for i in ids if i])-len(set(i for i in ids if i)))
    (out/'wall-identity.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2), encoding='utf-8')
    return checkpoint


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=19723)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = capture(args.out, args.port)
    print(json.dumps({k: v for k, v in result.items() if k not in ('walls', 'project')}, indent=2))
