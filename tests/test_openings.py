import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import write_json
from prepare_openings import prepare


class StandaloneOpeningTests(unittest.TestCase):
    def fixture(self,folder,ambiguous=False):
        import ifcopenshell
        from ifcopenshell.guid import new
        model=ifcopenshell.file(schema='IFC4')
        point=model.createIfcCartesianPoint((0.,0.,0.));axis=model.createIfcAxis2Placement3D(point)
        context=model.createIfcGeometricRepresentationContext(None,'Model',3,1e-5,axis,None)
        profile=model.createIfcRectangleProfileDef('AREA',None,None,1.,1.)
        solid=model.createIfcExtrudedAreaSolid(profile,axis,model.createIfcDirection((0.,0.,1.)),1.)
        representation=model.createIfcShapeRepresentation(context,'Body','SweptSolid',[solid])
        shape=model.createIfcProductDefinitionShape(None,None,[representation])
        host_guid='11111111-1111-4111-8111-111111111111';opening_guid='22222222-2222-4222-8222-222222222222'
        host=model.create_entity('IfcSlab',GlobalId=new(),Tag=host_guid)
        opening=model.create_entity('IfcOpeningElement',GlobalId=new(),Tag=opening_guid,Representation=shape)
        model.create_entity('IfcRelVoidsElement',GlobalId=new(),RelatingBuildingElement=host,RelatedOpeningElement=opening)
        if ambiguous:
            other=model.create_entity('IfcSlab',GlobalId=new())
            model.create_entity('IfcRelVoidsElement',GlobalId=new(),RelatingBuildingElement=other,RelatedOpeningElement=opening)
        ifc=folder/'model.ifc';model.write(str(ifc))
        manifest=dict(source=dict(projectId='project'),coordinates={},elements=[
            dict(sourceGuid=host_guid,kind='Slab',levelKey='story:0',mark='',raw={},payload={}),
            dict(sourceGuid=opening_guid,kind='Opening',levelKey='story:0',mark='Niche',raw=dict(details=dict(error='Not yet supported element type')),payload={})])
        path=folder/'manifest.json';write_json(path,manifest)
        frame=dict(matrix=[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]])
        return path,ifc,frame,host_guid

    def test_ifc_identity_and_void_relation_recover_an_unsupported_source_opening(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);manifest,ifc,frame,host=self.fixture(folder)
            summary=prepare(folder/'out',manifest_path=manifest,ifc_path=ifc,frame=frame)
            self.assertEqual(summary['statuses'],{'ready':1})
            row=json.loads((folder/'out/openings.json').read_text(encoding='utf-8'))['elements'][0]
            self.assertEqual(row['hostGuid'],host);self.assertTrue(row['emptyOpening']);self.assertEqual(row['opening']['mode'],'void')
            self.assertEqual(row['kind'],'Opening');self.assertEqual(len(row['voidGeometry']['faces']),12)
            self.assertAlmostEqual(row['voidGeometry']['parts'][0]['approximateVolumeM3'],1.)

    def test_ambiguous_void_host_is_blocked_instead_of_using_proximity(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);manifest,ifc,frame,_=self.fixture(folder,True)
            summary=prepare(folder/'out',manifest_path=manifest,ifc_path=ifc,frame=frame)
            self.assertEqual(summary['statuses'],{'blocked':1})
            row=json.loads((folder/'out/openings.json').read_text(encoding='utf-8'))['elements'][0]
            self.assertIn('unique explicit source host',row['reason'])

    def test_identified_physical_filling_survives_missing_void_relation(self):
        import ifcopenshell
        from ifcopenshell.guid import new
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);manifest,ifc,frame,host=self.fixture(folder)
            model=ifcopenshell.open(str(ifc));opening=model.by_type('IfcOpeningElement')[0]
            door_guid='33333333-3333-4333-8333-333333333333'
            model.create_entity('IfcDoor',GlobalId=new(),Tag=door_guid,Representation=opening.Representation)
            model.write(str(ifc))
            data=json.loads(manifest.read_text(encoding='utf-8'))
            data['elements']=[data['elements'][0],dict(sourceGuid=door_guid,kind='Door',levelKey='story:0',mark='D',raw={},payload=dict(hostGuid=host))]
            write_json(manifest,data)
            summary=prepare(folder/'out',manifest_path=manifest,ifc_path=ifc,frame=frame)
            self.assertEqual(summary['statuses'],{'fillingOnly':1})
            row=json.loads((folder/'out/openings.json').read_text(encoding='utf-8'))['elements'][0]
            self.assertEqual(len(row['faces']),12)
            self.assertIn('no unique IFC void relation',row['reason'])
            self.assertNotIn('voidGeometry',row)
            self.assertNotIn('opening',row)

    def filling_fixture(self,folder,ifc_class='IfcBuildingElementProxy',kind='Window',relation=True,duplicate=False,conflict=False):
        import ifcopenshell
        from ifcopenshell.guid import new
        manifest,ifc,frame,host=self.fixture(folder)
        model=ifcopenshell.open(str(ifc));opening=model.by_type('IfcOpeningElement')[0]
        key='33333333-3333-4333-8333-333333333333'
        obj=model.create_entity(ifc_class,GlobalId=new(),Tag=host if conflict else key,Representation=opening.Representation)
        if duplicate:model.create_entity(ifc_class,GlobalId=new(),Tag=key,Representation=opening.Representation)
        if relation:model.create_entity('IfcRelFillsElement',GlobalId=new(),RelatingOpeningElement=opening,RelatedBuildingElement=obj)
        model.write(str(ifc))
        data=json.loads(manifest.read_text(encoding='utf-8'))
        data['elements'][0].update(kind='Wall',raw=dict(details=dict(begCoordinate=dict(x=-1,y=0),endCoordinate=dict(x=2,y=0),zCoordinate=0)))
        data['elements']=[data['elements'][0],dict(sourceGuid=key,kind=kind,levelKey='story:0',mark='W',
            raw=dict(ifcIdentifiers=dict(ifcId=obj.GlobalId)) if not duplicate else {},payload=dict(hostGuid=host))]
        write_json(manifest,data)
        prepare(folder/'out',manifest_path=manifest,ifc_path=ifc,frame=frame)
        return json.loads((folder/'out/openings.json').read_text(encoding='utf-8'))['elements'][0]

    def test_explicit_neutral_proxy_keeps_source_category_and_exact_void(self):
        for kind in ('Window','Door'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                row=self.filling_fixture(Path(temp),kind=kind)
                self.assertEqual(row['status'],'ready');self.assertEqual(row['kind'],kind)
                self.assertEqual(row['ifcClass'],'IfcBuildingElementProxy')
                self.assertIn('Exact Archicad '+kind,row['classificationAdaptation'])
                self.assertEqual(len(row['faces']),12);self.assertEqual(len(row['voidGeometry']['faces']),12)

    def test_neutral_proxy_without_relation_preserves_only_physical_filling(self):
        with tempfile.TemporaryDirectory() as temp:
            row=self.filling_fixture(Path(temp),relation=False)
            self.assertEqual(row['status'],'fillingOnly');self.assertNotIn('voidGeometry',row)
            self.assertIn('no unique IFC void relation',row['reason'])

    def test_wrong_semantic_ifc_class_is_still_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            row=self.filling_fixture(Path(temp),ifc_class='IfcDoor',kind='Window')
            self.assertEqual(row['status'],'blocked');self.assertNotIn('vertices',row)
            self.assertIn('different element class',row['reason'])

    def test_proxy_duplicate_or_conflicting_identity_is_blocked(self):
        for options in (dict(duplicate=True),dict(conflict=True)):
            with self.subTest(options=options),tempfile.TemporaryDirectory() as temp:
                row=self.filling_fixture(Path(temp),**options)
                self.assertEqual(row['status'],'blocked');self.assertNotIn('vertices',row)
