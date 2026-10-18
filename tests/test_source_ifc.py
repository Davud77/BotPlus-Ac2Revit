from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from source_ifc import SourceIfcIndex


class Model:
    def __init__(self,*products):self.products=products
    def by_type(self,_):return self.products


def source(key,primary='',external=''):
    return {'sourceGuid':key,'raw':{'ifcIdentifiers':{'ifcId':primary,'externalIFCId':external}}}


class IdentityTests(unittest.TestCase):
    def test_explicit_id_works_when_tag_is_an_ordinary_element_mark(self):
        row=source('A','ifc-a');product=SimpleNamespace(GlobalId='ifc-a',Tag='Column-17')
        index=SourceIfcIndex({'elements':[row]},Model(product))
        self.assertEqual(index.matches(row),[product]);self.assertEqual(index.source_guid(product),'A')

    def test_tag_guid_is_an_exact_alternative_for_older_snapshots(self):
        row=source('A');product=SimpleNamespace(GlobalId='other',Tag='a')
        index=SourceIfcIndex({'elements':[row]},Model(product))
        self.assertEqual(index.matches(row),[product])

    def test_conflicting_tag_and_api_id_is_rejected(self):
        a,b=source('A','ifc-a'),source('B','ifc-b')
        product=SimpleNamespace(GlobalId='ifc-a',Tag='B')
        index=SourceIfcIndex({'elements':[a,b]},Model(product))
        with self.assertRaisesRegex(ValueError,'conflicts'):index.matches(a)
        with self.assertRaisesRegex(ValueError,'Conflicting'):index.source_guid(product)

    def test_external_is_only_used_when_primary_not_present(self):
        row=source('A','primary','external')
        primary=SimpleNamespace(GlobalId='primary',Tag=None);external=SimpleNamespace(GlobalId='external',Tag=None)
        self.assertEqual(SourceIfcIndex({'elements':[row]},Model(primary,external)).matches(row),[primary])
        self.assertEqual(SourceIfcIndex({'elements':[row]},Model(external)).matches(row),[external])

    def test_unrelated_name_or_mark_is_never_an_identity(self):
        row=source('A');product=SimpleNamespace(GlobalId='x',Tag='Column-17',Name='A')
        index=SourceIfcIndex({'elements':[row]},Model(product))
        self.assertEqual(index.matches(row),[]);self.assertEqual(index.source_guid(product),'')


if __name__=='__main__':unittest.main()
