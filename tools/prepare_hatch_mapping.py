"""Resolve explicit source fill rules against a read-back Revit drafting catalog."""
import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


def rgb(pen):
    values=[pen['color'][k] for k in ('red','green','blue')]
    if any(not 0<=v<=1 for v in values):raise ValueError('Invalid source pen RGB')
    return [int(v*255+0.5) for v in values]


def prepare(manifest, source, target, rules, allow_unmapped=False):
    if source['projectId']!=manifest['source']['projectId'] or source['materialDifferences']:
        raise ValueError('Different source material snapshot')
    if target['projectId']!=source['projectId']:raise ValueError('Different Revit project')
    fills={r['index']:r for r in source['fills'] if 'attributeId' in r}
    if len(fills)!=len(source['fills']):raise ValueError('Fill errors or duplicate source indices')
    tables=[r for r in source['penTables'] if r.get('isActiveForModel') is True]
    if len(tables)!=1:raise ValueError('Expected exactly one active source pen table')
    pens={r['index']:r for r in tables[0]['pens']}
    patterns={}
    for r in target['patterns']:
        if r['geometry']['target']=='Drafting':
            if r['name'] in patterns:raise ValueError('Ambiguous target name')
            patterns[r['name']]=r
    solids=[r for r in patterns.values() if r['geometry']['solid']]
    if len(solids)!=1:raise ValueError('Expected one target solid pattern')
    compound_ids={l['materialId'] for t in target['compoundTypes'] for l in (t['layers'] or [])}
    target_mats={r['sourceGuid'].upper():r for r in target['materials']}
    rows=[];inventory=[]
    for material in manifest['materials']:
        guid=material['attributeId']['guid'].upper();fill=fills.get(material['cutFillIndex']);revit=target_mats.get(guid)
        if not fill or not revit or not revit['elementId']:raise ValueError('Missing source fill/imported material')
        base=re.sub(r'\s*\(\d+\)$','',fill['name']).strip();name=rules.get(base);mode='analogue';reason='Явный аналог образца шаблона; геометрия/масштаб рисунка и ориентация слоя могут отличаться.'
        if fill['subType']=='Empty':mode='empty';name='';reason='Исходный тип Empty: штриховка отсутствует.'
        elif fill['subType']=='Solid' and fill.get('percent')==1:mode='solid';name=solids[0]['name'];reason='Исходная сплошная заливка 100%; сохранён цвет пера.'
        used=revit['elementId'] in compound_ids
        summary={'materialGuid':guid,'materialName':material['name'],'sourceFillIndex':fill['index'],'sourceFillName':fill['name'],'usedInCompoundType':used,'targetName':name or '',
                 'status':'mapped' if name or mode=='empty' else 'unmapped'}
        inventory.append(summary)
        if summary['status']=='unmapped':continue
        if mode!='empty' and name not in patterns:
            if not allow_unmapped:raise ValueError('Explicit target pattern absent: '+name)
            summary.update(status='unmapped', reason='Explicit target pattern absent in this Revit template')
            continue
        fg=pens.get(material['cutFillPen'])
        if not fg:raise ValueError('Missing foreground source pen')
        bp=material['cutFillBackgroundPen'];background='transparent';brgb=[255,255,255]
        if bp>0:
            if bp not in pens:raise ValueError('Missing background source pen')
            background='solid';brgb=rgb(pens[bp])
        elif bp==-1:reason+=' Фон окна Archicad сопоставлен прозрачному фону листа Revit.'
        elif bp!=0:raise ValueError('Unknown background pen semantics')
        rows.append({'materialGuid':guid,'sourceFillIndex':fill['index'],'sourceFillGuid':fill['attributeId']['guid'],'sourceFillName':fill['name'],
            'match':mode,'reason':reason,'targetName':name or '', 'targetFingerprint':patterns[name]['fingerprint'] if name else '',
            'foregroundRgb':rgb(fg),'background':background,'backgroundRgb':brgb,'backgroundFingerprint':solids[0]['fingerprint'] if background=='solid' else '',
            'sourceForegroundPen':material['cutFillPen'],'sourceBackgroundPen':bp,'sourceCutFillOrientation':material['cutFillOrientation']})
    missing=[r for r in inventory if r['usedInCompoundType'] and r['status']=='unmapped']
    if missing and not allow_unmapped:raise ValueError('Unmapped material used in a compound type: '+str(missing))
    return rows,inventory


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--source',type=Path);p.add_argument('--target',type=Path,required=True);p.add_argument('--rules',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--allow-unmapped',action='store_true');a=p.parse_args()
    def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
    manifest,target=map(read,(a.manifest,a.target));rules=read(a.rules)['rules']
    digest=hashlib.file_digest(a.manifest.open('rb'),'sha256').hexdigest()
    if a.source:source=read(a.source)
    else:
        if not manifest.get('fills') or not manifest.get('penTables'):raise ValueError('Manifest has no fill/pen definitions; provide --source catalog')
        source={'projectId':manifest['source']['projectId'],'materialDifferences':[],'sourceManifestSha256':digest,'fills':manifest['fills'],'penTables':manifest['penTables']}
    if digest!=source['sourceManifestSha256']:raise ValueError('Manifest bytes differ')
    rows,inventory=prepare(manifest,source,target,rules,a.allow_unmapped);a.out.mkdir(parents=True,exist_ok=True)
    catalog_path=a.source or a.manifest
    plan={'format':'ac2r.hatch-mapping/1','projectId':source['projectId'],'sourceManifestSha256':digest,'sourceCatalog':str(catalog_path.resolve()),
          'sourceCatalogSha256':hashlib.file_digest(catalog_path.open('rb'),'sha256').hexdigest(),'penTable':next(r['attributeId']['guid'] for r in source['penTables'] if r.get('isActiveForModel')),
          'materials':rows}
    (a.out/'hatch-mapping.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    with (a.out/'mapping-inventory.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['materialGuid','materialName','sourceFillIndex','sourceFillName','usedInCompoundType','targetName','status','reason']);w.writeheader();w.writerows(inventory)
    summary={'sourceMaterials':len(inventory),'mapped':len(rows),'unmapped':len(inventory)-len(rows),'usedCompoundMaterials':sum(r['usedInCompoundType'] for r in inventory),'usedCompoundUnmapped':sum(r['usedInCompoundType'] and r['status']=='unmapped' for r in inventory),
             'mappingKinds':{k:sum(r['match']==k for r in rows) for k in ('analogue','empty','solid')}}
    (a.out/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary))
