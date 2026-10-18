import copy
import math
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_room_separators import build,source_curves
from export_room_separators import same_source


class SeparatorsTests(unittest.TestCase):
    def setUp(self):
        self.row={'sourceGuid':'a','type':'Line','floorIndex':0,'details':{'roomSeparator':True,'zCoordinate':0,
                  'begCoordinate':{'x':0,'y':0},'endCoordinate':{'x':2,'y':0}}}
        self.manifest={'source':{'projectId':'p'},'levels':[{'key':'story:0','elevation':0}]}

    def test_unmarked_graphics_are_rejected(self):
        self.row['details']['roomSeparator']=False
        self.assertEqual(len(build({'projectId':'p','elements':[self.row]},self.manifest)['segments']),0)

    def test_reversed_coincident_lines_retain_both_guids(self):
        other=copy.deepcopy(self.row);other['sourceGuid']='b'
        other['details']['begCoordinate'],other['details']['endCoordinate']=other['details']['endCoordinate'],other['details']['begCoordinate']
        plan=build({'projectId':'p','elements':[self.row,other]},self.manifest)
        self.assertEqual(len(plan['segments']),1)
        self.assertEqual(len(plan['segments'][0]['sourceRefs']),2)

    def test_open_polyline_is_not_closed_artificially(self):
        self.row.update(type='PolyLine');self.row['details']['coordinates']=[{'x':0,'y':0},{'x':1,'y':0},{'x':1,'y':1}]
        edges=source_curves(self.row,0);self.assertEqual(len(edges),2)
        self.assertEqual(edges[-1]['end'],{'x':1.,'y':1.,'z':0})

    def test_ambiguous_zero_arc_not_guessed_as_circle(self):
        self.row['type']='Arc'
        with self.assertRaises(ValueError):source_curves(self.row,0)

    def test_explicit_circle_uses_two_exact_semicircles(self):
        self.row.update(type='Arc',isCircleFromInventory=True)
        self.row['details'].update(origin={'x':2,'y':3},radius=.1,ratio=1)
        edges=source_curves(self.row,0);self.assertEqual(len(edges),2)
        for edge in edges:
            for key in ['start','mid','end']:
                p=edge[key];self.assertAlmostEqual(math.hypot(p['x']-2,p['y']-3),.1)

    def test_source_wall_shift_is_not_serialization_noise(self):
        self.assertFalse(same_source({'x':1.0},{'x':1.1}))


if __name__=='__main__':unittest.main()
