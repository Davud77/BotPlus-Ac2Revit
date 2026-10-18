"""Join source doors/windows to exact IFC fillings and independently checked wall voids."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path

import numpy as np
from prepare_column_bodies import body_products
from prepare_extruded_columns import recognise_part
from source_ifc import SourceIfcIndex
from ac2r.jsonio import file_sha256, write_json
from ifc_coordinates import infer_frame, apply_frame

ROOT=Path(__file__).resolve().parents[1]


def prepare(out,limit=0,manifest_path=None,ifc_path=None,ifc_model=None,frame=None):
    import ifcopenshell
    import ifcopenshell.geom
    m=json.loads((manifest_path or ROOT/'artifacts/runtime/geometry-offsets/manifest.json').read_text(encoding='utf-8'))
    path=ifc_path or ROOT/'artifacts/reference-exchange/exports/Abakan_brep_all_materials_20261014.ifc'
    model=ifc_model if ifc_model is not None else ifcopenshell.open(str(path));settings=ifcopenshell.geom.settings()
    settings.set('use-world-coords',True);settings.set('mesher-linear-deflection',.00025)
    source={e['sourceGuid']:e for e in m['elements']};identities=SourceIfcIndex(m,model)
    frame=frame or infer_frame(m,model,identities)
    voids=collections.defaultdict(list)
    for obj in model.by_type('IfcOpeningElement'):
        if not obj.HasFillings:
            for rel in obj.VoidsElements:
                key=identities.source_guid(rel.RelatingBuildingElement)
                if key:voids[key].append(obj)
    geometry_cache={}
    def mesh(obj):
        if obj.id() in geometry_cache:return geometry_cache[obj.id()]
        points,faces,parts,cursor=[],[],[],0
        for item in body_products(obj):
            shape=ifcopenshell.geom.create_shape(settings,item)
            v=apply_frame(np.array(shape.geometry.verts).reshape(-1,3),frame);f=np.array(shape.geometry.faces,dtype=int).reshape(-1,3)
            if not len(f) or not np.isfinite(v).all():raise ValueError('Empty/nonfinite IFC body')
            tri=v[f]-v[0];volume=abs(float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6))
            edges=collections.Counter(tuple(sorted((int(a),int(b)))) for face in f for a,b in zip(face,np.roll(face,-1)))
            parts.append({'ifcStepId':item.id(),'firstFace':sum(len(x) for x in faces),'faceCount':len(f),
                          'approximateVolumeM3':volume,'closedEdgeCheck':all(n==2 for n in edges.values())})
            points.append(v);faces.append(f+cursor);cursor+=len(v)
        v,f=np.concatenate(points),np.concatenate(faces)
        result={'vertices':[dict(zip('xyz',p)) for p in v.tolist()],'faces':f.tolist(),'parts':parts,
                'boundsMinMeters':v.min(axis=0).tolist(),'boundsMaxMeters':v.max(axis=0).tolist()}
        geometry_cache[obj.id()]=result;return result
    def rectangle(obj,wall):
        g=mesh(obj);d=wall['raw']['details'];a,b=d['begCoordinate'],d['endCoordinate']
        dx,dy=b['x']-a['x'],b['y']-a['y'];length=math.hypot(dx,dy)
        if abs(d.get('arcAngle',0))>1e-10 or length<1e-6:raise ValueError('Curved/short wall requires another opening adapter')
        u=np.array([dx/length,dy/length,0]);n=np.array([-dy/length,dx/length,0]);origin=np.array([a['x'],a['y'],d['zCoordinate']])
        world=np.array([[p[c] for c in 'xyz'] for p in g['vertices']]);local=(world-origin)@np.array([u,[0,0,1],n]).T
        lo,hi=local.min(axis=0),local.max(axis=0)
        mode='rectangle'
        try:
            prism=recognise_part(local,np.array(g['faces']))
            if len(prism['loops'])!=1 or len(prism['loops'][0])!=4 or abs(prism['areaM2']-(hi[0]-lo[0])*(hi[1]-lo[1]))>1e-7:mode='void'
        except ValueError:mode='void'
        return {'sourceStart':dict(zip('xyz',(origin+u*lo[0]+[0,0,lo[1]]).tolist())),
                'sourceEnd':dict(zip('xyz',(origin+u*hi[0]+[0,0,hi[1]]).tolist())),
                'uMin':float(lo[0]),'uMax':float(hi[0]),'zMin':float(lo[1]+origin[2]),'zMax':float(hi[1]+origin[2]),
                'yMin':float(lo[2]),'yMax':float(hi[2]),'width':float(hi[0]-lo[0]),'height':float(hi[1]-lo[1]),
                'ifcGlobalId':obj.GlobalId,'hostGuid':wall['sourceGuid'],'mode':mode,
                'volumeM3':sum(p['approximateVolumeM3'] for p in g['parts'])}
    selected=[e for e in m['elements'] if e['kind'] in ('Door','Window')]
    if limit:selected=selected[:limit]
    records,checks=[],[];used_voids=set()
    for i,e in enumerate(selected):
        row={'sourceGuid':e['sourceGuid'],'kind':e['kind'],'levelKey':e['levelKey'],'mark':e['mark'],
             'rawSource':e['raw'],'payload':e['payload']}
        try:
            row['hostGuid']=e['payload']['hostGuid']
            wall=source[row['hostGuid']];matches=identities.matches(e)
            if len(matches)>1:raise ValueError('Ambiguous IFC filling Tag')
            if matches:
                obj=matches[0];rels=list(obj.FillsVoids)
                if len(rels)!=1:raise ValueError('Filling has no unique IFC void relation')
                opening=rels[0].RelatingOpeningElement
                hosts=[identities.source_guid(r.RelatingBuildingElement) for r in opening.VoidsElements]
                if hosts!=[row['hostGuid']]:raise ValueError('IFC host differs from source host')
                row.update(mesh(obj));row['kind']=e['kind'];row['ifcGlobalId']=obj.GlobalId
                row['identityMethod']='Explicit source IFC identifiers or exact GUID Tag, and FillsVoids/VoidsElements relations'
                row['opening']=rectangle(opening,wall)
            else:
                # Empty library openings have no IfcDoor filling. Require the
                # exact wall GUID plus a unique complete rectangular void match.
                p=e['payload'];candidates=[]
                for opening in voids[row['hostGuid']]:
                    try:
                        rect=rectangle(opening,wall)
                        difference=max(abs((rect['uMin']+rect['uMax'])/2-p['centerOffset']),abs(rect['width']-p['width']),
                                       abs(rect['height']-p['height']),abs(rect['zMin']-(wall['raw']['details']['zCoordinate']+p['sillHeight'])))
                        if difference<=.001:candidates.append((opening,rect))
                    except ValueError:pass
                if len(candidates)!=1:raise ValueError('Empty opening has no unique host/complete-geometry match: '+str(len(candidates)))
                opening,rect=candidates[0];row['opening']=rect;row['emptyOpening']=True
                row['identityMethod']='Exact source host GUID plus unique full rectangular void geometry within 1 mm; not an original IFC Tag match'
            if opening.id() in used_voids:raise ValueError('Two source records would use the same IFC void')
            used_voids.add(opening.id());row['voidGeometry']=mesh(opening);row['status']='ready'
        except Exception as exc:row.update(status='blocked',reason=str(exc))
        records.append(row);checks.append({k:row[k] for k in ('sourceGuid','kind','status','reason') if k in row})
        if (i+1)%100==0:print('Openings',i+1,'/',len(selected),flush=True)
    out.mkdir(parents=True,exist_ok=False)
    data={'projectId':m['source']['projectId'],'coordinates':m['coordinates'],'ifcPath':str(path),'ifcSha256':file_sha256(path),'elements':records,'ifcToSource':frame}
    write_json(out/'openings.json',data,compact=True)
    summary={'source':len(records),'statuses':dict(collections.Counter(r['status'] for r in records)),
             'emptyReady':sum(r.get('emptyOpening',False) and r['status']=='ready' for r in records),
             'physicalReady':dict(collections.Counter(r['kind'] for r in records if r['status']=='ready' and not r.get('emptyOpening'))),
             'blockedReasons':dict(collections.Counter(r['reason'] for r in records if r['status']=='blocked'))}
    (out/'preflight.json').write_text(json.dumps({'summary':summary,'elements':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--limit',type=int,default=0);a=p.parse_args()
    print(json.dumps(prepare(a.out,a.limit),indent=2),flush=True)
