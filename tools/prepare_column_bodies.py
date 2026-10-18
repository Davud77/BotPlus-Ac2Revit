"""Extract every source column's IFC bodies, including complex profiles and parts.

The result is a geometric reference/fallback, not a parametric family mapping.
No column is approximated by a box. API nominal data and IFC net geometry remain
separate so finish/connection bodies are not silently interpreted as a core.
"""
import argparse
import collections
import hashlib
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from source_ifc import SourceIfcIndex
from ac2r.jsonio import file_sha256, write_json
from ifc_coordinates import infer_frame, apply_frame

def body_products(obj,seen=None,source_owner=None,owner_key=None):
    seen=set() if seen is None else seen
    if source_owner is not None:
        key=source_owner(obj)
        if key and key!=owner_key:return []
    if obj.id() in seen:raise ValueError('Cyclic IFC decomposition')
    seen.add(obj.id())
    reps=getattr(getattr(obj,'Representation',None),'Representations',[]) or []
    if any(r.RepresentationIdentifier=='Body' for r in reps):return [obj]
    children=[child for rel in getattr(obj,'IsDecomposedBy',[]) for child in rel.RelatedObjects if child.is_a('IfcProduct')]
    if not children:raise ValueError('No body or decomposed column bodies')
    return [body for child in children for body in body_products(child,seen,source_owner,owner_key)]


def prepare(manifest_path,ifc_path,out,kind='Column',keys=None,ifc_model=None,frame=None,manifest=None,identities=None,ifc_sha=None,meshes=None):
    import ifcopenshell
    import ifcopenshell.geom
    import numpy as np
    out.mkdir(parents=True,exist_ok=False)
    m=manifest if manifest is not None else json.loads(manifest_path.read_text(encoding='utf-8'));model=ifc_model if ifc_model is not None else ifcopenshell.open(str(ifc_path))
    identities=identities or SourceIfcIndex(m,model)
    frame=frame or infer_frame(m,model,identities)
    if kind not in ('Column','Beam','Wall','Object','Roof','Shell','Mesh','Morph','Stair','Railing','CurtainWall','Skylight','Lamp'):raise ValueError('Unsupported physical kind')
    settings=ifcopenshell.geom.settings();settings.set('use-world-coords',True);settings.set('mesher-linear-deflection',0.00025)
    rows,checks=[],[]
    source=[e for e in m['elements'] if e['kind']==kind and (keys is None or e['sourceGuid'].upper() in keys)]
    for n,e in enumerate(source):
        key=e['sourceGuid'];d=e['payload'].get('details',e['raw'].get('details',{}));check={'sourceGuid':key,'profiled':'profileId' in d}
        try:
            matches=identities.matches(e)
            if len(matches)!=1:raise ValueError('IFC Tag match missing/ambiguous')
            obj=matches[0];vertices,faces,parts,cursor=[],[],[],0
            for part in body_products(obj,source_owner=identities.source_guid,owner_key=key.upper()):
                if meshes is not None and part.id() in meshes:
                    raw_points,triangles=meshes[part.id()]
                else:
                    shape=ifcopenshell.geom.create_shape(settings,part)
                    raw_points=np.array(shape.geometry.verts).reshape(-1,3);triangles=np.array(shape.geometry.faces,dtype=int).reshape(-1,3)
                points=apply_frame(raw_points,frame)
                if len(points)==0 or len(triangles)==0 or not np.isfinite(points).all():raise ValueError('Empty/nonfinite body')
                tri=points[triangles]-points[0]
                volume=abs(float(np.einsum('ij,ij->i',tri[:,0],np.cross(tri[:,1],tri[:,2])).sum()/6))
                edges=collections.Counter(tuple(sorted((int(i),int(j)))) for f in triangles for i,j in zip(f,np.roll(f,-1)))
                parts.append({'ifcStepId':part.id(),'firstFace':sum(len(f) for f in faces),'faceCount':len(triangles),
                              'closedEdgeCheck':all(v==2 for v in edges.values()),'approximateVolumeM3':volume})
                vertices.append(points);faces.append(triangles+cursor);cursor+=len(points)
            points=np.concatenate(vertices);triangles=np.concatenate(faces)
            row={'sourceGuid':key,'ifcGlobalId':obj.GlobalId,'kind':kind,'levelKey':e['levelKey'],'mark':e['mark'],
                 'vertices':[dict(zip('xyz',p)) for p in points.tolist()],'faces':triangles.tolist(),'parts':parts,
                 'boundsMinMeters':points.min(axis=0).tolist(),'boundsMaxMeters':points.max(axis=0).tolist(),
                 'reason':'Complex profile/net body' if 'profileId' in d else 'Unmapped or net geometry differs from nominal core',
                 'ifcClass':obj.is_a(),
                 'rawSource':e['raw'],'representation':'tessellatedNetIFC',
                 'materialKey':d.get('buildingMaterialId',{}).get('guid','')}
            rows.append(row);check.update(status='extracted',ifcGlobalId=obj.GlobalId,parts=len(parts),triangles=len(triangles),
                                          allPartsClosed=all(p['closedEdgeCheck'] for p in parts))
        except Exception as exc:check.update(status='error',error=str(exc))
        if (n+1)%100==0:print(kind+' bodies',n+1,'/',len(source),flush=True)
        checks.append(check)
    result={'projectId':m['source']['projectId'],'coordinates':m['coordinates'],'ifcPath':str(ifc_path.resolve()),
            'ifcSha256':ifc_sha or file_sha256(ifc_path),'elements':rows,'ifcToSource':frame,
            'identityMethod':'Explicit Archicad IFC identifier = GlobalId, or exact IFC Tag = source GUID; no nearest-neighbour matching',
            'geometryNote':'IFC tessellation includes cuts/parts. Native family functionality and texture mapping are separate stages.'}
    write_json(out/(kind.lower()+'-bodies.json'),result,compact=True)
    summary={'source'+kind+'s':len(source),'statuses':dict(collections.Counter(c['status'] for c in checks)),
             'profiledExtracted':sum(c.get('profiled') and c['status']=='extracted' for c in checks),
             'withMultipleParts':sum(c.get('parts',0)>1 for c in checks),'allPartsClosed':sum(c.get('allPartsClosed',False) for c in checks)}
    if kind in ('Column','Beam','Wall'):summary['ifc'+kind+'s']=len(model.by_type('Ifc'+kind))
    (out/'audit.json').write_text(json.dumps({'summary':summary,kind.lower()+'s':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True)
    p.add_argument('--ifc',type=Path,required=True)
    p.add_argument('--kind',choices=['Column','Beam','Wall'],default='Column')
    p.add_argument('--keys-file',type=Path)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    from ifc_reader_wait import wait_for_reader
    wait_for_reader(ROOT)
    keys={k.upper() for k in json.loads(a.keys_file.read_text(encoding='utf-8'))} if a.keys_file else None
    print(json.dumps(prepare(a.manifest,a.ifc,a.out,a.kind,keys),indent=2),flush=True)
