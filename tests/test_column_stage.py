import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_stage_plan import prism_check,axis_curve
from prepare_column_bodies import body_products


class ColumnStageTests(unittest.TestCase):
    def prism(self):
        v=np.array([[x,y,z] for z in (0,3) for y in (-.2,.2) for x in (-.4,.4)])
        f=np.array([[0,2,3],[0,3,1],[4,5,7],[4,7,6],[0,1,5],[0,5,4],
                    [2,6,7],[2,7,3],[0,4,6],[0,6,2],[1,3,7],[1,7,5]])
        d={'width':.8,'depth':.4,'height':3,'axisRotationAngle':math.pi/3,'origin':{'x':11,'y':-7},'zCoordinate':4.05}
        a=d['axisRotationAngle'];r=np.array([[math.cos(a),-math.sin(a),0],[math.sin(a),math.cos(a),0],[0,0,1]])
        return d,v@r.T+[11,-7,4.05],f

    def test_rotated_prism_matches_position_and_volume(self):
        d,v,f=self.prism();self.assertAlmostEqual(prism_check(d,v,f)['volumeM3'],.96)

    def test_shifted_or_swapped_section_cannot_pass(self):
        d,v,f=self.prism()
        with self.assertRaisesRegex(ValueError,'differs'):prism_check(d,v+[.02,0,0],f)
        d.update(width=.4,depth=.8)
        with self.assertRaisesRegex(ValueError,'differs'):prism_check(d,v,f)

    def test_missing_face_cannot_pass_volume_screen(self):
        d,v,f=self.prism()
        with self.assertRaisesRegex(ValueError,'not closed'):prism_check(d,v,f[:-1])

    def test_ifc_decomposition_reaches_all_children_without_double_counting(self):
        class Item:
            def __init__(self,key,body=False,children=()):
                self.key=key
                self.Representation=type('R',(),{'Representations':[type('S',(),{'RepresentationIdentifier':'Body'})()] if body else []})()
                self.IsDecomposedBy=[type('D',(),{'RelatedObjects':children})()] if children else []
            def id(self):return self.key
            def is_a(self,name):return name=='IfcProduct'
        a,b=Item(2,True),Item(3,True)
        self.assertEqual(body_products(Item(1,children=[a,b])),[a,b])
        self.assertEqual(body_products(Item(1,True,[a,b]))[0].id(),1)


if __name__=='__main__':unittest.main()
