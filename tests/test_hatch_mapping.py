import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_hatch_mapping import prepare


class HatchMappingTests(unittest.TestCase):
    def setUp(self):
        self.manifest={'source':{'projectId':'p'},'materials':[{'attributeId':{'guid':'m'},'name':'Unrelated material name',
            'cutFillIndex':1,'cutFillPen':1,'cutFillBackgroundPen':0,'cutFillOrientation':'FitToSkin'}]}
        self.source={'projectId':'p','materialDifferences':[],'fills':[{'index':1,'attributeId':{'guid':'f'},'name':'Concrete','subType':'Vector'}],
            'penTables':[{'isActiveForModel':True,'pens':[{'index':1,'color':{'red':0.25,'green':0.5,'blue':1}}]}]}
        self.target={'projectId':'p','patterns':[{'name':'Solid','fingerprint':'s','geometry':{'target':'Drafting','solid':True}},
            {'name':'Concrete target','fingerprint':'c','geometry':{'target':'Drafting','solid':False}}],
            'materials':[{'sourceGuid':'m','elementId':10}],'compoundTypes':[{'layers':[{'materialId':10}]}]}
        self.rules={'Concrete':'Concrete target'}

    def run_plan(self):return prepare(self.manifest,self.source,self.target,self.rules)

    def test_explicit_fill_and_active_pen_are_used(self):
        rows,inventory=self.run_plan();self.assertEqual(rows[0]['targetName'],'Concrete target')
        self.assertEqual(rows[0]['foregroundRgb'],[64,128,255]);self.assertEqual(rows[0]['sourceFillGuid'],'f')
        self.assertEqual(rows[0]['match'],'analogue');self.assertTrue(inventory[0]['usedInCompoundType'])

    def test_missing_used_mapping_does_not_fall_back(self):
        self.rules={}
        with self.assertRaisesRegex(ValueError,'Unmapped material'):self.run_plan()

    def test_another_revit_template_reports_missing_pattern_without_guessing(self):
        self.target['patterns'].pop()
        rows,inventory=prepare(self.manifest,self.source,self.target,self.rules,allow_unmapped=True)
        self.assertEqual(rows,[])
        self.assertEqual(inventory[0]['status'],'unmapped')
        self.assertIn('absent',inventory[0]['reason'])

    def test_model_pattern_cannot_be_used_for_cut(self):
        self.target['patterns'][1]['geometry']['target']='Model'
        with self.assertRaisesRegex(ValueError,'target pattern absent'):self.run_plan()

    def test_empty_source_clears_without_pattern_guess(self):
        self.source['fills'][0]['subType']='Empty';self.rules={}
        rows,_=self.run_plan();self.assertEqual(rows[0]['match'],'empty');self.assertEqual(rows[0]['targetName'],'')

    def test_quarter_fill_is_not_promoted_to_solid_black(self):
        self.source['fills'][0].update(subType='Solid',percent=0.25)
        rows,_=self.run_plan();self.assertEqual(rows[0]['match'],'analogue');self.assertEqual(rows[0]['targetName'],'Concrete target')

    def test_duplicate_target_or_ambiguous_pen_table_rejected(self):
        self.target['patterns'].append(copy.deepcopy(self.target['patterns'][1]))
        with self.assertRaisesRegex(ValueError,'Ambiguous target'):self.run_plan()
        self.target['patterns'].pop();self.source['penTables'].append(copy.deepcopy(self.source['penTables'][0]))
        with self.assertRaisesRegex(ValueError,'exactly one active'):self.run_plan()

    def test_changed_source_material_graphics_rejected(self):
        self.source['materialDifferences']=[{'guid':'m'}]
        with self.assertRaisesRegex(ValueError,'Different source'):self.run_plan()

    def test_coloured_background_preserved(self):
        self.manifest['materials'][0]['cutFillBackgroundPen']=1
        rows,_=self.run_plan();self.assertEqual(rows[0]['background'],'solid');self.assertEqual(rows[0]['backgroundRgb'],[64,128,255])


if __name__=='__main__':unittest.main()
