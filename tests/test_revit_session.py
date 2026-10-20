import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import revit_session as session


class SessionTests(unittest.TestCase):
    def saved_session(self,root,complete=True,ifc_end=True):
        folder=root/'artifacts/sessions/saved';(folder/'source').mkdir(parents=True)
        state={'format':'ac2r.session/1','status':'ready','projectId':'wanted','project':{'projectPath':'source.pln'},'pipeline':'previous'}
        (folder/'session.json').write_text(json.dumps(state),encoding='utf-8')
        (folder/'source/export-report.json').write_text(json.dumps({'complete':complete}),encoding='utf-8')
        (folder/'source/manifest.json').write_text(json.dumps({'source':{'projectId':'wanted'},'elements':[]}),encoding='utf-8')
        (folder/'source/model.ifc').write_bytes(b' '*1100+(b'END-ISO-10303-21;' if ifc_end else b''))
        return folder,state

    def test_resume_rejects_incomplete_json_before_any_api_call(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder,_=self.saved_session(root,complete=False)
            args=argparse.Namespace(session=folder,port=19723)
            with patch.object(session,'ROOT',root),patch.object(session,'probe') as probe:
                with self.assertRaisesRegex(ValueError,'JSON'):session.continue_session(args)
                probe.assert_not_called()

    def test_resume_rejects_ifc_still_being_written(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder,_=self.saved_session(root,ifc_end=False)
            args=argparse.Namespace(session=folder,port=19723)
            with patch.object(session,'ROOT',root),patch.object(session,'probe') as probe:
                with self.assertRaisesRegex(ValueError,'IFC'):session.continue_session(args)
                probe.assert_not_called()

    def test_failed_preparation_attempt_keeps_previous_ready_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder,previous=self.saved_session(root)
            pointer=folder.parent/'last-completed.json';pointer.write_text('{"session":"previous"}',encoding='utf-8')
            args=argparse.Namespace(session=folder,port=19723,assets=root/'assets')
            info={'projectId':'wanted','project':{'projectPath':'source.pln'}}
            with patch.object(session,'ROOT',root),patch.object(session,'probe',return_value=info),patch('export_room_separators.extract'),patch.object(session,'write_separator_plan'),patch.object(session,'prepare_pipeline',side_effect=RuntimeError('geometry failure')):
                with self.assertRaisesRegex(RuntimeError,'geometry failure'):session.continue_session(args)
            self.assertEqual(json.loads((folder/'session.json').read_text()),previous)
            self.assertEqual(json.loads(pointer.read_text())['session'],'previous')
            attempts=list(folder.glob('prepared-*/attempt.json'))
            self.assertEqual(len(attempts),1)
            self.assertEqual(json.loads(attempts[0].read_text())['status'],'failed')

    def test_existing_project_id_is_retained_and_unknown_path_is_stable(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);pln=folder/'Other project.pln';pln.write_bytes(b'source')
            registry=folder/'registry.json'
            registry.write_text(json.dumps({'projects':[{'source':str(pln),'projectId':'known'}]}),encoding='utf-8')
            project={'projectPath':str(pln)}
            self.assertEqual(session.project_identity(project,registry),'known')
            registry.write_text('{"projects":[]}',encoding='utf-8')
            first=session.project_identity(project,registry)
            self.assertEqual(session.project_identity(project,registry),first)
            other=folder/'Other.pln';other.write_bytes(b'source')
            self.assertNotEqual(session.project_identity({'projectPath':str(other)},registry),first)

    def test_successful_continuation_clears_obsolete_failure_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder,state=self.saved_session(root)
            state.update(status='failed',error='old failure',errorType='TypeError',phase='geometry-preparation')
            (folder/'session.json').write_text(json.dumps(state),encoding='utf-8')
            args=argparse.Namespace(session=folder,port=19723,assets=root/'assets')
            info={'projectId':'wanted','project':{'projectPath':'source.pln'}}
            with patch.object(session,'ROOT',root),patch.object(session,'probe',return_value=info),patch('export_room_separators.extract'),patch.object(session,'write_separator_plan'),patch.object(session,'prepare_pipeline'):
                session.continue_session(args)
            ready=json.loads((folder/'session.json').read_text())
            self.assertEqual(ready['status'],'ready')
            for key in ('error','errorType','phase'):self.assertNotIn(key,ready)

    def test_unsaved_source_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'PLN'):session.project_identity({})

    def test_default_local_registry_preserves_existing_exchange_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'Source.pln';source.write_bytes(b'source')
            config=root/'config';config.mkdir()
            (config/'project-registry.local.json').write_text(json.dumps({'projects':[{'source':str(source),'projectId':'retained-id'}]}),encoding='utf-8')
            with patch.object(session,'ROOT',root):
                self.assertEqual(session.project_identity({'projectPath':str(source)}),'retained-id')

    def test_project_switched_after_ui_connection_is_rejected_before_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            args=argparse.Namespace(output=root/'artifacts/sessions/new',port=19723,project_id='wanted',project_path='source.pln')
            with patch.object(session,'ROOT',root),patch.object(session,'probe',return_value={'projectId':'other','project':{'projectPath':'other.pln'}}),patch.object(session,'export') as export:
                with self.assertRaisesRegex(ValueError,'изменился'):session.run(args)
                export.assert_not_called()
            self.assertFalse(args.output.exists())
            self.assertFalse((root/'artifacts/sessions/last-completed.json').exists())

    def test_failed_ifc_export_never_replaces_last_completed_session(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);folder=root/'artifacts/sessions';folder.mkdir(parents=True)
            pointer=folder/'last-completed.json';pointer.write_text('{"session":"previous"}',encoding='utf-8')
            args=argparse.Namespace(output=folder/'new',port=19723,project_id='wanted',project_path='source.pln',translator='Exact',properties=False)
            info={'projectId':'wanted','project':{'projectPath':'source.pln'},'translators':[{'name':'Exact'}]}
            def api_call(name,*_,**__):
                return info['project'] if name=='GetProjectInfo' else {'success':False}
            with patch.object(session,'ROOT',root),patch.object(session,'probe',return_value=info),patch.object(session,'export'),patch.object(session,'TapirClient') as client:
                client.return_value.call.side_effect=api_call
                with self.assertRaisesRegex(RuntimeError,'IFC'):session.run(args)
            self.assertEqual(json.loads(pointer.read_text())['session'],'previous')
            self.assertEqual(json.loads((args.output/'session.json').read_text())['status'],'failed')


if __name__=='__main__':unittest.main()
