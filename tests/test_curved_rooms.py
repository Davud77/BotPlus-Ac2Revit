import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.geometry import curve,interior_point,arc_circle,ring


def p(x,y):return {'x':x,'y':y,'z':0}


def segment(sweep):
    return [curve(p(0,0),p(2,0),sweep),curve(p(2,0),p(1,0)),curve(p(1,0),p(0,0))]


class CurvedRoomsTests(unittest.TestCase):
    def test_both_semicircles_keep_exact_contours_and_place_inside(self):
        for sign in (1,-1):
            edges=segment(sign*math.pi);before=repr(edges);seed=interior_point(edges)
            self.assertLess((seed['x']-1)**2+seed['y']**2,1)
            self.assertLess(sign*seed['y'],0);self.assertEqual(repr(edges),before)

    def test_major_arc_is_not_reduced_to_minor(self):
        edges=segment(1.5*math.pi);cx,cy,radius,start,sweep=arc_circle(edges[0])
        self.assertAlmostEqual(sweep,1.5*math.pi)
        seed=interior_point(edges)
        self.assertLess(math.hypot(seed['x']-cx,seed['y']-cy),radius)
        self.assertLess(seed['y'],0)

    def test_line_hole_in_curved_zone_is_excluded(self):
        hole=ring({'polygonOutline':[p(.7,-.9),p(1.3,-.9),p(1.3,-.1),p(.7,-.1)]})
        seed=interior_point(segment(math.pi),[hole])
        self.assertFalse(.7<seed['x']<1.3 and -.9<seed['y']<-.1)
        self.assertLess((seed['x']-1)**2+seed['y']**2,1)

    def test_two_arc_circle_and_circular_hole(self):
        outer=[curve(p(-2,0),p(2,0),math.pi),curve(p(2,0),p(-2,0),math.pi)]
        hole=[curve(p(-1,0),p(1,0),math.pi),curve(p(1,0),p(-1,0),math.pi)]
        seed=interior_point(outer,[hole]);radius=math.hypot(seed['x'],seed['y'])
        self.assertGreater(radius,1);self.assertLess(radius,2)

    def test_large_coordinate_frame_is_stable(self):
        edges=segment(math.pi)
        for edge in edges:
            for key in ('start','mid','end'):
                if key in edge:edge[key]={'x':edge[key]['x']+500000,'y':edge[key]['y']-200000,'z':0}
        seed=interior_point(edges)
        self.assertLess((seed['x']-500001)**2+(seed['y']+200000)**2,1)
        self.assertLess(seed['y'],-200000)


if __name__=='__main__':unittest.main()
