"""Match source walls by explicit IFC IDs; extract net tessellated geometry in metres.

Bounds are a placement check, not proof of full geometric equality. IFC georeferencing
is recorded, never silently fitted. An explicit rigid transform can be supplied.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
from pathlib import Path


def audit(ifc_path, identity_path, out, geometry_limit=0, transform_path=None, geometry_guids=None):
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.unit
    import numpy as np

    source = json.loads(identity_path.read_text(encoding='utf-8'))
    if not source.get('complete'):
        raise ValueError('Source identity capture is incomplete')
    source_rows = source['walls']
    keys = [r['sourceGuid'] for r in source_rows]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate source GUIDs')
    transform = {'translationMeters': [0, 0, 0], 'rotationRadians': 0}
    if transform_path:
        transform = json.loads(transform_path.read_text(encoding='utf-8'))
    translation = np.asarray(transform['translationMeters'], dtype=float)
    angle = float(transform['rotationRadians'])
    if translation.shape != (3,) or not np.all(np.isfinite(translation)) or not math.isfinite(angle):
        raise ValueError('Invalid coordinate transform')
    rotation = np.array([[math.cos(angle), -math.sin(angle), 0], [math.sin(angle), math.cos(angle), 0], [0, 0, 1]])
    out.mkdir(parents=True, exist_ok=False)
    model = ifcopenshell.open(str(ifc_path))
    products = collections.defaultdict(list)
    for entity in model.by_type('IfcProduct'):
        if entity.GlobalId:
            products[entity.GlobalId].append(entity)
    settings = ifcopenshell.geom.settings()
    settings.set('use-world-coords', True)
    settings.set('mesher-linear-deflection', 0.00025)
    settings.set('disable-opening-subtractions', False)
    def geometry_products(element, seen=None):
        seen = set() if seen is None else seen
        if element.id() in seen:
            raise ValueError('Cyclic IFC decomposition')
        seen.add(element.id())
        representations = getattr(getattr(element, 'Representation', None), 'Representations', [])
        if any(r.RepresentationIdentifier == 'Body' for r in representations):
            return [element]
        children = [child for relation in getattr(element, 'IsDecomposedBy', [])
                    for child in relation.RelatedObjects if child.is_a('IfcProduct')]
        if not children:
            raise ValueError('No Body geometry or decomposed product bodies')
        return [body for child in children for body in geometry_products(child, seen)]
    rows, matched_ids, geometry_done = [], set(), 0
    for source_row in source_rows:
        ids = source_row.get('ifc', {})
        # Primary identifier first. External ID is only an explicit alternative.
        candidates = products.get(ids.get('ifcId', ''), [])
        used_id = ids.get('ifcId', '')
        if not candidates and ids.get('externalIFCId'):
            used_id = ids['externalIFCId']
            candidates = products.get(used_id, [])
        row = {'sourceGuid': source_row['sourceGuid'], 'ifcGlobalId': used_id,
               'identityStatus': 'missing' if not candidates else 'ambiguous' if len(candidates) > 1 else 'matched',
               'geometryStatus': 'notProcessed'}
        if len(candidates) == 1:
            element = candidates[0]
            matched_ids.add(element.id())
            row.update(ifcClass=element.is_a(), stepId=element.id(), name=element.Name,
                       wallClass=element.is_a('IfcWall'), openings=len(getattr(element, 'HasOpenings', [])))
            if geometry_limit and geometry_done < geometry_limit and (geometry_guids is None or source_row['sourceGuid'] in geometry_guids):
                geometry_done += 1
                try:
                    parts = geometry_products(element)
                    vertex_chunks, face_chunks, cursor = [], [], 0
                    for part in parts:
                        shape = ifcopenshell.geom.create_shape(settings, part)
                        points = np.asarray(shape.geometry.verts, dtype=float).reshape((-1, 3)) @ rotation.T + translation
                        triangles = np.asarray(shape.geometry.faces, dtype=int).reshape((-1, 3))
                        if not len(points) or not len(triangles):
                            raise ValueError('Empty decomposed body')
                        vertex_chunks.append(points); face_chunks.append(triangles + cursor); cursor += len(points)
                    vertices = np.concatenate(vertex_chunks)
                    faces = np.concatenate(face_chunks)
                    if not len(vertices) or not len(faces) or not np.isfinite(vertices).all():
                        raise ValueError('Empty/nonfinite mesh')
                    mesh_path = out/'meshes'/f'{source_row["sourceGuid"]}.npz'
                    mesh_path.parent.mkdir(exist_ok=True)
                    np.savez_compressed(mesh_path, vertices=vertices, faces=faces)
                    lo, hi = vertices.min(axis=0), vertices.max(axis=0)
                    edges = collections.Counter(tuple(sorted((int(a), int(b)))) for f in faces for a,b in zip(f, np.roll(f,-1)))
                    closed = all(count == 2 for count in edges.values())
                    # Translate near zero before computing approximate signed mesh volume.
                    local = vertices - vertices[0]
                    tri = local[faces]
                    volume = abs(float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6)) if closed else None
                    row.update(geometryStatus='extracted', mesh=str(mesh_path), boundsMinMeters=lo.tolist(),
                               boundsMaxMeters=hi.tolist(), meshClosedEdgeCheck=closed, approximateMeshVolumeM3=volume,
                               vertices=len(vertices), triangles=len(faces), geometryProductStepIds=[p.id() for p in parts])
                    box = source_row.get('bounds', {}).get('boundingBox3D')
                    if box and all(axis+side in box for axis in 'xyz' for side in ('Min','Max')):
                        delta = max(abs(float(lo[i])-box[a+'Min']) for i,a in enumerate('xyz'))
                        delta = max(delta, max(abs(float(hi[i])-box[a+'Max']) for i,a in enumerate('xyz')))
                        row.update(boundsMaxDifferenceMeters=delta, boundsWithin1mm=delta <= 0.001)
                except Exception as error:
                    row.update(geometryStatus='error', error=str(error))
        rows.append(row)
        if len(rows) % 100 == 0:
            print('Audited', len(rows), '/', len(source_rows), flush=True)
    actual_walls = model.by_type('IfcWall')
    map_conversion = model.by_type('IfcMapConversion') if model.schema != 'IFC2X3' else []
    summary = {'ifcPath':str(ifc_path.resolve()), 'ifcSha256':hashlib.sha256(ifc_path.read_bytes()).hexdigest(),
               'schema':model.schema, 'sourceWalls':len(rows), 'ifcWallEntities':len(actual_walls),
               'identityStatuses':dict(collections.Counter(r['identityStatus'] for r in rows)),
               'geometryStatuses':dict(collections.Counter(r['geometryStatus'] for r in rows)),
               'unmatchedIfcWallStepIds':[w.id() for w in actual_walls if w.id() not in matched_ids],
               'ifcLengthUnitToMeters':ifcopenshell.util.unit.calculate_unit_scale(model),
               'explicitIFCToArchicad':transform, 'mapConversion':[e.get_info() for e in map_conversion],
               'coordinateNote':'World placements are applied by the geometry engine; CRS MapConversion is not automatically applied. Validate the explicit transform.',
               'geometryEqualityProven':False, 'note':'Tessellated net meshes include opening subtraction. Bounds and volume do not establish BRep equality.'}
    (out/'audit.json').write_text(json.dumps({'summary':summary,'walls':rows},ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    with (out/'identity.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=['sourceGuid','ifcGlobalId','identityStatus','ifcClass','stepId','geometryStatus'],extrasaction='ignore')
        writer.writeheader();writer.writerows(rows)
    return summary


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('ifc',type=Path);parser.add_argument('identity',type=Path);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--geometry-limit',type=int,default=0,help='0 = identity only; use source wall count to extract all')
    parser.add_argument('--transform',type=Path)
    parser.add_argument('--geometry-guids',type=Path,help='JSON list of source GUIDs for priority geometry checks')
    args=parser.parse_args()
    if args.geometry_limit < 0:parser.error('Negative geometry limit')
    selected=set(json.loads(args.geometry_guids.read_text(encoding='utf-8'))) if args.geometry_guids else None
    print(json.dumps(audit(args.ifc,args.identity,args.out,args.geometry_limit,args.transform,selected),ensure_ascii=False,indent=2,default=str))
