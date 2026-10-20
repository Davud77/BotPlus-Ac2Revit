import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'exporter'))
sys.path.insert(0, str(ROOT/'tools'))
from ac2r.client import TapirClient
from ac2r.jsonio import canonical_digest, file_sha256, write_json
from ac2r.recover import recover
from ac2r.export import export
from test_pipeline import snapshot, PROJECT, WALL
import revit_session


class LargeJsonTests(unittest.TestCase):
    def test_canonical_hash_is_backwards_compatible(self):
        data = {'я': [-0.0, 1e-20, 1e30, True, None, {'b': 3, 'a': '雪😀'}], 'a': [1, 2, 3]}
        old = json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        self.assertEqual(canonical_digest(data), hashlib.sha256(old).hexdigest())

    def test_large_repeated_arrays_are_written_with_bounded_extra_memory(self):
        data = {'rows': [{'text': 'Д'*1024}]*3000}
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'snapshot.json'
            tracemalloc.start()
            try:
                write_json(target, data)
                canonical_digest(data)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertGreater(target.stat().st_size, 6_000_000)
            self.assertLess(peak, 1_000_000, 'Output/hash must not allocate a complete serialized model')
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), data)

    def test_invalid_json_keeps_previous_checkpoint_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/'snapshot.json'
            write_json(target, {'previous': True})
            with self.assertRaises(ValueError):
                write_json(target, {'rows': [1, float('nan')]})
            self.assertEqual(json.loads(target.read_text()), {'previous': True})
            self.assertEqual(list(Path(temp).glob('*.tmp')), [])

    def test_file_hash_does_not_read_entire_ifc_into_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'model.ifc'
            path.write_bytes(b'IFC'*1_000_000)
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('Whole file allocation')):
                self.assertEqual(file_sha256(path), hashlib.sha256(b'IFC'*1_000_000).hexdigest())

    def test_resume_cache_keeps_paths_instead_of_all_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'000001_GetFills.json'
            request = {'command': 'API.ExecuteAddOnCommand', 'parameters': {'addOnCommandId': {'commandNamespace': 'TapirCommand', 'commandName': 'GetFills'}}}
            write_json(path, {'request': request, 'response': {'succeeded': True, 'result': {'addOnCommandResponse': {'fills': [1, 2]}}}})
            client = TapirClient(archive=temp, resume=True)
            self.assertTrue(all(isinstance(p, Path) for p in client.cache.values()))
            with patch('urllib.request.urlopen', side_effect=AssertionError('Cached call should be local')):
                self.assertEqual(client.call('GetFills'), {'fills': [1, 2]})
            self.assertEqual(client.last_archive, path)


class RecoveryTests(unittest.TestCase):
    def test_zone_relations_keep_boundary_intervals_and_errors_without_wall_filter(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); source = snapshot(); calls = []
            source['elements'] += [dict(sourceGuid='ZONE', type='Zone', floorIndex=5, details={}),
                                   dict(sourceGuid='DOOR', type='Door', floorIndex=5, details={})]
            ids = [{'elementId': {'guid': e['sourceGuid']}} for e in source['elements']]
            by_id = {e['sourceGuid']: e for e in source['elements']}
            relation = {'zoneRelations': {'wallParts': [{'elementId': {'guid': WALL},
                'roomEdgeIndex': 7, 'begDistance': .2, 'endDistance': 2.7}],
                'elementsGroupedByType': [{'elementType': 'Column', 'elements': [{'elementId': {'guid': 'COLUMN'}}]}]}}
            class Api:
                def call(self, name, params=None, **kwargs):
                    if name == 'GetStories': return source['stories']
                    if name == 'GetAllElements': return {'elements': ids}
                    if name == 'GetElementsByType': return {'elements': [i for i in ids if by_id[i['elementId']['guid']]['type'] == params['elementType']]}
                    if name == 'GetDetailsOfElements': return {'detailsOfElements': [by_id[i['elementId']['guid']] for i in params['elements']]}
                    if name == 'GetIFCIdsOfElements': return {'elementIFCIds': [{} for _ in params['elements']]}
                    if name == 'GetRelationsOfElements':
                        calls.append(params)
                        return {'relations': [relation if i['elementId']['guid'] == 'ZONE' else
                            {'error': {'code': -1, 'message': 'No source relation'}} if i['elementId']['guid'] == 'DOOR' else
                            {'wallConnections': {}} for i in params['elements']]}
                    return {}
            args = argparse.Namespace(output=folder, port=19723, timeout=10, resume=False, include_2d=False,
                limit=None, batch=100, metadata_batch=100, property_scope='custom', properties=False,
                gdl=False, ifc=False, project_id=PROJECT)
            with patch('ac2r.export.TapirClient', return_value=Api()), patch('builtins.print'):
                export(args)
            result = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            zone = next(e for e in result['elements'] if e['sourceGuid'] == 'ZONE')
            self.assertEqual(zone['raw']['relations'], relation)
            self.assertEqual(calls[0]['otherElementType'], 'Wall')
            self.assertNotIn('otherElementType', calls[1])
            coverage = json.loads((folder/'export-report.json').read_text())['relationCoverage']
            self.assertEqual(coverage['Zone'], dict(expected=1, retrieved=1, errors=0, missing=0))
            self.assertEqual(coverage['Door'], dict(expected=1, retrieved=0, errors=1, missing=0))

    def fixture(self, folder, terminal=True):
        (folder/'raw').mkdir()
        source = snapshot()
        source['project'] = {'projectPath': 'source.pln'}
        source['elements'].append({'sourceGuid': 'OBJECT', 'type': 'Object', 'floorIndex': 5, 'details': {}})
        source['elementInventory'] = [{'elementId': {'guid': e['sourceGuid']}} for e in source['elements']]
        write_json(folder/'snapshot.json', source)
        write_json(folder/'manifest.json', {'previousCheckpoint': True})
        write_json(folder/'export-report.json', {'complete': False, 'gdl': True})

        def archive(number, command, result, params=None):
            request = {'command': 'API.ExecuteAddOnCommand', 'parameters': {'addOnCommandId': {'commandNamespace': 'TapirCommand', 'commandName': command}}}
            if params is not None:
                request['parameters']['addOnCommandParameters'] = params
            path = folder/'raw'/f'{number:06d}_{command}.json'
            write_json(path, {'request': request, 'response': {'succeeded': True, 'result': {'addOnCommandResponse': result}}})
            return path

        ids = source['elementInventory']
        archive(1, 'GetIFCIdsOfElements', {'elementIFCIds': [{'ifcId': 'wall'}, {'ifcId': 'object'}]}, {'elements': ids})
        gdl = archive(2, 'GetGDLParametersOfElements', {'gdlParametersOfElements': [{'parameters': [{'name': 'array', 'value': [1, 2, 3]}]}]}, {'elements': [ids[1]]})
        if terminal:
            archive(3, 'GetProjectInfo', source['project'])
            archive(4, 'GetStories', source['stories'])
        return gdl, archive, source

    def test_recovery_preserves_gdl_in_raw_without_copying_it_into_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            gdl, _, original = self.fixture(folder)
            before = file_sha256(gdl)
            report = recover(folder, PROJECT)
            result = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            raw = result['elements'][1]['raw']
            self.assertNotIn('gdl', raw)
            ref = raw['gdlReference']
            self.assertEqual(file_sha256(folder/ref['path']), ref['sha256'])
            self.assertEqual(ref['sha256'], before)
            value = json.loads(gdl.read_text(encoding='utf-8'))
            for token in ref['jsonPointer'].split('/')[1:]:
                value = value[int(token)] if isinstance(value, list) else value[token]
            self.assertEqual(value['parameters'][0]['value'], [1, 2, 3])
            self.assertEqual(result['elements'][0]['raw']['details'], original['elements'][0]['details'])
            self.assertTrue(report['complete'])
            self.assertEqual(report['metadataCoverage']['GetIFCIdsOfElements'], 2)

    def test_unfinished_read_cannot_be_published_as_complete_json(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            self.fixture(folder, terminal=False)
            before = file_sha256(folder/'manifest.json')
            with self.assertRaisesRegex(ValueError, 'проверки'):
                recover(folder, PROJECT)
            self.assertEqual(file_sha256(folder/'manifest.json'), before)
            self.assertFalse(json.loads((folder/'export-report.json').read_text())['complete'])

    def test_missing_element_metadata_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            _, archive, source = self.fixture(folder)
            archive(1, 'GetIFCIdsOfElements', {'elementIFCIds': [{'ifcId': 'wall'}]}, {'elements': source['elementInventory'][:1]})
            with self.assertRaisesRegex(ValueError, 'покрытие'):
                recover(folder, PROJECT)

    def test_memory_failure_has_a_nonempty_message_and_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            args = argparse.Namespace(output=root/'artifacts/sessions/new', port=19723, project_id=PROJECT, project_path='source.pln', translator='Exact', properties=False)
            info = {'projectId': PROJECT, 'project': {'projectPath': 'source.pln'}, 'translators': [{'name': 'Exact'}]}
            with patch.object(revit_session, 'ROOT', root), patch.object(revit_session, 'probe', return_value=info), patch.object(revit_session, 'export', side_effect=MemoryError()):
                with self.assertRaises(MemoryError):
                    revit_session.run(args)
            state = json.loads((args.output/'session.json').read_text(encoding='utf-8'))
            self.assertIn('памяти', state['error'])
            self.assertEqual(state['errorType'], 'MemoryError')
            self.assertEqual(state['phase'], 'json-export')
            self.assertIn('MemoryError', (args.output/'failure.txt').read_text(encoding='utf-8'))
            self.assertFalse((root/'artifacts/sessions/last-completed.json').exists())

    def test_live_export_keeps_full_gdl_in_archive_and_only_reference_in_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            ids = [{'elementId': {'guid': 'OBJECT'}}]
            stories = {'stories': [{'index': 5, 'level': 0, 'height': 3, 'name': 'Этаж'}]}
            class Api:
                last_archive = None
                def call(self, name, params=None, **kwargs):
                    if name == 'GetStories': return stories
                    if name == 'GetAllElements': return {'elements': ids}
                    if name == 'GetElementsByType': return {'elements': ids if params['elementType'] == 'Object' else []}
                    if name == 'GetDetailsOfElements': return {'detailsOfElements': [{'type': 'Object', 'floorIndex': 5, 'details': {}}]}
                    if name == 'GetIFCIdsOfElements': return {'elementIFCIds': [{'ifcId': 'exact'}]}
                    if name == 'GetGDLParametersOfElements':
                        result = {'gdlParametersOfElements': [{'parameters': list(range(100_000))}]}
                        self.last_archive = folder/'raw/000001_GetGDLParametersOfElements.json'
                        write_json(self.last_archive, {'response': {'result': {'addOnCommandResponse': result}}}, compact=True)
                        return result
                    return {}
            args = argparse.Namespace(output=folder, port=19723, timeout=10, resume=False, include_2d=False,
                limit=None, batch=100, metadata_batch=20, property_scope='custom', properties=False,
                gdl=True, ifc=False, project_id=PROJECT)
            with patch('ac2r.export.TapirClient', return_value=Api()), patch('builtins.print'):
                export(args)
            result = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('gdlReference', result['elements'][0]['raw'])
            self.assertNotIn('gdl', result['elements'][0]['raw'])
            self.assertLess((folder/'snapshot.json').stat().st_size, 10_000)
            self.assertTrue(json.loads((folder/'export-report.json').read_text())['complete'])
            archive = json.loads((folder/'raw/000001_GetGDLParametersOfElements.json').read_text())
            self.assertEqual(len(archive['response']['result']['addOnCommandResponse']['gdlParametersOfElements'][0]['parameters']), 100_000)

    def test_changed_geometry_is_rejected_before_missing_ifc_can_be_created(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); data = snapshot()
            data['project']={'projectPath':'source.pln'}
            write_json(folder/'snapshot.json', data)
            write_json(folder/'manifest.json', {'elements': [{'sourceGuid': WALL, 'raw': data['elements'][0]}]})
            def call(name, params=None, **kwargs):
                if name == 'GetProjectInfo': return data['project']
                if name == 'GetStories': return data['stories']
                if name == 'GetElementsByType': return {'elements': [{'elementId': {'guid': WALL}}] if params['elementType'] == 'Wall' else []}
                if name == 'GetDetailsOfElements':
                    return {'detailsOfElements': [dict(data['elements'][0], details=dict(data['elements'][0]['details'], height=99))]}
                raise AssertionError('Unexpected command '+name)
            api = unittest.mock.Mock(); api.call.side_effect = call
            with patch('builtins.print'), self.assertRaisesRegex(ValueError, 'геометрия изменена'):
                revit_session.verify_source_geometry(folder/'manifest.json', api)
            self.assertFalse(any(c.args[0] == 'IFCFileOperation' for c in api.call.call_args_list))

    def test_changed_construction_is_rejected_even_if_element_coordinates_match(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); data = snapshot()
            headers = [{'attributeId': {'guid': 'COMPOSITE'}}]
            data['attributes']['Composite'] = {'headers': headers, 'details': [{'thickness': 0.3}]}
            write_json(folder/'snapshot.json', data)
            write_json(folder/'manifest.json', {'elements': [{'sourceGuid': WALL, 'raw': data['elements'][0]}]})
            def call(name, params=None, **kwargs):
                if name == 'GetStories': return data['stories']
                if name == 'GetAttributesByType': return {'attributes': headers}
                if name == 'GetComposites': return {'composites': [{'thickness': 0.4}]}
                raise AssertionError('Unexpected command '+name)
            api = unittest.mock.Mock(); api.call.side_effect = call
            with self.assertRaisesRegex(ValueError, 'конструкции/материалы'):
                revit_session.verify_source_geometry(folder/'manifest.json', api)


if __name__ == '__main__':
    unittest.main()
