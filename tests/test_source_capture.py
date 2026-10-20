import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'));sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import write_json,file_sha256
from source_capture import seal,verify
import revit_session


class SourceCaptureTests(unittest.TestCase):
    def capture(self,root):
        session=root/'artifacts/sessions/saved';(session/'source').mkdir(parents=True);(session/'separator-source').mkdir()
        source=root/'Source.pln';source.write_bytes(b'unchanged source')
        info=dict(projectId='wanted',project=dict(projectPath=str(source)),port=19723)
        write_json(session/'session.json',dict(format='ac2r.session/1',status='captured',**info))
        write_json(session/'source/manifest.json',dict(source=dict(projectId='wanted'),elements=[]))
        write_json(session/'source/snapshot.json',dict(project=info['project']))
        write_json(session/'source/export-report.json',dict(complete=True,counts={}))
        (session/'source/model.ifc').write_bytes(b' '*1100+b'END-ISO-10303-21;')
        write_json(session/'separator-source/elements.json',dict(projectId='wanted',elements=[]))
        write_json(session/'separator-source/export-report.json',dict(projectId='wanted',physicalSourceIdentical=True))
        sha=file_sha256(session/'source/manifest.json')
        write_json(session/'separators.json',dict(projectId='wanted',sourceManifestSha256=sha))
        write_json(session/'source-verification.json',dict(complete=True,projectId='wanted',sourceProject=info['project'],manifestSha256=sha,counts={}))
        return session,info

    def test_unchanged_capture_can_be_prepared_with_archicad_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session,info=self.capture(root);capture=seal(session,info)
            args=argparse.Namespace(session=session,port=19723,assets=root/'assets')
            with patch.object(revit_session,'ROOT',root),patch.object(revit_session,'probe') as probe,patch.object(revit_session,'prepare_pipeline'),patch.object(revit_session,'write_separator_plan'),patch('source_capture.memory_budget',return_value=dict(defer=False)):
                state=revit_session.continue_session(args)
            probe.assert_not_called();self.assertTrue(state['preparedOffline']);self.assertEqual(state['captureHash'],capture['hash'])
            self.assertEqual(json.loads((session.parent/'last-completed.json').read_text())['session'],str(session/'session.json'))

    def test_changed_ifc_blocks_preparation_and_preserves_previous_ready_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session,info=self.capture(root);seal(session,info)
            pointer=session.parent/'last-completed.json';pointer.write_text('{"session":"previous"}')
            (session/'source/model.ifc').write_bytes(b'changed IFC')
            with patch.object(revit_session,'ROOT',root),patch.object(revit_session,'prepare_pipeline') as prepare:
                with self.assertRaisesRegex(ValueError,'Captured input changed'):revit_session.prepare_captured_session(session,root/'assets')
                prepare.assert_not_called()
            self.assertEqual(json.loads(pointer.read_text())['session'],'previous')

    def test_seal_rejects_unverified_or_different_geometry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session,info=self.capture(root)
            verification=session/'source-verification.json';data=json.loads(verification.read_text());data['manifestSha256']='wrong';write_json(verification,data)
            with self.assertRaisesRegex(ValueError,'different manifest'):seal(session,info)
            self.assertFalse((session/'capture.json').exists())

    def test_seal_rejects_incomplete_ifc(self):
        with tempfile.TemporaryDirectory() as temp:
            session,info=self.capture(Path(temp));(session/'source/model.ifc').write_bytes(b'not finished')
            with self.assertRaisesRegex(ValueError,'incomplete'):seal(session,info)

    def test_manifest_and_snapshot_are_both_immutable(self):
        for name in ('source/manifest.json','source/snapshot.json','separators.json'):
            with self.subTest(name=name),tempfile.TemporaryDirectory() as temp:
                session,info=self.capture(Path(temp));seal(session,info)
                (session/name).write_text('{}')
                with self.assertRaisesRegex(ValueError,'Captured input changed'):verify(session)

    def test_failed_offline_preparation_preserves_capture_and_pointer(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session,info=self.capture(root);capture=seal(session,info)
            pointer=session.parent/'last-completed.json';pointer.write_text('{"session":"previous"}')
            with patch.object(revit_session,'ROOT',root),patch.object(revit_session,'write_separator_plan'),patch('source_capture.memory_budget',return_value=dict(defer=False)),patch.object(revit_session,'prepare_pipeline',side_effect=RuntimeError('test failure')):
                with self.assertRaisesRegex(RuntimeError,'test failure'):revit_session.prepare_captured_session(session,root/'assets')
            self.assertEqual(verify(session)['hash'],capture['hash']);self.assertEqual(json.loads(pointer.read_text())['session'],'previous')
            self.assertEqual(len(list(session.glob('prepared-offline-*/failure.txt'))),1)

    def test_large_capture_never_publishes_a_ready_pipeline_before_preparation(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);saved,info=self.capture(root);info['translators']=[dict(name='Exact')]
            target=saved.parent/'new';pointer=saved.parent/'last-completed.json';pointer.write_text('{"session":"previous"}')
            args=argparse.Namespace(output=target,port=19723,project_id='wanted',project_path=info['project']['projectPath'],translator='Exact',properties=False,assets=root/'assets')
            def export(_):shutil.copytree(saved/'source',target/'source')
            def separators(*_,**__):shutil.copytree(saved/'separator-source',target/'separator-source')
            def plan(*_):shutil.copy2(saved/'separators.json',target/'separators.json')
            verification=json.loads((saved/'source-verification.json').read_text())
            with patch.object(revit_session,'ROOT',root),patch.object(revit_session,'probe',return_value=info),patch.object(revit_session,'export',side_effect=export),patch.object(revit_session,'export_ifc'),patch('export_room_separators.extract',side_effect=separators),patch.object(revit_session,'write_separator_plan',side_effect=plan),patch.object(revit_session,'verify_source_geometry',return_value=verification),patch('source_capture.memory_budget',return_value=dict(defer=True,requiredBytes=100,availableCommitBytes=50)),patch.object(revit_session,'prepare_pipeline') as prepare:
                state=revit_session.run(args)
            prepare.assert_not_called();self.assertEqual(state['status'],'captured');self.assertNotIn('pipeline',state)
            self.assertEqual(json.loads(pointer.read_text())['session'],'previous');verify(target)

    def test_legacy_capture_populates_counts_missing_from_exporting_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);session,info=self.capture(root)
            verification=json.loads((session/'source-verification.json').read_text())
            with patch.object(revit_session,'ROOT',root),patch.object(revit_session,'probe',return_value=info),patch.object(revit_session,'verify_source_geometry',return_value=verification),patch('source_capture.memory_budget',return_value=dict(defer=False)):
                state=revit_session.capture_existing_session(argparse.Namespace(session=session,port=19723))
            self.assertEqual(state['counts'],{});self.assertEqual(state['status'],'captured');verify(session)
