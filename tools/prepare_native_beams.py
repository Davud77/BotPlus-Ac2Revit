"""Select only beams whose complete IFC body is the nominal rectangular prism."""
import argparse
import collections
import json
import math
from pathlib import Path

import numpy as np
from prepare_extruded_columns import recognise_part


def check_rectangular_beam(record):
    d=record['rawSource']['details']
    if d['isSlanted'] or abs(d['arcAngle'])>1e-10 or abs(d['verticalCurveHeight'])>1e-10:
        raise ValueError('Sloped or curved axis requires another native adapter')
    if d.get('circleBased') or d['anchorPoint']!='TopCenter' or abs(d['offset'])>1e-10 or d.get('holes'):
        raise ValueError('Unsupported section or reference line')
    if abs(math.remainder(d['profileAngle'],2*math.pi))>1e-8:raise ValueError('Rotated section')
    if len(record['parts'])!=1 or not record['parts'][0]['closedEdgeCheck']:raise ValueError('Open or multipart IFC body')
    a,b=d['begCoordinate'],d['endCoordinate'];dx,dy=b['x']-a['x'],b['y']-a['y'];length=math.hypot(dx,dy)
    if not math.isfinite(length) or length<1e-5:raise ValueError('Invalid beam length')
    width,height=d['width'],d['height']
    if not all(math.isfinite(v) and v>0 for v in (width,height)):raise ValueError('Invalid section size')
    u=np.array([dx/length,dy/length,0]);v=np.array([-dy/length,dx/length,0]);w=np.array([0,0,1])
    origin=np.array([a['x'],a['y'],d['zCoordinate']])
    points=np.array([[p[c] for c in 'xyz'] for p in record['vertices']])
    local=(points-origin)@np.array([v,w,u]).T
    part=recognise_part(local,np.array(record['faces']))
    if len(part['loops'])!=1 or len(part['loops'][0])!=4:raise ValueError('IFC section is not a rectangle')
    if max(abs(local.min(axis=0)-[-width/2,-height,0]))>1e-6 or max(abs(local.max(axis=0)-[width/2,0,length]))>1e-6:
        raise ValueError('IFC prism differs from nominal reference line/section')
    if abs(part['volumeM3']-width*height*length)>max(1e-6,width*height*length*1e-5):raise ValueError('Nominal volume differs')
    return {'sourceGuid':record['sourceGuid'],'levelKey':record['levelKey'],'mark':record['mark'],
            'start':dict(zip('xyz',origin.tolist())), 'end':{'x':b['x'],'y':b['y'],'z':d['zCoordinate']},
            'width':width,'height':height,'volumeM3':part['volumeM3'],
            'materialKey':record['materialKey'],'boundsMinMeters':record['boundsMinMeters'],
            'boundsMaxMeters':record['boundsMaxMeters'],'rawSource':record['rawSource']}


def prepare(reference,out):
    data=json.loads(reference.read_text(encoding='utf-8'));ready,checks=[],[]
    for record in data['elements']:
        try:
            ready.append(check_rectangular_beam(record));checks.append({'sourceGuid':record['sourceGuid'],'status':'ready'})
        except ValueError as exc:checks.append({'sourceGuid':record['sourceGuid'],'status':'retained','reason':str(exc)})
    plan={k:data[k] for k in ('projectId','coordinates','ifcPath','ifcSha256')};plan['beams']=ready
    out.mkdir(parents=True,exist_ok=False)
    (out/'native-beams.json').write_text(json.dumps(plan,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    summary={'sourceBeams':len(checks),'nativeCandidates':len(ready),'outcomes':dict(collections.Counter(c.get('reason','ready') for c in checks))}
    (out/'preflight.json').write_text(json.dumps({'summary':summary,'beams':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('reference',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();print(json.dumps(prepare(a.reference,a.out),indent=2))
