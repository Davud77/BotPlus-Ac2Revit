"""Generate a bounded native-Revit regression case for an IFC void in a floor."""
import argparse
from pathlib import Path
import sys
import uuid
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.geometry import loops
from ac2r.jsonio import write_json
from ac2r.normalize import digest
from prepare_openings import prepare


def build(out,filling_only=False):
    import ifcopenshell
    from ifcopenshell.guid import new
    out.mkdir(parents=True,exist_ok=False)
    project='33333333-3333-4333-8333-333333333333';host='11111111-1111-4111-8111-111111111111';hole='22222222-2222-4222-8222-222222222222'
    model=ifcopenshell.file(schema='IFC4');origin=model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.,0.,0.)))
    context=model.createIfcGeometricRepresentationContext(None,'Model',3,1e-5,origin,None)
    position=model.createIfcAxis2Placement3D(model.createIfcCartesianPoint((0.,0.,-.5)))
    solid=model.createIfcExtrudedAreaSolid(model.createIfcRectangleProfileDef('AREA',None,None,1.,1.),position,model.createIfcDirection((0.,0.,1.)),1.)
    representation=model.createIfcProductDefinitionShape(None,None,[model.createIfcShapeRepresentation(context,'Body','SweptSolid',[solid])])
    slab=model.create_entity('IfcSlab',GlobalId=new(),Tag=host)
    opening=model.create_entity('IfcDoor' if filling_only else 'IfcOpeningElement',GlobalId=new(),Tag=hole,Representation=representation)
    if not filling_only:model.create_entity('IfcRelVoidsElement',GlobalId=new(),RelatingBuildingElement=slab,RelatedOpeningElement=opening)
    model.write(str(out/'model.ifc'))
    footprint=loops(dict(polygonOutline=[dict(x=x,y=y) for x,y in [(-2,-2),(2,-2),(2,2),(-2,2)]]))
    elements=[dict(sourceGuid=host,kind='Slab',levelKey='story:0',mark='Floor',status='ready',dependencies=[],
                  raw={},payload=dict(typeKey='slab:fixture',loops=footprint,topOffset=0)),
              dict(sourceGuid=hole,kind='Opening',levelKey='story:0',mark='Opening',status='unsupported',dependencies=[],
                  raw=dict(details=dict(error='Not yet supported element type')),payload={})]
    for row in elements:row['contentHash']=digest(row)
    if filling_only:
        elements[1].update(kind='Door',status='ready',raw={},payload=dict(hostGuid=host))
        elements[1]['contentHash']=digest(elements[1])
    manifest=dict(schemaVersion='1.0.0',source=dict(projectId=project),units=dict(length='m',angle='rad'),
        coordinates=dict(transform=dict(rotationRadians=0,translation=dict(x=0,y=0,z=0))),
        levels=[dict(key='story:0',name='Opening fixture',elevation=0)],materials=[],
        types=[dict(key='slab:fixture',kind='Slab',name='Opening fixture floor 200',layers=[dict(width=.2,function='Core',materialKey='')])],elements=elements)
    manifest['manifestHash']=digest(manifest);write_json(out/'manifest.json',manifest)
    write_json(out/'mapping.json',dict(allowPartial=True,connectWalls=False,families={}))
    prepare(out/'openings',manifest_path=out/'manifest.json',ifc_path=out/'model.ifc',frame=dict(matrix=np.eye(4).tolist()))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--filling-only',action='store_true')
    a=p.parse_args();build(a.out.resolve(),a.filling_only)
