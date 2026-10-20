import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'exporter')]
from prepare_opening_repairs import merge_audit_plan


class OpeningRepairPlanTests(unittest.TestCase):
    def fixture(self,root):
        session=root/'session';prepared=session/'prepared';out=root/'repair'
        prepared.mkdir(parents=True);(out/'fillings').mkdir(parents=True)
        write=lambda path,data:path.write_text(json.dumps(data),encoding='utf-8')
        original=dict(projectId='project',ifcSha256='reference',coordinates={},ifcToSource=dict(matrix=[]),
                      elements=[dict(sourceGuid='W',status='blocked'),dict(sourceGuid='D',status='ready')])
        write(prepared/'openings.json',original)
        write(prepared/'pipeline.json',dict(pipelineFormat='ac2r.pipeline/1',sourceManifest='../source/manifest.json',
                                            openingsPlan='openings.json',rebuildRooms=True))
        write(session/'session.json',dict(pipeline=str(prepared/'pipeline.json')))
        repair={**original,'elements':[dict(sourceGuid='W',status='ready',voidGeometry=dict(vertices=[]))]}
        write(out/'fillings/openings.json',repair)
        return session,prepared,out,write,repair

    def test_readback_sees_repaired_voids_and_initial_plan_stays_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            session,prepared,out,_,_=self.fixture(Path(temp))
            before=(prepared/'openings.json').read_bytes()
            merge_audit_plan(session,out)
            self.assertEqual((prepared/'openings.json').read_bytes(),before)
            plan=json.loads((out/'audit-pipeline.json').read_text(encoding='utf-8'))
            self.assertEqual(plan['pipelineFormat'],'ac2r.pipeline/1')
            self.assertTrue(Path(plan['sourceManifest']).is_absolute())
            rows=json.loads(Path(plan['openingsPlan']).read_text(encoding='utf-8'))['elements']
            self.assertEqual([r['sourceGuid'] for r in rows],['W','D'])
            self.assertIn('voidGeometry',rows[0]);self.assertEqual(rows[1]['status'],'ready')

    def test_foreign_reference_or_identity_cannot_enter_the_audit(self):
        for change in ('reference','identity'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as temp:
                session,_,out,write,repair=self.fixture(Path(temp))
                if change=='reference':repair['ifcSha256']='foreign'
                else:repair['elements'][0]['sourceGuid']='FOREIGN'
                write(out/'fillings/openings.json',repair)
                with self.assertRaises(ValueError):merge_audit_plan(session,out)
