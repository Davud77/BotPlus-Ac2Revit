from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from benchmark_case import ProgressWatch


class ProgressWatchTests(unittest.TestCase):
    def test_elapsed_timer_is_not_element_progress_and_resume_is_recorded(self):
        watch=ProgressWatch(10);row=dict(status='running',phase='wallGeometry',done=52,total=52,seconds=1)
        self.assertIsNone(watch.observe(row,0))
        self.assertIsNone(watch.observe(dict(row,seconds=100),9))
        self.assertEqual(watch.observe(dict(row,seconds=101),10)['status'],'needsInspection')
        self.assertIsNone(watch.observe(row,20))
        self.assertEqual(watch.observe(dict(row,phase='beamBodies',done=0),21)['status'],'resumed')
        self.assertIsNone(watch.observe(dict(row,phase='beamBodies',done=1),22))

    def test_missing_or_final_result_is_not_reported_as_a_native_stall(self):
        watch=ProgressWatch(10)
        self.assertIsNone(watch.observe({},0));self.assertIsNone(watch.observe({},100))
        self.assertIsNone(watch.observe(dict(status='complete'),101))
        self.assertIsNone(watch.observe(dict(status='complete'),200))
