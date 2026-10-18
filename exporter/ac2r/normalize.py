import hashlib
import json
import math
from .geometry import point, curve, loops, interior_point, morph_mesh
from .jsonio import canonical_digest


def digest(value):
    return canonical_digest(value)


def guid(value):
    if not isinstance(value, dict):
        return ''
    if 'guid' in value:
        return value['guid'].upper()
    if 'attributeId' in value:
        return guid(value['attributeId'])
    if 'elementId' in value:
        return guid(value['elementId'])
    return ''


def normalize(snapshot, project_id):
    issues, elements, types = [], [], {}
    stories = snapshot['stories']['stories']
    levels = [{'key': f'story:{s["index"]}', 'index': int(s['index']),
               'name': s['name'] or f'Этаж {s["index"]}', 'elevation': float(s['level']),
               'raw': s} for s in stories]
    by_index = {s['index']: s for s in levels}
    composites = {guid(c.get('attributeId')): c for c in snapshot.get('attributes', {}).get('Composite', {}).get('details', []) if 'error' not in c}
    materials = snapshot.get('attributes', {}).get('BuildingMaterial', {}).get('details', [])

    def construction(d, kind):
        if d.get('structureType') == 'Profile':
            raise ValueError('Complex profile needs profile/solid adapter')
        comp_id = guid(d.get('compositeId'))
        if comp_id:
            if comp_id not in composites:
                raise ValueError('Missing composite ' + comp_id)
            comp = composites[comp_id]
            skins = comp['skins']
            name = comp['name']
        else:
            skins = [{'type': 'Core', 'thickness': d.get('thickness', d.get('begThickness')),
                      'buildingMaterialId': d.get('buildingMaterialId', {})}]
            name = 'Basic'
        layers = [{'width': float(s['thickness']), 'function': 'Membrane' if s['thickness'] == 0 and s['type'] != 'Core' else s['type'],
                   'sourceFunction': s['type'],
                   'materialKey': guid(s.get('buildingMaterialId'))} for s in skins]
        if not layers or any(s['width'] < 0 or (s['width'] == 0 and s['function'] != 'Membrane') or not math.isfinite(s['width']) for s in layers):
            raise ValueError('Invalid construction layer thickness')
        core = [i for i, s in enumerate(layers) if s['function'] == 'Core']
        if core and core != list(range(core[0], core[-1]+1)):
            raise ValueError('Non-contiguous core cannot be mapped without loss')
        data = {'kind': kind, 'layers': layers, 'sourceCompositeGuid': comp_id, 'name': name}
        key = kind + ':' + digest(data)[:20]
        types[key] = dict(data, key=key)
        return key, layers

    for raw in snapshot['elements']:
        source_id, kind = raw['sourceGuid'], raw.get('type', 'Unknown')
        e = {'sourceGuid': source_id, 'kind': kind, 'mark': raw.get('id', ''),
             'levelKey': f'story:{raw.get("floorIndex")}', 'dependencies': [],
             'status': 'unsupported', 'raw': raw, 'payload': {}}
        if kind == 'Wall':
            e['wallConnections'] = []
            connections = raw.get('relations', {}).get('wallConnections', {})
            for relation, items in connections.items():
                for item in items:
                    target = guid(item.get('elementId'))
                    if target and target != source_id:
                        e['wallConnections'].append({'targetGuid': target, 'relation': relation,
                            'ownerEnd': 0 if relation == 'connectedToBeginPoint' else 1 if relation == 'connectedToEndPoint' else None,
                            'targetWithBeginPoint': bool(item.get('connectedWithBeginPoint'))})
        try:
            if 'error' in raw:
                raise ValueError('Tapir element error: ' + str(raw['error']))
            if raw.get('floorIndex') not in by_index:
                raise ValueError('Unknown home story index')
            d = raw.get('details', {})
            level = by_index[raw['floorIndex']]['elevation']
            if kind == 'Wall':
                if d.get('geometryType') not in ('Straight', None) or d.get('profileType', 'Normal') != 'Normal':
                    raise ValueError('Polygonal/trapezoid/slanted wall needs solid/profile adapter')
                key, layers = construction(d, kind)
                if abs(sum(x['width'] for x in layers)-d['begThickness']) > 1e-6:
                    raise ValueError('Wall thickness differs from construction')
                z = float(d['zCoordinate'])
                if abs(z-level-float(d['bottomOffset'])) > 1e-6:
                    raise ValueError('Wall absolute Z and home-story offset disagree')
                if d['height'] <= 0:
                    raise ValueError('Non-positive wall height')
                e['payload'] = {'curve': curve(point(d['begCoordinate'], z), point(d['endCoordinate'], z), d.get('arcAngle', 0)),
                                'typeKey': key, 'height': float(d['height']), 'bottomOffset': z-level,
                                'flipped': bool(d['flipped']), 'referenceLine': d.get('referenceLineLocation', 'Center'),
                                'referenceOffset': float(d.get('offset', 0)),
                                'roomBounding': d.get('zoneRel', 'Boundary') == 'Boundary'}
                if abs(d.get('offset', 0)) > 1e-9 and abs(d.get('arcAngle', 0)) > 1e-10:
                    raise ValueError('Nonzero reference-line offset on an arc requires radial calibration; source value preserved')
                e['status'] = 'ready'
            elif kind == 'Slab':
                key, layers = construction(d, 'Floor')
                width = sum(s['width'] for s in layers)
                if abs(width-float(d['thickness'])) > 1e-6:
                    raise ValueError('Slab thickness differs from construction')
                core = [i for i, s in enumerate(layers) if s['function'] == 'Core']
                ref = d.get('referencePlaneLocation', 'Top')
                if ref == 'Top':
                    shift = 0
                elif ref == 'Bottom':
                    shift = width
                elif core and ref == 'CoreTop':
                    shift = sum(s['width'] for s in layers[:core[0]])
                elif core and ref == 'CoreBottom':
                    shift = sum(s['width'] for s in layers[:core[-1]+1])
                else:
                    raise ValueError('Unknown slab reference plane or missing core')
                # Tapir zCoordinate is the reference plane; Revit floor offset uses its top.
                top = float(d['zCoordinate']) + shift
                e['payload'] = {'loops': loops(d, top), 'typeKey': key, 'topOffset': top-level}
                e['status'] = 'ready'
            elif kind in ('Door', 'Window'):
                host = guid(d.get('ownerElementId'))
                if not host or d.get('ownerElementType') != 'Wall':
                    raise ValueError('Opening has no wall host')
                e['dependencies'] = [host]
                e['payload'] = {'hostGuid': host, 'libraryGuid': guid(d.get('libPart', {}).get('ownUnID')),
                                'libraryName': d.get('libPart', {}).get('name', ''),
                                **{k: d[k] for k in ('width', 'height', 'sillHeight', 'centerOffset', 'reflected', 'refSide', 'oSide')}}
                if d['width'] <= 0 or d['height'] <= 0:
                    raise ValueError('Invalid opening dimensions')
                e['status'] = 'needsMapping'
            elif kind == 'Zone':
                profile = loops(d, float(d['zCoordinate']))
                p = interior_point(profile[0], profile[1:])
                e['payload'] = {'loops': profile, 'point': p, 'name': d['name'],
                                'number': d['numberStr'], 'baseOffset': float(d['zCoordinate'])-level}
                e['status'] = 'ready'
            elif kind in ('Column', 'Beam', 'Object', 'Lamp'):
                e['payload'] = {'details': d, 'libraryGuid': guid(d.get('libPart', {}).get('ownUnID')) or guid(d.get('profileId')),
                                'libraryName': d.get('libPart', {}).get('name', '')}
                e['status'] = 'needsMapping'
            elif kind == 'Morph':
                e['payload'] = {'mesh': morph_mesh(d), 'materialKey': guid(d.get('buildingMaterialId'))}
                e['status'] = 'ready'
            else:
                raise ValueError('No native adapter for ' + kind + '; raw data preserved')
        except (KeyError, TypeError, ValueError) as exc:
            e['status'] = 'unsupported'
            e['reason'] = str(exc)
            issues.append({'sourceGuid': source_id, 'kind': kind, 'message': str(exc)})
        e['contentHash'] = digest(e)
        elements.append(e)
    all_ids = {e['sourceGuid'] for e in elements}
    for e in elements:
        missing = [h for h in e['dependencies'] if h not in all_ids]
        if missing:
            e.update(status='unsupported', reason='Missing hosts: ' + ', '.join(missing))
            e['contentHash'] = digest({k: v for k, v in e.items() if k not in ('raw', 'contentHash')})
    result = {'schemaVersion': '1.0.0', 'source': {'application': 'Archicad', 'projectId': project_id,
              'project': snapshot.get('project', {}), 'product': snapshot.get('product', {}),
              'tapir': snapshot.get('tapir', {}), 'scope': 'currentDatabase'},
              'units': {'length': 'm', 'angle': 'rad'},
              'coordinates': {'system': 'ArchicadProject', 'transform': {'translation': {'x': 0, 'y': 0, 'z': 0}, 'rotationRadians': 0},
                              'geolocation': snapshot.get('geolocation', {})},
              'levels': levels, 'materials': materials,
              'surfaces': snapshot.get('attributes', {}).get('Surface', {}).get('details', []),
              'fills': snapshot.get('attributes', {}).get('Fill', {}).get('details', []),
              'penTables': snapshot.get('attributes', {}).get('PenTable', {}).get('details', []),
              'types': list(types.values()),
              'elements': elements, 'issues': issues,
              'propertyDefinitions': snapshot.get('propertyDefinitions', {}),
              'classificationSystems': snapshot.get('classificationSystems', {})}
    result['manifestHash'] = digest(result)
    return result
