import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from prepare_column_bodies import body_products


class WorkerTests(unittest.TestCase):
    def test_standalone_worker_imports_without_inherited_pythonpath(self):
        env=dict(os.environ);env.pop('PYTHONPATH',None);env['PYTHONUTF8']='1'
        with tempfile.TemporaryDirectory() as temp:
            for name in ('prepare_column_bodies.py','prepare_stage_plan.py','prepare_openings.py','prepare_supplemental_bodies.py'):
                with self.subTest(worker=name):
                    result=subprocess.run([sys.executable,str(ROOT/'tools'/name),'--help'],cwd=temp,env=env,capture_output=True,text=True,encoding='utf-8',timeout=30)
                    self.assertEqual(result.returncode,0,result.stderr)
                    self.assertIn('usage:',result.stdout)

    def test_decomposed_body_does_not_duplicate_another_source_element(self):
        class Product:
            def __init__(self,number,key,children=(),body=True):
                self.number=number;self.key=key
                self.Representation=SimpleNamespace(Representations=[SimpleNamespace(RepresentationIdentifier='Body')] if body else [])
                self.IsDecomposedBy=[SimpleNamespace(RelatedObjects=children)] if children else []
            def id(self):return self.number
            def is_a(self,name):return name=='IfcProduct'
        own=Product(2,'');foreign=Product(3,'RAILING')
        stair=Product(1,'STAIR',[own,foreign],False)
        self.assertEqual(body_products(stair,source_owner=lambda p:p.key,owner_key='STAIR'),[own])


if __name__=='__main__':unittest.main()
