"""Recognise exact vertical prisms in IFC meshes; retain holes, parts and offsets.

No bounding-box profile inference: both caps, all vertical sides and volume must
describe the same polygon. Grouping is quantised to 1 um; actual loops stay intact.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon,LineString
from shapely.ops import unary_union
from shapely import set_precision

ROOT=Path(__file__).resolve().parents[1]


def recognise_part(vertices,faces,tolerance=1e-6):
    points=vertices[np.unique(faces)]
    lo,hi=float(points[:,2].min()),float(points[:,2].max())
    if hi-lo<=tolerance:raise ValueError('Zero extrusion height')
    if np.any(np.minimum(abs(points[:,2]-lo),abs(points[:,2]-hi))>tolerance):raise ValueError('Part has intermediate Z vertices')
    caps=[[],[]];sides=[]
    for face in faces:
        tri=vertices[face]
        if np.max(abs(tri[:,2]-lo))<=tolerance:caps[0].append(Polygon(tri[:,:2]))
        elif np.max(abs(tri[:,2]-hi))<=tolerance:caps[1].append(Polygon(tri[:,:2]))
        else:sides.append(tri)
    polygons=[unary_union([set_precision(p,1e-8) for p in cap]) for cap in caps]
    if any(p.is_empty or not p.is_valid or p.geom_type not in ('Polygon','MultiPolygon') for p in polygons):raise ValueError('Caps are not valid polygons')
    a,b=polygons
    if a.symmetric_difference(b).area>max(1e-9,a.area*1e-7) or a.boundary.hausdorff_distance(b.boundary)>tolerance:
        raise ValueError('Top/bottom profiles differ')
    for tri in sides:
        xy=tri[:,:2]
        u,v=xy[1]-xy[0],xy[2]-xy[0]
        if abs(u[0]*v[1]-u[1]*v[0])>tolerance*max(1,np.linalg.norm(u)):
            raise ValueError('Side face is not vertical')
        if not a.boundary.buffer(tolerance*2).covers(LineString(xy)):
            raise ValueError('Side face differs from cap boundary')
    local=vertices-vertices[faces[0,0]];tri=local[faces]
    volume=abs(float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6))
    if abs(volume-a.area*(hi-lo))>max(1e-6,volume*1e-5):raise ValueError('Prism volume differs from source')
    # Remove only numerical collinear vertices, at nanometre scale. Revit checks
    # its actual ShortCurveTolerance later; no mm-scale profile simplification.
    poly=a.simplify(1e-8,preserve_topology=True).normalize()
    if poly.symmetric_difference(a).area>1e-9:raise ValueError('Profile cleanup changed area')
    components=list(poly.geoms) if poly.geom_type=='MultiPolygon' else [poly]
    rings=[list(r.coords)[:-1] for component in components for r in [component.exterior,*component.interiors]]
    return {'loops':[[{'x':float(x),'y':float(y),'z':0} for x,y in ring] for ring in rings],
            'start':lo,'end':hi,'areaM2':float(poly.area),'volumeM3':volume}


def prepare(out, reference=None, already=None):
    if reference is None:raise ValueError('Explicit column body reference path is required')
    data=json.loads(reference.read_text(encoding='utf-8'))
    if already is None:already=set()
    rows,checks=[],[]
    for record in data['elements']:
        key=record['sourceGuid'];d=record['rawSource']['details'];check={'sourceGuid':key,'profiled':'profileId' in d}
        try:
            if key in already:raise ValueError('Already native')
            if d['isSlanted']:raise ValueError('Slanted member needs a slanted-axis adapter')
            angle=d['axisRotationAngle'];origin=np.array([d['origin']['x'],d['origin']['y'],d['zCoordinate']])
            rotation=np.array([[math.cos(angle),-math.sin(angle),0],[math.sin(angle),math.cos(angle),0],[0,0,1]])
            world=np.array([[p[a] for a in 'xyz'] for p in record['vertices']]);local=(world-origin)@rotation
            faces=np.array(record['faces']);parts=[]
            for p in record['parts']:
                if not p['closedEdgeCheck']:raise ValueError('Open IFC part')
                part=recognise_part(local,faces[p['firstFace']:p['firstFace']+p['faceCount']])
                part['topOffset']=part.pop('end')-d['height'];part['bottomOffset']=part.pop('start')
                part['materialKey']=record.get('materialKey','') if len(record['parts'])==1 else ''
                parts.append(part)
            shape=[{'loops':[[[round(p[a],6) for a in 'xy'] for p in loop] for loop in part['loops']],
                    'bottomOffset':round(part['bottomOffset'],6),'topOffset':round(part['topOffset'],6)} for part in parts]
            family_key=hashlib.sha256(json.dumps(shape,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            rows.append({'sourceGuid':key,'familyKey':family_key,'origin':dict(zip('xyz',origin.tolist())),
                         'angle':angle,'height':d['height'],'levelKey':record['levelKey'],'mark':record['mark'],
                         'parts':parts,'boundsMinMeters':record['boundsMinMeters'],'boundsMaxMeters':record['boundsMaxMeters'],
                         'referenceVertices':record['vertices'],'referenceFaces':record['faces'],
                         'volumeM3':sum(p['volumeM3'] for p in parts),'profileGuid':d.get('profileId',{}).get('guid',''),
                         'rawSource':record['rawSource']})
            check.update(status='ready',familyKey=family_key,parts=len(parts))
        except Exception as exc:check.update(status='retained',reason=str(exc))
        checks.append(check)
    out.mkdir(parents=True,exist_ok=False)
    manifest={'projectId':data['projectId'],'coordinates':data['coordinates'],'ifcPath':data['ifcPath'],'ifcSha256':data['ifcSha256'],'columns':rows}
    (out/'extrusions.json').write_text(json.dumps(manifest,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    summary={'sourceColumns':len(checks),'readyToReplace':len(rows),'profiledReady':sum(bool(r['profileGuid']) for r in rows),
             'families':len({r['familyKey'] for r in rows}),
             'retainedReasons':dict(collections.Counter(r.get('reason','ready') for r in checks))}
    (out/'preflight.json').write_text(json.dumps({'summary':summary,'columns':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--reference',type=Path,required=True);p.add_argument('--exclude-native-report',type=Path);a=p.parse_args()
    already=set()
    if a.exclude_native_report:
        report=json.loads(a.exclude_native_report.read_text(encoding='utf-8'))
        already={r['SourceKey'] for r in report['Items'] if r['Kind']=='Column' and r['Status']=='created'}
    print(json.dumps(prepare(a.out,a.reference,already),indent=2),flush=True)
