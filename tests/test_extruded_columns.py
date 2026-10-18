import sys
from pathlib import Path
import unittest

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_extruded_columns import recognise_part


class ExtrudedColumnTests(unittest.TestCase):
    def hollow(self):
        outer=[[-2,-2],[2,-2],[2,2],[-2,2]];inner=[[-1,-1],[1,-1],[1,1],[-1,1]]
        vertices=np.array([[x,y,z] for z in (0,3) for x,y in outer+inner],dtype=float);faces=[]
        def quad(a,b,c,d):faces.extend([[a,b,c],[a,c,d]])
        for i in range(4):
            j=(i+1)%4
            quad(8+i,8+j,12+j,12+i);quad(4+i,4+j,j,i)
            quad(i,j,8+j,8+i);quad(4+j,4+i,12+i,12+j)
        return vertices,np.array(faces)

    def test_hole_is_preserved_with_correct_area_and_volume(self):
        vertices,faces=self.hollow();part=recognise_part(vertices,faces)
        self.assertEqual(len(part['loops']),2)
        self.assertAlmostEqual(part['areaM2'],12)
        self.assertAlmostEqual(part['volumeM3'],36)

    def test_taper_is_not_mistaken_for_extrusion(self):
        vertices,faces=self.hollow();vertices[8:,:2]*=.8
        with self.assertRaisesRegex(ValueError,'profiles differ'):recognise_part(vertices,faces)

    def test_intermediate_height_feature_is_retained(self):
        vertices,faces=self.hollow();vertices[0,2]=.1
        with self.assertRaisesRegex(ValueError,'intermediate Z'):recognise_part(vertices,faces)


if __name__=='__main__':unittest.main()
