import argparse
import collections
import json
from pathlib import Path
import sys
import uuid

from .client import TapirClient, ApiError, chunks, aligned
from .normalize import normalize, guid
from .jsonio import write_json, archive_reference


def export(args):
    target = Path(args.output)
    target.mkdir(parents=True, exist_ok=True)
    api = TapirClient(args.port, target/'raw', args.timeout, args.resume)
    snapshot = {'product': api.call('GetProductInfo', tapir=False),
                'tapir': api.call('GetAddOnVersion'), 'project': api.call('GetProjectInfo'),
                'stories': api.call('GetStories'), 'elements': [], 'attributes': {}, 'warnings': []}
    def optional(name, params=None, tapir=True):
        try:
            return api.call(name, params, tapir=tapir)
        except ApiError as exc:
            snapshot['warnings'].append(str(exc))
            print('WARNING', exc, flush=True)
            return {}

    snapshot['geolocation'] = optional('GetGeoLocation')
    snapshot['propertyDefinitions'] = optional('GetAllProperties')
    snapshot['classificationSystems'] = optional('GetAllClassificationSystems', tapir=False)
    snapshot['classificationSystems']['trees'] = {
        s['classificationSystemId']['guid']: optional('GetAllClassificationsInSystem',
            {'classificationSystemId': s['classificationSystemId']}, tapir=False)
        for s in snapshot['classificationSystems'].get('classificationSystems', [])}
    snapshot['elementInventory'] = api.call('GetAllElements')['elements']
    if args.include_2d:
        ids = snapshot['elementInventory']
    else:
        ids = []
        for kind in ('Wall', 'Slab', 'Door', 'Window', 'Zone', 'Column', 'Beam', 'Object', 'Lamp',
                     'Roof', 'Shell', 'Mesh', 'Morph', 'Stair', 'Railing', 'CurtainWall', 'Skylight', 'Opening'):
            rows = optional('GetElementsByType', {'elementType': kind}, tapir=False).get('elements', [])
            ids.extend(rows)
            print('Inventory:', kind, len(rows), flush=True)
    # Remove duplicate ids returned by section-parent conversions, before reading details.
    ids = list({guid(e['elementId']): e for e in ids}.values())
    if args.limit:
        ids = ids[:args.limit]
    print('Reading elements:', len(ids), flush=True)
    fields = ['type', 'id', 'floorIndex', 'layerIndex', 'drawIndex', 'hotlinkId', 'details']
    for i, batch in enumerate(chunks(ids, args.batch)):
        response = api.call('GetDetailsOfElements', {'elements': batch, 'fields': fields})
        for elem_id, details in aligned(response, 'detailsOfElements', batch):
            snapshot['elements'].append(dict(details, sourceGuid=guid(elem_id['elementId'])))
        if i % 10 == 0:
            print('Details:', min((i+1)*args.batch, len(ids)), '/', len(ids), flush=True)

    for kind, command, result_key in [('Composite', 'GetComposites', 'composites'),
                                      ('BuildingMaterial', 'GetBuildingMaterials', 'buildingMaterials'),
                                      ('Surface', 'GetSurfaces', 'surfaces'), ('Layer', 'GetLayers', 'layers'),
                                      ('Profile', 'GetProfiles', 'profiles'), ('ZoneCategory', None, None),
                                      ('Fill', 'GetFills', 'fills'), ('PenTable', 'GetPenTables', 'penTables'), ('Line', None, None)]:
        headers = optional('GetAttributesByType', {'attributeType': kind}).get('attributes', [])
        attr = {'headers': headers, 'details': []}
        if command:
            query = [{'attributeId': h['attributeId']} for h in headers if 'attributeId' in h]
            for batch in chunks(query, args.batch):
                response = optional(command, {'attributeIds': batch})
                attr['details'].extend(response.get(result_key, []))
        snapshot['attributes'][kind] = attr
        print('Attributes:', kind, len(headers), flush=True)

    # Make a usable geometry checkpoint even if an expensive calculated property times out.
    write_json(target/'snapshot.json', snapshot)
    write_json(target/'manifest.json', normalize(snapshot, args.project_id))
    write_json(target/'export-report.json', {'complete': False, 'message': 'Geometry checkpoint; metadata export in progress',
        'properties': args.properties, 'propertyScope': args.property_scope, 'gdl': args.gdl, 'ifc': args.ifc,
        'include2D': args.include_2d})
    prop_defs = {guid(p['propertyId']): p for p in snapshot['propertyDefinitions'].get('properties', []) if 'propertyId' in p}
    systems = [{'classificationSystemId': s['classificationSystemId']} for s in snapshot['classificationSystems'].get('classificationSystems', []) if 'classificationSystemId' in s]
    by_id = {e['sourceGuid']: e for e in snapshot['elements']}
    # IFC Tag depends on the selected translator. Preserve Archicad's explicit
    # IDs as a second exact identity path, including for IFC proxy classes.
    for batch in chunks(ids, args.batch):
        response = optional('GetIFCIdsOfElements', {'elements': batch})
        if response:
            for elem_id, identifiers in aligned(response, 'elementIFCIds', batch):
                by_id[guid(elem_id['elementId'])]['ifcIdentifiers'] = identifiers
    print('IFC identifiers:', len(ids), flush=True)
    wall_ids = [i for i in ids if by_id[guid(i['elementId'])].get('type') == 'Wall']
    for batch in chunks(wall_ids, args.batch):
        response = optional('GetRelationsOfElements', {'elements': batch, 'otherElementType': 'Wall'})
        if response:
            for elem_id, relation in aligned(response, 'relations', batch):
                by_id[guid(elem_id['elementId'])]['relations'] = relation
    for i, batch in enumerate(chunks(ids, args.metadata_batch)):
        if args.properties:
            available = optional('GetAllPropertyIdsOfElements', {'elements': batch}, tapir=False)
            groups = {}
            if available:
                for elem_id, row in aligned(available, 'propertyIdsOfElements', batch):
                    candidates = row.get('propertyIdsOfElement', {}).get('propertyIds', [])
                    if args.property_scope == 'custom':
                        candidates = [p for p in candidates if prop_defs.get(guid(p['propertyId']), {}).get('propertyType') == 'Custom']
                    key = tuple(sorted(guid(p['propertyId']) for p in candidates))
                    groups.setdefault(key, []).append(elem_id)
            for property_keys, elem_batch in groups.items():
                props = [{'propertyId': {'guid': k}} for k in property_keys]
                for prop_batch in chunks(props, args.property_batch):
                    response = optional('GetPropertyValuesOfElements', {'elements': elem_batch, 'properties': prop_batch}, tapir=False)
                    if response:
                        for elem_id, values in aligned(response, 'propertyValuesForElements', elem_batch):
                            by_id[guid(elem_id['elementId'])].setdefault('propertyBatches', []).append({'properties': prop_batch, 'values': values})
        if systems:
            response = optional('GetClassificationsOfElements', {'elements': batch, 'classificationSystemIds': systems})
            if response:
                for elem_id, values in aligned(response, 'elementClassifications', batch):
                    by_id[guid(elem_id['elementId'])]['classifications'] = values
        if args.ifc:
            response = optional('GetIFCPropertiesOfElements', {'elements': batch})
            if response:
                for elem_id, values in aligned(response, 'elementIFCProperties', batch):
                    by_id[guid(elem_id['elementId'])]['ifc'] = values
        if i % 10 == 0:
            print('Metadata:', min((i+1)*args.metadata_batch, len(ids)), '/', len(ids), flush=True)
    if args.gdl:
        gdl_ids = [e for e in ids if by_id[guid(e['elementId'])].get('type') in ('Door', 'Window', 'Object', 'Lamp', 'Zone', 'Skylight', 'Label')]
        for batch in chunks(gdl_ids, args.batch):
            response = optional('GetGDLParametersOfElements', {'elements': batch})
            if response:
                reference = archive_reference(api.last_archive, target, '/response/result/addOnCommandResponse/gdlParametersOfElements')
                for row, (elem_id, values) in enumerate(aligned(response, 'gdlParametersOfElements', batch)):
                    by_id[guid(elem_id['elementId'])]['gdlReference'] = dict(reference, jsonPointer=reference['jsonPointer']+'/'+str(row))
                # Full parameters remain losslessly in the immutable raw response.
                # Do not retain gigabytes of duplicated GDL arrays in the snapshot.
                del values
            response = None
        print('GDL:', len(gdl_ids), flush=True)
    # Detect accidental change of active document/story structure during long serial export.
    if api.call('GetProjectInfo') != snapshot['project'] or api.call('GetStories') != snapshot['stories']:
        raise ApiError('Active project/story structure changed during export. Retry with a stable model.')
    write_json(target/'snapshot.json', snapshot)
    manifest = normalize(snapshot, args.project_id)
    write_json(target/'manifest.json', manifest)
    counts = collections.Counter(e['kind'] for e in manifest['elements'])
    statuses = collections.Counter(e['status'] for e in manifest['elements'])
    report = {'complete': True, 'counts': dict(counts), 'statuses': dict(statuses), 'warnings': snapshot['warnings'],
              'issues': manifest['issues'], 'scope': 'current database; no visibility filters',
              'inventoryCount': len(snapshot['elementInventory']), 'include2D': args.include_2d,
              'properties': args.properties, 'propertyScope': args.property_scope, 'gdl': args.gdl, 'ifc': args.ifc,
              'gdlStorage': 'archivedResponsesWithSha256References',
              'snapshotConsistency': 'document/story checked; element edits are not locked'}
    write_json(target/'export-report.json', report)
    # Populate mapping inventory without guessing equivalent Revit families.
    inventory = {}
    for e in manifest['elements']:
        if e['status'] == 'needsMapping':
            p = e['payload']
            key = e['kind'] + ':' + (p.get('libraryGuid') or e['sourceGuid'])
            inventory.setdefault(key, {'kind': e['kind'], 'libraryName': p.get('libraryName', ''),
                                      'sourceGuid': e['sourceGuid'], 'count': 0})['count'] += 1
    write_json(target/'mapping-inventory.json', inventory)
    print(json.dumps({'counts': dict(counts), 'statuses': dict(statuses), 'output': str(target)}, ensure_ascii=True), flush=True)


def main():
    p = argparse.ArgumentParser(description='Read-only Tapir export to deterministic manifest')
    p.add_argument('--port', type=int, default=19723)
    p.add_argument('--output', default='artifacts/export')
    p.add_argument('--project-id', required=True, help='Stable user-managed UUID, retained when the PLN moves')
    p.add_argument('--batch', type=int, default=100)
    p.add_argument('--property-batch', type=int, default=100)
    p.add_argument('--metadata-batch', type=int, default=20)
    p.add_argument('--property-scope', choices=['custom', 'all'], default='custom')
    p.add_argument('--resume', action='store_true', help='Reuse archived responses; only with an unchanged source model')
    p.add_argument('--timeout', type=int, default=180)
    p.add_argument('--limit', type=int)
    p.add_argument('--properties', action='store_true')
    p.add_argument('--gdl', action='store_true')
    p.add_argument('--ifc', action='store_true')
    p.add_argument('--include-2d', action='store_true', help='Also read annotation/drawing details; can be very large')
    args = p.parse_args()
    uuid.UUID(args.project_id)
    if args.batch <= 0 or args.property_batch <= 0 or args.metadata_batch <= 0:
        p.error('batch sizes must be positive')
    try:
        export(args)
    except Exception as exc:
        print(f'EXPORT FAILED: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
