"""Verify persisted column GUIDs and all fallback triangles against the IFC reference."""
import argparse
import collections
import json
from pathlib import Path

import numpy as np


def surface_distance(probes,vertices,faces):
    """Maximum sampled point-to-triangle distance, including edge/vertex cases."""
    tri=vertices[faces];a,b,c=tri[:,0],tri[:,1],tri[:,2]
    ab,ac=b-a,c-a;normal=np.cross(ab,ac);norm2=np.einsum('ij,ij->i',normal,normal)
    good=norm2>1e-24;a,b,c,ab,ac,normal,norm2=(x[good] for x in (a,b,c,ab,ac,normal,norm2))
    if not len(a):return float('inf')
    worst=0.0
    for start in range(0,len(probes),128):
        point=probes[start:start+128,None,:];ap=point-a
        d00=np.einsum('ij,ij->i',ab,ab);d01=np.einsum('ij,ij->i',ab,ac);d11=np.einsum('ij,ij->i',ac,ac)
        d20=np.einsum('ntj,tj->nt',ap,ab);d21=np.einsum('ntj,tj->nt',ap,ac);den=d00*d11-d01*d01
        u=(d11*d20-d01*d21)/den;v=(d00*d21-d01*d20)/den
        plane=np.einsum('ntj,tj->nt',ap,normal)**2/norm2
        distance=np.where((u>=-1e-9)&(v>=-1e-9)&(u+v<=1+1e-9),plane,np.inf)
        for lo,hi in ((a,b),(b,c),(c,a)):
            direction=hi-lo;length=np.einsum('ij,ij->i',direction,direction)
            parameter=np.clip(np.einsum('ntj,tj->nt',point-lo,direction)/length,0,1)
            delta=point-(lo+parameter[:,:,None]*direction)
            distance=np.minimum(distance,np.einsum('ntj,ntj->nt',delta,delta))
        worst=max(worst,float(np.sqrt(distance.min(axis=1).max())))
    return worst


def probes(vertices,faces):
    tri=vertices[faces]
    return np.concatenate([vertices,tri.mean(axis=1),(tri[:,0]+tri[:,1])/2,(tri[:,1]+tri[:,2])/2,(tri[:,2]+tri[:,0])/2])


def compare(reference_path,native_path,out,kind='Column'):
    source=json.loads(reference_path.read_text(encoding='utf-8'))['elements']
    native=[json.loads(line) for line in native_path.read_text(encoding='utf-8').splitlines()]
    def unique(rows,label):
        keys=[r['sourceGuid'] for r in rows]
        if len(keys)!=len(set(keys)):raise ValueError('Duplicate '+label+' GUID')
        return {r['sourceGuid']:r for r in rows}
    original=unique(source,'source');actual=unique(native,'Revit');checks=[]
    for key,reference in original.items():
        row={'sourceGuid':key}
        if key not in actual:row['status']='missing';checks.append(row);continue
        result=actual[key];row['revitClass']=result['elementClass']
        if result['geometryStatus']!='extracted':row.update(status='geometryError',error=result.get('error',''));checks.append(row);continue
        points=np.array([[p[a] for a in 'xyz'] for p in reference['vertices']]);faces=np.array(reference['faces'])
        vertices=np.array(result['vertices']);triangles=np.array(result['faces'])
        delta=max(abs(vertices.min(axis=0)-points.min(axis=0)).max(),abs(vertices.max(axis=0)-points.max(axis=0)).max())
        row['boundsMaxDifferenceMeters']=float(delta)
        if result['elementClass']=='DirectShape':
            distances=np.linalg.norm(vertices[:,None,:]-points[None,:,:],axis=2)
            mapping=distances.argmin(axis=1);deviation=distances[np.arange(len(vertices)),mapping].max()
            # A canonical source-coordinate key collapses coincident vertices
            # belonging to separate parts while retaining triangle multiplicity.
            keys=[tuple(np.rint(p/1e-6).astype(np.int64)) for p in points]
            expected=collections.Counter(tuple(sorted(keys[i] for i in f)) for f in faces)
            reconstructed=collections.Counter(tuple(sorted(keys[mapping[i]] for i in f)) for f in triangles)
            row.update(maxVertexDifferenceMeters=float(deviation),sourceTriangles=len(faces),revitTriangles=len(triangles),
                       triangleMultisetsEqual=expected==reconstructed,
                       status='meshVerified' if delta<=.001 and deviation<=.001 and expected==reconstructed else 'meshMismatch')
        else:
            reference_volume=sum(p['approximateVolumeM3'] for p in reference['parts'])
            volume_delta=abs(result['nativeSolidVolumeM3']-reference_volume)
            surface=max(surface_distance(probes(points,faces),vertices,triangles),surface_distance(probes(vertices,triangles),points,faces))
            row.update(volumeDifferenceM3=float(volume_delta),sampledSurfaceDifferenceMeters=surface,
                       status='nativeSurfaceVolumeVerified' if delta<=.001 and surface<=.001 and volume_delta<=max(1e-6,reference_volume*1e-5) else 'nativeMismatch')
            # Native prisms were also checked before creation in prepare_stage_plan
            # and BasicColumnAdapter; differing triangulation is acceptable here.
        checks.append(row)
    summary={'source'+kind+'s':len(source),'revit'+kind+'CategoryElements':len(native),
             'statuses':dict(collections.Counter(r['status'] for r in checks)),
             'extraRevitSourceGuids':sorted(set(actual)-set(original)),
             'profiledSource'+kind+'s':sum('profileId' in r['rawSource'].get('details',{}) for r in source),
             'maxBoundsDifferenceMeters':max((r.get('boundsMaxDifferenceMeters',0) for r in checks),default=0),
             'maxMeshVertexDifferenceMeters':max((r.get('maxVertexDifferenceMeters',0) for r in checks),default=0),
             'maxNativeSampledSurfaceDifferenceMeters':max((r.get('sampledSurfaceDifferenceMeters',0) for r in checks),default=0),
             'note':'Mesh equality is checked after vertex correspondence within 1 mm. IFC tessellation is the reference; exact analytic BRep and full native parametric equivalence are not proven.'}
    out.mkdir(parents=True,exist_ok=False)
    (out/'comparison.json').write_text(json.dumps({'summary':summary,kind.lower()+'s':checks},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('reference',type=Path);p.add_argument('native',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--kind',choices=['Column','Beam','Door','Window'],default='Column')
    a=p.parse_args();print(json.dumps(compare(a.reference,a.native,a.out,a.kind),indent=2))
