"""Prepare explicit columns, IFC axes and surface graphics for a copied RVT.

Only centred, straight rectangular columns whose IFC net mesh is the expected
prism enter the native family trial. Profiles are never inferred from bounds.
IFC axis keys are derived UUIDs; they are not claimed to be Archicad axis GUIDs.
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'exporter'))
from ac2r.normalize import digest
from source_ifc import SourceIfcIndex
from ac2r.jsonio import file_sha256, write_json
from ifc_coordinates import infer_frame, apply_frame


def prism_check(details, vertices, faces, tolerance=0.001):
    import numpy as np
    w, d, h = (float(details[k]) for k in ('width', 'depth', 'height'))
    if min(w, d, h) <= 0 or not all(math.isfinite(v) for v in (w, d, h)):
        raise ValueError('Invalid column dimensions')
    a = details['axisRotationAngle']
    origin = [details['origin']['x'], details['origin']['y'], details['zCoordinate']]
    local = (vertices-np.asarray(origin)) @ np.array([[math.cos(a),-math.sin(a),0], [math.sin(a),math.cos(a),0], [0,0,1]])
    delta = max(abs(local.min(axis=0)-[-w/2,-d/2,0]).max(), abs(local.max(axis=0)-[w/2,d/2,h]).max())
    edges = collections.Counter(tuple(sorted((int(i),int(j)))) for f in faces for i,j in zip(f,np.roll(f,-1)))
    if not edges or any(count != 2 for count in edges.values()):
        raise ValueError('IFC prism mesh is not closed')
    triangles = local[faces]
    volume = abs(float(np.einsum('ij,ij->i',triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum()/6))
    if delta > tolerance or abs(volume-w*d*h) > max(1e-6,w*d*h*1e-5):
        raise ValueError(f'IFC differs from nominal prism: bounds {delta:.6g} m; volume {volume:.9g} vs {w*d*h:.9g} m3')
    # Every nondegenerate face must lie on one of the six expected planes.
    lo, hi = np.array([-w/2,-d/2,0]), np.array([w/2,d/2,h])
    for tri in triangles:
        if not any(np.max(np.abs(tri[:,i]-v)) <= tolerance for i in range(3) for v in (lo[i],hi[i])):
            raise ValueError('IFC has non-prismatic faces despite matching bounds/volume')
    return {'boundsMinMeters':vertices.min(axis=0).tolist(), 'boundsMaxMeters':vertices.max(axis=0).tolist(),
            'volumeM3':volume, 'boundsToleranceMeters':tolerance}


def axis_curve(axis, placement, scale, frame=None):
    import numpy as np
    curve = axis.AxisCurve
    if not curve.is_a('IfcPolyline'):
        raise ValueError('Axis curve requires an arc/spline adapter: '+curve.is_a())
    local = np.array([list(p.Coordinates)+( [0] if len(p.Coordinates)==2 else []) for p in curve.Points],dtype=float)
    world = (np.c_[local,np.ones(len(local))] @ placement.T)[:,:3]*scale
    if frame is not None: world=apply_frame(world,frame)
    if len(world)<2 or not np.isfinite(world).all():raise ValueError('Empty/nonfinite axis')
    vector = world[-1]-world[0]
    length = np.linalg.norm(vector)
    if length<=0.001 or abs(vector[2])>1e-6:raise ValueError('Short/nonhorizontal axis')
    if max(np.linalg.norm(np.cross(p-world[0],vector))/length for p in world)>1e-6:
        raise ValueError('Polyline axis is not straight')
    if not axis.SameSense:world=world[::-1]
    return {'kind':'line','start':dict(zip('xyz',world[0].tolist())), 'end':dict(zip('xyz',world[-1].tolist()))}


def prepare(manifest_path, ifc_path, out, ifc_model=None, column_bodies_path=None, frame=None):
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    import numpy as np
    out.mkdir(parents=True,exist_ok=False)
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    model=ifc_model if ifc_model is not None else ifcopenshell.open(str(ifc_path))
    scale=ifcopenshell.util.unit.calculate_unit_scale(model)
    project=manifest['source']['projectId']
    identities=SourceIfcIndex(manifest,model)
    frame=frame or infer_frame(manifest,model,identities)
    cached=None
    if column_bodies_path is not None:
        reference=json.loads(column_bodies_path.read_text(encoding='utf-8'))
        if reference['projectId']!=project or reference['ifcSha256']!=file_sha256(ifc_path):
            raise ValueError('Column body cache belongs to a different source snapshot')
        if reference.get('ifcToSource',{}).get('hash')!=frame['hash']:
            raise ValueError('Column body cache uses a different IFC coordinate frame')
        cached={r['sourceGuid']:r for r in reference['elements']}
    settings=ifcopenshell.geom.settings();settings.set('use-world-coords',True)
    settings.set('mesher-linear-deflection',0.00025)
    selected, checks, axes = [], [], []
    columns=[e for e in manifest['elements'] if e['kind']=='Column']
    def bodies(obj,seen=None):
        seen=set() if seen is None else seen
        if obj.id() in seen:raise ValueError('Cyclic IFC decomposition')
        seen.add(obj.id())
        reps=getattr(getattr(obj,'Representation',None),'Representations',[]) or []
        if any(r.RepresentationIdentifier=='Body' for r in reps):return [obj]
        children=[child for rel in getattr(obj,'IsDecomposedBy',[]) for child in rel.RelatedObjects if child.is_a('IfcProduct')]
        if not children:raise ValueError('No IFC body or decomposed column bodies')
        return [body for child in children for body in bodies(child,seen)]
    for n,e in enumerate(columns):
        d=e['payload'].get('details',{})
        row={'sourceGuid':e['sourceGuid']}
        try:
            if 'profileId' in d:raise ValueError('Profile column needs a profile/body adapter')
            if d.get('coreAnchor')!='Center' or d.get('isSlanted') or d.get('wrapping') or d.get('circleBased'):
                raise ValueError('Only centred unwrapped vertical rectangular columns are supported in this stage')
            matches=identities.matches(e)
            if len(matches)!=1:raise ValueError('IFC column Tag match is missing/ambiguous')
            obj=matches[0]
            if cached is not None:
                record=cached.get(e['sourceGuid'])
                if record is None or record['ifcGlobalId']!=obj.GlobalId:raise ValueError('Exact IFC column body unavailable in checked cache')
                vertices=np.array([[p[k] for k in 'xyz'] for p in record['vertices']]);faces=np.array(record['faces'],dtype=int)
            else:
                chunks,triangles,cursor=[],[],0
                for part in bodies(obj):
                    shape=ifcopenshell.geom.create_shape(settings,part)
                    v=apply_frame(np.array(shape.geometry.verts).reshape(-1,3),frame);f=np.array(shape.geometry.faces).reshape(-1,3)
                    chunks.append(v);triangles.append(f+cursor);cursor+=len(v)
                vertices=np.concatenate(chunks);faces=np.concatenate(triangles)
            placement=ifcopenshell.util.placement.get_local_placement(obj.ObjectPlacement)
            angle=math.atan2(placement[1,0],placement[0,0])+frame['rotationRadians']
            native_details=dict(d,axisRotationAngle=angle)
            reference=prism_check(native_details,vertices,faces)
            copy=json.loads(json.dumps(e));copy['payload']['columnReference']=reference
            copy['payload']['details']['axisRotationAngle']=angle
            copy['payload']['columnReference']['sourceApiRotationRadians']=d['axisRotationAngle']
            copy['payload']['columnReference']['nativeOrientationSource']='IFC ObjectPlacement; full net prism verified'
            copy['payload']['columnReference']['ifcGlobalId']=obj.GlobalId
            copy['contentHash']=digest({k:v for k,v in copy.items() if k!='contentHash'})
            selected.append(copy);row.update(status='validatedPrism',**reference)
        except Exception as exc:row.update(status='deferred',reason=str(exc))
        checks.append(row)
        if (n+1)%100==0:print('Column checks',n+1,'/',len(columns),flush=True)
    for grid in model.by_type('IfcGrid'):
        placement=ifcopenshell.util.placement.get_local_placement(grid.ObjectPlacement)
        for group in ('UAxes','VAxes','WAxes'):
            for index,axis in enumerate(getattr(grid,group,None) or []):
                label=axis.AxisTag or ''
                key=str(uuid.uuid5(uuid.UUID(project),f'IFC:{grid.GlobalId}:{group}:{index}:{label}')).upper()
                row={'sourceGuid':key,'gridGlobalId':grid.GlobalId,'group':group,'axisTag':label,'axisOrdinal':index,
                     'identityNote':'Derived UUID; original Archicad axis GUID is not exposed by this IFC representation'}
                try:row.update(status='ready',curve=axis_curve(axis,placement,scale,frame))
                except Exception as exc:row.update(status='deferred',reason=str(exc))
                axes.append(row)
    ready=[a for a in axes if a['status']=='ready']
    labels=collections.Counter(a['axisTag'] for a in ready)
    for a in ready:
        # Never invent a bare name silently when separate grids repeat a label.
        a['revitName']=a['axisTag'] if a['axisTag'] and labels[a['axisTag']]==1 else f'{a["axisTag"] or "Axis"} [IFC {a["gridGlobalId"][:8]} {a["group"][0]}{a["axisOrdinal"]+1}]'
        payload={'curve':a['curve'],'name':a['revitName'],'sourceLabel':a['axisTag'],'ifcGridGlobalId':a['gridGlobalId'],
                 'identityNote':a['identityNote']}
        selected.append({'sourceGuid':a['sourceGuid'],'kind':'Grid','levelKey':manifest['levels'][0]['key'],
                         'mark':a['axisTag'],'dependencies':[],'status':'ready','raw':a,'payload':payload,'contentHash':digest(payload)})
    # A fresh project must never inherit Abakan's surfaces.
    surfaces=manifest.get('surfaces',[])
    manifest['elements']=selected;manifest['types']=[];manifest['surfaces']=surfaces
    manifest['source']['scope']+='; staged missing columns and IFC axes; reference geometry checked'
    manifest['referenceIFC']={'path':str(ifc_path.resolve()),'sha256':file_sha256(ifc_path),
                              'ifcLengthUnitToMeters':scale,'coordinateAssumption':'IFC world coordinates must match Archicad project coordinates; column prism checks enforce this for selected columns'}
    manifest.pop('manifestHash',None);manifest['manifestHash']=digest(manifest)
    write_json(out/'manifest.json',manifest)
    summary={'sourceColumns':len(columns),'validatedNativePrisms':sum(r['status']=='validatedPrism' for r in checks),
             'columnReasons':dict(collections.Counter(r.get('reason','validatedPrism') for r in checks)),
             'ifcGridGroups':len(model.by_type('IfcGrid')),'axisRecords':len(axes),'readyAxes':len(ready),
             'surfaces':len(surfaces),'geometryEqualityProven':False}
    (out/'preflight.json').write_text(json.dumps({'summary':summary,'columns':checks,'axes':axes},ensure_ascii=False,indent=2),encoding='utf-8')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,default=ROOT/'artifacts/runtime/geometry-offsets/manifest.json')
    parser.add_argument('--ifc',type=Path,default=ROOT/'artifacts/reference-exchange/exports/Abakan_brep_all_materials_20261014.ifc')
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    print(json.dumps(prepare(args.manifest,args.ifc,args.out),ensure_ascii=False,indent=2),flush=True)
