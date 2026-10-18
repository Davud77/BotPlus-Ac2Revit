"""Normalize only explicit source roomSeparator geometry; retain coincident aliases."""
import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.geometry import curve, point


def source_curves(row,z):
    d=row['details']
    if d.get('roomSeparator') is not True:raise ValueError('No explicit roomSeparator=true')
    if row['type']=='Line':return [curve(point(d['begCoordinate'],z),point(d['endCoordinate'],z))]
    if row.get('isCircleFromInventory') is True:
        if abs(d['ratio']-1)>1e-10 or d['radius']<=0:raise ValueError('Elliptical or invalid circle requires its own adapter')
        x,y=d['origin']['x'],d['origin']['y'];r=d['radius']
        at=lambda a:{'x':x+r*math.cos(a),'y':y+r*math.sin(a),'z':z}
        return [{'kind':'arc','start':at(0),'mid':at(math.pi/2),'end':at(math.pi)},
                {'kind':'arc','start':at(math.pi),'mid':at(3*math.pi/2),'end':at(2*math.pi)}]
    if row['type']=='PolyLine':
        coords=[point(p,z) for p in d['coordinates']];arcs={}
        for a in d.get('arcs',[]):
            i,j=int(a['begIndex']),int(a['endIndex'])
            if j!=i+1 or not 0<=i<len(coords)-1 or i in arcs:raise ValueError('Invalid open/closed polyline arc index')
            arcs[i]=a['arcAngle']
        if len(coords)<2:raise ValueError('Polyline has fewer than two points')
        return [curve(a,b,arcs.get(i,0)) for i,(a,b) in enumerate(zip(coords,coords[1:]))]
    raise ValueError('Unsupported source curve; no inferred sweep or tessellation: '+row['type'])


def canonical(level,c):
    def p(value):return tuple(round(value[a],9) for a in 'xyz')
    a,b=sorted([p(c['start']),p(c['end'])])
    return json.dumps([level,c['kind'],a,b,p(c['mid']) if 'mid' in c else None],separators=(',',':'))


def build(source,manifest):
    if source['projectId']!=manifest['source']['projectId']:raise ValueError('Project mismatch')
    levels={l['key']:l for l in manifest['levels']};segments={};unsupported=[];source_ids=set()
    for row in source['elements']:
        guid=row['sourceGuid']
        if guid in source_ids:raise ValueError('Duplicate source identity')
        source_ids.add(guid)
        level='story:'+str(row['floorIndex'])
        try:
            if level not in levels:raise ValueError('Source story missing')
            z=levels[level]['elevation']
            if abs(row['details']['zCoordinate']-z)>1e-7:raise ValueError('2D elevation disagrees with home story')
            for i,c in enumerate(source_curves(row,z)):
                key='room-separator:'+hashlib.sha256(canonical(level,c).encode()).hexdigest()
                ref={'sourceGuid':guid,'edgeIndex':i}
                if key in segments:segments[key]['sourceRefs'].append(ref)
                else:segments[key]={'key':key,'levelKey':level,'curve':c,'sourceRefs':[ref]}
        except (ValueError,KeyError) as ex:unsupported.append({'sourceGuid':guid,'reason':str(ex)})
    return {'format':'ac2r.room-separators/1','projectId':source['projectId'],'sourceElements':source['elements'],
            'segments':list(segments.values()),'unsupported':unsupported,
            'aliasKeyPrecisionMeters':1e-9,'note':'Canonical keys collapse only coincident geometry; curve coordinates are not rounded or snapped.'}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    plan=build(json.loads(a.source.read_text(encoding='utf-8-sig')),json.loads(a.manifest.read_text(encoding='utf-8-sig')))
    plan['sourceManifestSha256']=hashlib.sha256(a.manifest.read_bytes()).hexdigest()
    plan['sourceEvidence']={'path':str(a.source.resolve()),'sha256':hashlib.sha256(a.source.read_bytes()).hexdigest()}
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as stream:json.dump(plan,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'source':len(plan['sourceElements']),'uniqueCurves':len(plan['segments']),'unsupported':len(plan['unsupported'])}))
