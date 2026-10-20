from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'))
from diagnose_room_boundaries import diagnose


class RoomDiagnosisTests(unittest.TestCase):
    def room(self):
        return dict(sourceGuid='ZONE', sourceLoops=[[[0, 0, 0], [5, 0, 0], [5, 4, 0], [0, 4, 0]]],
                    sourcePoint=dict(x=1, y=1), probeArea=12, boundaryModes={'Finish': [[
                        dict(curve='Line', points=[[0, 0, 0], [5, 0, 0]], element={'sourceGuid': 'OUTER'}),
                        dict(curve='Line', points=[[3, 0, 0], [3, 4, 0]], element={'sourceGuid': 'WALL', 'id': 7}),
                    ]]})

    def test_interior_boundary_is_evidence_and_missing_source_relations_stay_unknown(self):
        result = diagnose(self.room(), {'WALL': {'raw': {'details': {
            'visibility': {'showOnHome': False}, 'isAutoOnStoryVisibility': True}}}})
        self.assertEqual(result['status'], 'interiorNativeBoundary')
        self.assertEqual(len(result['candidates']), 1)
        self.assertAlmostEqual(result['candidates'][0]['lengthInsideSourceMeters'], 3.94)
        self.assertIsNone(result['candidates'][0]['confirmedSourceBoundaryOwner'])
        self.assertTrue(result['candidates'][0]['sourceAutomaticStoryVisibility'])

    def test_source_hole_boundary_is_not_an_interior_obstruction(self):
        room = self.room();room['sourceLoops'].append([[2, 1, 0], [3, 1, 0], [3, 3, 0], [2, 3, 0]])
        room['boundaryModes']['Finish'] = [[dict(curve='Line', points=[[3, 1, 0], [3, 3, 0]], element={})]]
        self.assertEqual(diagnose(room, {})['candidates'], [])

    def test_explicit_source_owner_is_distinct_from_visibility_and_missing_enclosure(self):
        room = self.room();elements = {'ZONE': {'raw': {'relations': {'zoneRelations': {
            'wallParts': [{'elementId': {'guid': 'WALL'}}]}}}}}
        self.assertTrue(diagnose(room, elements)['candidates'][0]['confirmedSourceBoundaryOwner'])
        room['probeArea'] = 0;room['boundaryModes']['Finish'] = []
        self.assertEqual(diagnose(room, elements)['status'], 'noNativeEnclosure')

    def test_derived_zero_wall_separator_is_compared_using_its_source_owner(self):
        room=self.room();segment=room['boundaryModes']['Finish'][0][1]
        segment['element'].update(sourceGuid='zero-wall-separator:WALL:story:0',sourceOwnerGuid='WALL')
        elements={'ZONE':{'raw':{'relations':{'zoneRelations':{'wallParts':[
            {'elementId':{'guid':'WALL'}},{'elementId':{'guid':'MISSING'}}]}}}}}
        result=diagnose(room,elements)
        self.assertEqual(result['sourceBoundaryOwnerCount'],2)
        self.assertEqual(result['sourceOwnersAbsentFromNativeBoundary'],['MISSING'])
        self.assertEqual(result['nativeOwnersAbsentFromSourceRelations'],['OUTER'])
        self.assertTrue(result['candidates'][0]['confirmedSourceBoundaryOwner'])


if __name__ == '__main__':
    unittest.main()
