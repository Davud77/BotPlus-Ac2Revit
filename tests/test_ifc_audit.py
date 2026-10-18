import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from audit_ifc_walls import audit


class IfcAuditTests(unittest.TestCase):
    def fixture(self, folder):
        import ifcopenshell
        model=ifcopenshell.file(schema='IFC4')
        create=model.create_entity
        def pt(*coords):return create('IfcCartesianPoint',Coordinates=tuple(float(c) for c in coords))
        origin=create('IfcAxis2Placement3D',Location=pt(0,0,0))
        context=create('IfcGeometricRepresentationContext',ContextIdentifier='Model',ContextType='Model',CoordinateSpaceDimension=3,Precision=1e-5,WorldCoordinateSystem=origin)
        unit=create('IfcSIUnit',UnitType='LENGTHUNIT',Prefix='MILLI',Name='METRE')
        create('IfcProject',GlobalId=ifcopenshell.guid.new(),RepresentationContexts=[context],UnitsInContext=create('IfcUnitAssignment',Units=[unit]))
        profile=create('IfcRectangleProfileDef',ProfileType='AREA',Position=create('IfcAxis2Placement2D',Location=pt(2500,150)),XDim=5000.,YDim=300.)
        solid=create('IfcExtrudedAreaSolid',SweptArea=profile,Position=origin,ExtrudedDirection=create('IfcDirection',DirectionRatios=(0.,0.,1.)),Depth=3000.)
        representation=create('IfcShapeRepresentation',ContextOfItems=context,RepresentationIdentifier='Body',RepresentationType='SweptSolid',Items=[solid])
        product=create('IfcProductDefinitionShape',Representations=[representation])
        placement=create('IfcLocalPlacement',RelativePlacement=create('IfcAxis2Placement3D',Location=pt(10000,20000,0)))
        wall=create('IfcWall',GlobalId=ifcopenshell.guid.new(),Name='Test wall',ObjectPlacement=placement,Representation=product)
        # The gross wall is 4.5 m3; a 1 x 0.3 x 1 m opening makes its net volume 4.2 m3.
        hole_profile=create('IfcRectangleProfileDef',ProfileType='AREA',Position=create('IfcAxis2Placement2D',Location=pt(1500,150)),XDim=1000.,YDim=300.)
        hole_solid=create('IfcExtrudedAreaSolid',SweptArea=hole_profile,Position=create('IfcAxis2Placement3D',Location=pt(0,0,1000)),ExtrudedDirection=create('IfcDirection',DirectionRatios=(0.,0.,1.)),Depth=1000.)
        hole_rep=create('IfcShapeRepresentation',ContextOfItems=context,RepresentationIdentifier='Body',RepresentationType='SweptSolid',Items=[hole_solid])
        opening=create('IfcOpeningElement',GlobalId=ifcopenshell.guid.new(),ObjectPlacement=placement,Representation=create('IfcProductDefinitionShape',Representations=[hole_rep]))
        create('IfcRelVoidsElement',GlobalId=ifcopenshell.guid.new(),RelatingBuildingElement=wall,RelatedOpeningElement=opening)
        create('IfcWall',GlobalId=ifcopenshell.guid.new(),Name='Unmatched wall')
        path=folder/'fixture.ifc';model.write(str(path))
        identity=folder/'identity.json'
        identity.write_text(json.dumps({'complete':True,'walls':[
            {'sourceGuid':'SOURCE-1','ifc':{'ifcId':wall.GlobalId},'bounds':{'boundingBox3D':{'xMin':10,'xMax':15,'yMin':20,'yMax':20.3,'zMin':0,'zMax':3}}},
            {'sourceGuid':'SOURCE-2','ifc':{'ifcId':ifcopenshell.guid.new()}}]}),encoding='utf-8')
        return path,identity

    def test_ids_units_world_placement_and_opening_subtraction(self):
        with tempfile.TemporaryDirectory(prefix='ifc-test-') as temp:
            folder=Path(temp);path,identity=self.fixture(folder)
            summary=audit(path,identity,folder/'out',2)
            report=json.loads((folder/'out/audit.json').read_text(encoding='utf-8'))
            wall=report['walls'][0]
            self.assertEqual(summary['identityStatuses'],{'matched':1,'missing':1})
            self.assertEqual(len(summary['unmatchedIfcWallStepIds']),1)
            self.assertEqual(wall['geometryStatus'],'extracted')
            self.assertTrue(wall['boundsWithin1mm'])
            self.assertTrue(wall['meshClosedEdgeCheck'])
            self.assertAlmostEqual(wall['approximateMeshVolumeM3'],4.2,places=6)
            self.assertFalse(summary['geometryEqualityProven'])

    def test_incomplete_source_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='ifc-test-') as temp:
            folder=Path(temp);path,identity=self.fixture(folder)
            identity.write_text('{"complete":false}',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'incomplete'):
                audit(path,identity,folder/'out')


if __name__=='__main__':unittest.main()
