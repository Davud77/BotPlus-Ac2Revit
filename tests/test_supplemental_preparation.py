import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import canonical_digest,write_json
from prepare_supplemental_bodies import prepare


class SupplementalPreparationTests(unittest.TestCase):
    def test_shared_model_pipeline_delegates_a_missing_body_to_the_body_adapter(self):
        import ifcopenshell
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);model=ifcopenshell.file(schema='IFC4');ifc=folder/'model.ifc';model.write(str(ifc))
            project='11111111-1111-4111-8111-111111111111'
            write_json(folder/'manifest.json',{'source':{'projectId':project},'coordinates':{},'elements':[
                {'sourceGuid':'22222222-2222-4222-8222-222222222222','kind':'Roof','raw':{},'payload':{}}]})
            frame={'projectId':project,'matrix':[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]};frame['hash']=canonical_digest(frame)
            write_json(folder/'frame.json',frame)
            prepare(argparse.Namespace(out=folder/'out',manifest=folder/'manifest.json',ifc=ifc,frame=folder/'frame.json',resume=False),ifc_model=model)
            plan=json.loads((folder/'out/supplemental.json').read_text(encoding='utf-8'))
            self.assertEqual(plan['outcomes'],{'error':1})
            body=json.loads(Path(plan['batches'][0]['path']).read_text(encoding='utf-8'))
            self.assertEqual(body['elements'],[])
            audit=json.loads((folder/'out/roof-0000/audit.json').read_text(encoding='utf-8'))
            self.assertEqual(audit['roofs'][0]['error'],'IFC Tag match missing/ambiguous')
