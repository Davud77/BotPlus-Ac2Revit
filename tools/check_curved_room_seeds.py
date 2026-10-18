"""Independent dense-polygon QA of analytic placement seeds; never changes contours."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import numpy as np
from shapely.geometry import Point,Polygon

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.normalize import normalize


def sample(edges,tolerance=1e-6):
    points=[]
    for edge in edges:
        a=edge['start'];points.append((a['x'],a['y']))
        if edge['kind']=='line':continue
        origin=np.array(points[-1]);b=np.array([edge['end'][k] for k in 'xy'])-origin
        m=np.array([edge['mid'][k] for k in 'xy'])-origin
        center=np.linalg.solve(2*np.array([b,m]),np.array([b@b,m@m]));radius=float(np.linalg.norm(center))
        start=math.atan2(-center[1],-center[0]);finish=math.atan2(b[1]-center[1],b[0]-center[0]);mid=math.atan2(m[1]-center[1],m[0]-center[0])
        sweep=(finish-start)%(2*math.pi)
        if (mid-start)%(2*math.pi)>sweep:sweep-=2*math.pi
        steps=max(2,math.ceil(abs(sweep)/max(1e-6,2*math.acos(max(-1,1-tolerance/radius)))))
        for i in range(1,steps):
            angle=start+sweep*i/steps;p=origin+center+radius*np.array([math.cos(angle),math.sin(angle)])
            points.append(tuple(p))
    return points


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    snapshot=json.loads(a.snapshot.read_text(encoding='utf-8'));old=json.loads(a.manifest.read_text(encoding='utf-8'))
    current=normalize(snapshot,old['source']['projectId']);before={e['sourceGuid']:e for e in old['elements']}
    changed=[]
    for e in current['elements']:
        previous=before[e['sourceGuid']]
        if e['contentHash']==previous['contentHash']:continue
        if e['kind']!='Zone' or previous.get('reason')!='Zone arcs require an exact inside-point adapter' or e['status']!='ready':
            raise ValueError('Unexpected normalization change: '+e['sourceGuid'])
        rings=e['payload']['loops'];polygon=Polygon(sample(rings[0]),[sample(h) for h in rings[1:]])
        seed=e['payload']['point'];point=Point(seed['x'],seed['y'])
        if not polygon.is_valid or not polygon.contains(point) or polygon.boundary.distance(point)<=1e-5:
            raise ValueError('Independent inside-point QA failed: '+e['sourceGuid'])
        changed.append({'sourceGuid':e['sourceGuid'],'point':seed,'sourceArcs':sum(c['kind']=='arc' for r in rings for c in r),
            'independentClearanceMeters':polygon.boundary.distance(point),'sampleSagittaMeters':1e-6})
    for key in ('levels','types','materials','coordinates','source','fills','penTables'):
        if current[key]!=old[key]:raise ValueError('Non-room resource changed: '+key)
    result={'changedRooms':len(changed),'unchangedElements':len(current['elements'])-len(changed),
            'previousManifestSha256':hashlib.sha256(a.manifest.read_bytes()).hexdigest(),
            'snapshotSha256':hashlib.sha256(a.snapshot.read_bytes()).hexdigest(),
            'scope':'Placement seeds only; analytic source arcs are retained. Actual Revit room boundary/area acceptance is separate.',
            'rooms':changed}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='rooms'}))
