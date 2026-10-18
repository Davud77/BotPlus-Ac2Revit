import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from compare_geometry_snapshots import compare_rows


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.row = {'sourceGuid': 'a', 'elementId': 1, 'geometryStatus': 'extracted', 'meshSaved': True,
                    'vertices': [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
                    'faces': [[0, 1, 2], [0, 2, 3]], 'nativeSolidVolumeM3': 0}

    def test_changed_triangulation_same_surface(self):
        other = copy.deepcopy(self.row)
        other['faces'] = [[0, 1, 3], [1, 2, 3]]
        self.assertEqual(compare_rows(self.row, other)['status'], 'equivalentWithinTolerance')

    def test_shift_without_volume_change_is_detected(self):
        other = copy.deepcopy(self.row)
        for point in other['vertices']:
            point[0] += .01
        self.assertEqual(compare_rows(self.row, other)['status'], 'changed')

    def test_geometry_error_cannot_pass_by_equal_mesh(self):
        other = copy.deepcopy(self.row)
        other['geometryStatus'] = 'error'
        self.assertEqual(compare_rows(self.row, other)['status'], 'geometryError')


if __name__ == '__main__':
    unittest.main()
