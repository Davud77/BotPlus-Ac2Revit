import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from prepare_column_behavior import build


class ColumnBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.columns = {'projectId': 'project', 'columns': [
            {'sourceGuid': 'column-a', 'familyKey': 'family-a', 'profileGuid': 'profile-a'}]}
        self.profiles = [{'attributeId': {'guid': 'profile-a'}, 'skins': [
            {'isCore': True, 'buildingMaterialId': {'attributeId': {'guid': 'concrete'}}},
            {'isCore': False, 'buildingMaterialId': {'attributeId': {'guid': 'insulation'}}}]}]

    def test_exact_core_not_finish_controls_behavior(self):
        plan = build(self.columns, self.profiles, ['CONCRETE'])
        self.assertEqual(len(plan['families']), 1)
        self.assertEqual(plan['families'][0]['coreMaterialGuids'], ['CONCRETE'])

    def test_unapproved_or_missing_material_does_not_become_concrete(self):
        self.assertEqual(build(self.columns, self.profiles, ['other'])['families'], [])
        self.assertEqual(build(self.columns, [], ['concrete'])['families'], [])

    def test_mixed_members_in_same_family_reject_entire_decision(self):
        other = copy.deepcopy(self.columns['columns'][0])
        other.update(sourceGuid='column-b', profileGuid='profile-b')
        self.columns['columns'].append(other)
        self.assertEqual(build(self.columns, self.profiles, ['concrete'])['families'], [])

    def test_duplicate_source_identity_rejected(self):
        self.columns['columns'].append(copy.deepcopy(self.columns['columns'][0]))
        with self.assertRaises(ValueError):
            build(self.columns, self.profiles, ['concrete'])


if __name__ == '__main__':
    unittest.main()
