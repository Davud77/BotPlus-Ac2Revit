import math
from pathlib import Path
import sys
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_native_beams import check_rectangular_beam


class NativeBeamTests(unittest.TestCase):
    def record(self):
        angle=.53;u=np.array([math.cos(angle),math.sin(angle),0]);v=np.array([-u[1],u[0],0]);o=np.array([24.,58.,4.4])
        local=np.array([[x,y,z] for z in (-.6,0) for y in (-.2,.2) for x in (0,5)])
        points=local@np.array([u,v,[0,0,1]])+o
        faces=[[0,2,3],[0,3,1],[4,5,7],[4,7,6],[0,1,5],[0,5,4],[2,6,7],[2,7,3],[0,4,6],[0,6,2],[1,3,7],[1,7,5]]
        d={'begCoordinate':{'x':o[0],'y':o[1]},'endCoordinate':{'x':(o+u*5)[0],'y':(o+u*5)[1]},
           'zCoordinate':4.4,'width':.4,'height':.6,'isSlanted':False,'arcAngle':0,'verticalCurveHeight':0,
           'circleBased':False,'anchorPoint':'TopCenter','offset':0,'profileAngle':2*math.pi,'holes':[]}
        return {'sourceGuid':'A','levelKey':'story:0','mark':'B','materialKey':'M',
                'vertices':[dict(zip('xyz',p)) for p in points.tolist()],'faces':faces,
                'parts':[{'closedEdgeCheck':True}], 'rawSource':{'details':d},
                'boundsMinMeters':points.min(axis=0).tolist(),'boundsMaxMeters':points.max(axis=0).tolist()}

    def test_rotated_top_anchored_beam_matches_complete_prism(self):
        r=check_rectangular_beam(self.record());self.assertAlmostEqual(r['volumeM3'],1.2)
        self.assertEqual(r['start']['z'],4.4)

    def test_wrong_reference_height_is_rejected(self):
        r=self.record();r['rawSource']['details']['zCoordinate']+=.01
        with self.assertRaisesRegex(ValueError,'reference line'):check_rectangular_beam(r)

    def test_sloped_open_or_offset_beam_is_retained(self):
        for edit in ('slope','open','offset'):
            r=self.record()
            if edit=='slope':r['rawSource']['details']['isSlanted']=True
            elif edit=='open':r['parts'][0]['closedEdgeCheck']=False
            else:r['rawSource']['details']['offset']=.02
            with self.assertRaises(ValueError):check_rectangular_beam(r)

    def test_cut_body_cannot_be_replaced_by_nominal_box(self):
        r=self.record();r['vertices'][0]['z']+=.05
        with self.assertRaises(ValueError):check_rectangular_beam(r)


if __name__=='__main__':unittest.main()
