import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from prepare_pipeline import prepare


class PipelineInputTests(unittest.TestCase):
    def test_inputs_resolve_against_the_input_map_for_an_arbitrary_location(self):
        schema=json.loads((ROOT/'contracts/pipeline.schema.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'source';data.mkdir();(data/'input.json').write_text('{}',encoding='utf-8')
            values={key:'input.json' for key in schema['required'] if key!='pipelineFormat'}
            file=data/'inputs.json';file.write_text(json.dumps(values),encoding='utf-8')
            output=root/'new'/'plan'
            plan=json.loads(prepare(output,file).read_text(encoding='utf-8'))
            self.assertEqual(plan['pipelineFormat'],'ac2r.pipeline/1')
            for key in values:
                self.assertEqual((output/plan[key]).resolve(),(data/'input.json').resolve())

    def test_missing_inputs_do_not_create_an_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);file=root/'inputs.json';file.write_text('{}',encoding='utf-8')
            output=root/'output'
            with self.assertRaisesRegex(ValueError,'Missing pipeline inputs'):prepare(output,file)
            self.assertFalse(output.exists())
