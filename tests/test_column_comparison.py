import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from compare_column_geometry import compare,surface_distance
import numpy as np


class ColumnComparisonTests(unittest.TestCase):
    def check(self,vertices,faces):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            reference={'elements':[{'sourceGuid':'A','vertices':[{'x':50.40000049,'y':0,'z':0},
                    {'x':51.40000049,'y':0,'z':0},{'x':50.40000049,'y':1,'z':0},{'x':51.40000049,'y':1,'z':0}],
                    'faces':[[0,1,2],[1,3,2]],'parts':[{'approximateVolumeM3':0}], 'rawSource':{'details':{}}}]}
            native={'sourceGuid':'A','elementClass':'DirectShape','geometryStatus':'extracted','vertices':vertices,'faces':faces}
            (root/'source.json').write_text(json.dumps(reference))
            (root/'actual.jsonl').write_text(json.dumps(native)+'\n')
            return compare(root/'source.json',root/'actual.jsonl',root/'result')

    def points(self):return [[50.40000249,-1e-6,0],[51.40000249,-1e-6,0],[50.40000249,.999999,0],[51.40000249,.999999,0]]

    def test_float_drift_crossing_rounding_boundary_preserves_geometry(self):
        summary=self.check(self.points(),[[2,1,0],[2,3,1]])
        self.assertEqual(summary['statuses'],{'meshVerified':1})
        self.assertLess(summary['maxMeshVertexDifferenceMeters'],.001)

    def test_missing_triangle_is_detected_despite_same_bounds(self):
        self.assertEqual(self.check(self.points(),[[0,1,2]])['statuses'],{'meshMismatch':1})

    def test_moved_vertex_is_detected(self):
        points=self.points();points[0][0]+=.01
        self.assertEqual(self.check(points,[[0,1,2],[1,3,2]])['statuses'],{'meshMismatch':1})

    def test_surface_distance_handles_inside_and_outside_projection(self):
        vertices=np.array([[0.,0,0],[1,0,0],[0,1,0]]);faces=np.array([[0,1,2]])
        self.assertAlmostEqual(surface_distance(np.array([[.2,.2,1]]),vertices,faces),1)
        self.assertAlmostEqual(surface_distance(np.array([[2.,0,0]]),vertices,faces),1)
        self.assertAlmostEqual(surface_distance(np.array([[.2,.2,0]]),vertices,faces),0)


if __name__=='__main__':unittest.main()
