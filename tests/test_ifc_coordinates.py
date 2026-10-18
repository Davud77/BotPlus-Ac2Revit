import math
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from ifc_coordinates import fit_frame, apply_frame


class IfcFrameTests(unittest.TestCase):
    def setUp(self):
        self.points=np.array([[0,0,0],[10,0,3],[0,12,6],[15,20,9]],dtype=float)

    def test_rigid_registration_recovers_rotation_and_translation(self):
        angle=.37;c,s=math.cos(angle),math.sin(angle)
        rotation=np.array([[c,-s,0],[s,c,0],[0,0,1]])
        expected=self.points@rotation.T+[121.353011015,1020.826622753,7]
        frame=fit_frame(self.points,expected)
        np.testing.assert_allclose(apply_frame(self.points,frame),expected,atol=1e-9)
        self.assertAlmostEqual(frame['rotationRadians'],angle)
        np.testing.assert_allclose(frame['translationMeters'],[121.353011015,1020.826622753,7],atol=1e-9)

    def test_one_incorrect_identity_pair_is_rejected_without_discarding_outlier(self):
        target=self.points+[100,1000,0];target[0,0]+=.01
        with self.assertRaisesRegex(ValueError,'residual'):fit_frame(self.points,target)

    def test_scale_and_reflection_are_not_silently_applied(self):
        for changed in (self.points*1.001,self.points*[-1,1,1]):
            with self.assertRaises(ValueError):fit_frame(self.points,changed)

    def test_collinear_anchors_are_insufficient_evidence(self):
        a=[[0,0,0],[1,1,0],[2,2,0]]
        with self.assertRaisesRegex(ValueError,'collinear'):fit_frame(a,a)

    def test_iterator_preserves_world_coordinates_of_shared_geometry_instances(self):
        import ifcopenshell
        import ifcopenshell.geom
        f=ifcopenshell.file(schema='IFC4')
        def point(x,y,z):return f.create_entity('IfcCartesianPoint',Coordinates=(float(x),float(y),float(z)))
        def axis(x,y,z):return f.create_entity('IfcAxis2Placement3D',Location=point(x,y,z))
        context=f.create_entity('IfcGeometricRepresentationContext',ContextType='Model',CoordinateSpaceDimension=3,Precision=1e-6,WorldCoordinateSystem=axis(0,0,0))
        profile=f.create_entity('IfcRectangleProfileDef',ProfileType='AREA',XDim=.3,YDim=.5)
        solid=f.create_entity('IfcExtrudedAreaSolid',SweptArea=profile,Position=axis(0,0,0),ExtrudedDirection=f.create_entity('IfcDirection',DirectionRatios=(0.,0.,1.)),Depth=3.)
        rep=f.create_entity('IfcShapeRepresentation',ContextOfItems=context,RepresentationIdentifier='Body',RepresentationType='SweptSolid',Items=[solid])
        shape=f.create_entity('IfcProductDefinitionShape',Representations=[rep])
        products=[]
        for x,y,z in ((10,20,3),(50,80,5)):
            products.append(f.create_entity('IfcColumn',GlobalId=ifcopenshell.guid.new(),ObjectPlacement=f.create_entity('IfcLocalPlacement',RelativePlacement=axis(x,y,z)),Representation=shape))
        settings=ifcopenshell.geom.settings();settings.set('use-world-coords',True);settings.set('mesher-linear-deflection',.00025)
        expected={}
        for obj in products:
            geometry=ifcopenshell.geom.create_shape(settings,obj)
            expected[obj.id()]=np.array(geometry.geometry.verts).reshape(-1,3)
        iterator=ifcopenshell.geom.iterator(settings,f,2,include=products);self.assertTrue(iterator.initialize());seen=set()
        while True:
            result=iterator.get();points=np.array(result.geometry.verts).reshape(-1,3);seen.add(result.id)
            np.testing.assert_allclose(points.min(axis=0),expected[result.id].min(axis=0),atol=1e-9)
            np.testing.assert_allclose(points.max(axis=0),expected[result.id].max(axis=0),atol=1e-9)
            if not iterator.next():break
        self.assertEqual(seen,set(expected))


if __name__=='__main__':unittest.main()
