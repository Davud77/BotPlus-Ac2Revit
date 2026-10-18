"""Catalog-only coverage; does not assert imported materials or matching hatch geometry."""
import argparse
import collections
import json
from pathlib import Path
import re


def check(catalog, rules, target):
    fills={f['index']:f for f in catalog['fills']}
    patterns={p['name'] for p in target['patterns'] if p['geometry']['target']=='Drafting'}
    rows=[]
    for material in catalog['materials']:
        fill=fills[material['cutFillIndex']]
        name=re.sub(r'\s*\(\d+\)$','',fill['name']).strip()
        mode='analogue';target_name=rules.get(name,'')
        if fill['subType']=='Empty':mode='empty';target_name=''
        elif fill['subType']=='Solid' and fill.get('percent')==1:mode='solid';target_name=''
        available=mode!='analogue' or target_name in patterns
        rows.append({'materialGuid':material['attributeId']['guid'],'sourceFillName':fill['name'],
            'mode':mode,'targetName':target_name,'availableRuleAndPattern':available})
    return {'scope':'Catalog-only rule availability against recorded target patterns; not an imported-model acceptance',
            'materials':len(rows),'covered':sum(r['availableRuleAndPattern'] for r in rows),
            'uncovered':dict(collections.Counter(r['sourceFillName'] for r in rows if not r['availableRuleAndPattern'])),
            'rows':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--catalog',type=Path,required=True);p.add_argument('--target',type=Path,required=True)
    p.add_argument('--rules',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--manifest',type=Path);a=p.parse_args()
    def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
    catalog=read(a.catalog)
    if 'materials' not in catalog:
        if a.manifest is None:raise ValueError('This catalog references frozen source materials; provide --manifest')
        manifest=read(a.manifest)
        if catalog['projectId']!=manifest['source']['projectId']:raise ValueError('Different source project')
        catalog['materials']=manifest['materials']
    result=check(catalog,read(a.rules)['rules'],read(a.target))
    a.out.parent.mkdir(parents=True,exist_ok=True)
    with a.out.open('x',encoding='utf-8') as stream:json.dump(result,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=True))
