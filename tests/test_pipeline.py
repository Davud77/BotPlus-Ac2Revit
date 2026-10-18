import copy
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.geometry import arc_midpoint, ring, interior_point, morph_mesh
from ac2r.client import aligned, ApiError
from ac2r.normalize import normalize, guid


PROJECT = '5e5a43ba-b71f-4cf1-9eb4-bcb98248b8fc'
WALL = '37D734D9-543B-409A-94A6-B5E0AA0E1888'


def snapshot():
    return {'stories': {'stories': [{'index': -1, 'level': -2.85, 'height': 2.85, 'name': ''},
                                     {'index': 5, 'level': 12.15, 'height': 3, 'name': 'Этаж'}]},
            'elements': [{'sourceGuid': WALL, 'type': 'Wall', 'floorIndex': 5, 'id': 'W-1',
                'details': {'geometryType': 'Straight', 'structureType': 'Basic', 'zCoordinate': 12.35,
                            'bottomOffset': 0.2, 'height': 3, 'begThickness': 0.3, 'flipped': False,
                            'begCoordinate': {'x': 10, 'y': 20}, 'endCoordinate': {'x': 15, 'y': 20},
                            'referenceLineLocation': 'CoreCenter'}}], 'attributes': {}}


class PipelineTests(unittest.TestCase):
    def test_hatch_and_pen_definitions_survive_normalization(self):
        s=snapshot();fill={'attributeId':{'guid':WALL},'subType':'Symbol','symbolArcs':[{'angle':math.pi}],'scaleWithPlan':True}
        table={'isActiveForModel':True,'pens':[{'index':168,'color':{'red':1,'green':0.5,'blue':0}}]}
        s['attributes'].update(Fill={'details':[fill]},PenTable={'details':[table]})
        m=normalize(s,PROJECT);self.assertEqual(m['fills'],[fill]);self.assertEqual(m['penTables'],[table])
        original=m['manifestHash'];s['attributes']['PenTable']['details'][0]['pens'][0]['color']['red']=0
        self.assertNotEqual(normalize(s,PROJECT)['manifestHash'],original)

    def test_guid_is_not_mark(self):
        m = normalize(snapshot(), PROJECT)
        e = m['elements'][0]
        self.assertEqual(e['sourceGuid'], WALL)
        self.assertEqual(e['mark'], 'W-1')
        self.assertEqual(e['levelKey'], 'story:5')
        self.assertAlmostEqual(e['payload']['bottomOffset'], 0.2)

    def test_inconsistent_absolute_z_is_rejected(self):
        s = snapshot(); s['elements'][0]['details']['zCoordinate'] = 1
        self.assertEqual(normalize(s, PROJECT)['elements'][0]['status'], 'unsupported')

    def test_nested_material_guid(self):
        self.assertEqual(guid({'attributeId': {'guid': 'abc'}}), 'ABC')

    def test_wall_reference_offset_is_preserved(self):
        s=snapshot();s['elements'][0]['details']['offset']=0.04
        s['elements'][0]['details']['referenceLineLocation']='Outside'
        e=normalize(s,PROJECT)['elements'][0]
        self.assertEqual(e['status'],'ready')
        self.assertEqual(e['payload']['referenceOffset'],0.04)
        self.assertEqual(e['payload']['referenceLine'],'Outside')

    def test_wall_topology_is_not_a_creation_dependency(self):
        s=snapshot();s['elements'][0]['relations']={'wallConnections':{'connectedToBeginPoint':[
            {'elementId':{'guid':'BB4811C7-7E47-4C57-B65B-9B7D7EF9B204'},'connectedWithBeginPoint':True}]}}
        e=normalize(s,PROJECT)['elements'][0]
        self.assertEqual(e['dependencies'],[])
        self.assertEqual(e['wallConnections'][0]['ownerEnd'],0)
        self.assertEqual(e['wallConnections'][0]['targetGuid'],'BB4811C7-7E47-4C57-B65B-9B7D7EF9B204')

    def test_positional_error_does_not_shift_guid(self):
        rows = list(aligned({'detailsOfElements': [{'error': 'bad'}, {'id': 'mark'}]}, 'detailsOfElements', ['guid1', 'guid2']))
        self.assertEqual(rows[1][0], 'guid2')
        with self.assertRaises(ApiError):
            list(aligned({'detailsOfElements': []}, 'detailsOfElements', ['guid1']))

    def test_signed_minor_major_arcs(self):
        a, b = {'x': 0, 'y': 0, 'z': 0}, {'x': 2, 'y': 0, 'z': 0}
        self.assertAlmostEqual(arc_midpoint(a, b, math.pi)['y'], -1)
        self.assertAlmostEqual(arc_midpoint(a, b, -math.pi)['y'], 1)
        self.assertAlmostEqual(arc_midpoint(a, b, 1.5*math.pi)['y'], -math.tan(3*math.pi/8))

    def test_tapir_zero_based_polygon_arcs(self):
        edges = ring({'polygonOutline': [{'x': 0, 'y': 0}, {'x': 2, 'y': 0}, {'x': 2, 'y': 2}, {'x': 0, 'y': 0}],
                      'polygonArcs': [{'begIndex': 0, 'endIndex': 1, 'arcAngle': math.pi}]})
        self.assertEqual(edges[0]['kind'], 'arc')
        self.assertEqual(edges[1]['kind'], 'line')

    def test_room_point_avoids_hole(self):
        def square(lo, hi):
            return ring({'polygonOutline': [{'x': lo, 'y': lo}, {'x': hi, 'y': lo}, {'x': hi, 'y': hi}, {'x': lo, 'y': hi}]})
        p = interior_point(square(0, 10), [square(2, 8)])
        self.assertFalse(2 < p['x'] < 8 and 2 < p['y'] < 8)

    def test_membranes_preserve_total_thickness(self):
        s = snapshot(); d = s['elements'][0]['details']; d['structureType'] = 'Composite'; d['compositeId'] = {'guid': 'C'}
        s['attributes']['Composite'] = {'details': [{'attributeId': {'guid': 'C'}, 'name': 'test', 'skins': [
            {'type': 'Finish', 'thickness': 0}, {'type': 'Core', 'thickness': 0.3}, {'type': 'Finish', 'thickness': 0}]}]}
        m = normalize(s, PROJECT)
        self.assertEqual(m['elements'][0]['status'], 'ready')
        self.assertEqual(m['types'][0]['layers'][0]['function'], 'Membrane')
        self.assertAlmostEqual(sum(l['width'] for l in m['types'][0]['layers']), 0.3)

    def test_metadata_change_affects_hash(self):
        s = snapshot(); before = normalize(s, PROJECT)['elements'][0]['contentHash']
        s['elements'][0]['classifications'] = {'key': 'changed'}
        self.assertNotEqual(before, normalize(s, PROJECT)['elements'][0]['contentHash'])

    def test_deterministic(self):
        self.assertEqual(normalize(snapshot(), PROJECT), normalize(copy.deepcopy(snapshot()), PROJECT))

    def test_morph_uses_rows_not_columns(self):
        m = morph_mesh({'origin': {'x': 10, 'y': 20, 'z': 0},
                        'xAxis': {'x': 0, 'y': -1, 'z': 0}, 'yAxis': {'x': 1, 'y': 0, 'z': 0},
                        'zAxis': {'x': 0, 'y': 0, 'z': 1},
                        'body': {'vertices': [{'x': 1, 'y': 0, 'z': 3}, {'x': 0, 'y': 1, 'z': 3}, {'x': 0, 'y': 0, 'z': 3}],
                                 'polygons': [{'vertexIds': [0, 1, 2]}]}})
        self.assertEqual(m['vertices'][0], {'x': 10.0, 'y': 21.0, 'z': 3.0})


if __name__ == '__main__': unittest.main()
