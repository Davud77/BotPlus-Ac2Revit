"""Re-normalize the captured geometry/topology without re-exporting heavy GDL data."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.normalize import normalize,digest,guid
from ac2r.export import write_json

root=Path(__file__).resolve().parents[1]
old=json.loads((root/'artifacts/runtime/geometry-joins/manifest.json').read_text(encoding='utf-8'))
composites={}
for t in old['types']:
    comp=t.get('sourceCompositeGuid')
    if not comp: continue
    composites[comp]={'attributeId':{'guid':comp},'name':t['name'],'skins':[
        {'type':l.get('sourceFunction',l['function']),'thickness':l['width'],'buildingMaterialId':{'guid':l['materialKey']}} for l in t['layers']]}
snap={'stories':{'stories':[l.get('raw',{'index':l['index'],'name':l['name'],'level':l['elevation']}) for l in old['levels']]},
      'project':old['source'].get('project',{}),'elements':[e['raw'] for e in old['elements']],
      'attributes':{'Composite':{'details':list(composites.values())},'BuildingMaterial':{'details':old['materials']}}}
m=normalize(snap,old['source']['projectId']);m['source']=old['source'];m['source']['scope']+='; source topology and straight reference offset enabled'
m['coordinates']=old['coordinates'];m.pop('manifestHash',None);m['manifestHash']=digest(m)
config={'allowPartial':True,'zoneMode':'room','roomAreaToleranceSquareMeters':1.0,'connectWalls':True,'wallJoinToleranceMeters':0.001,'families':{}}
out=root/'artifacts/runtime/geometry-offsets';write_json(out/'manifest.json',m);write_json(out/'mapping.json',config)
probe=json.loads((root/'artifacts/reference-face-probe/source-boxes.json').read_text(encoding='utf-8'))
chosen={e['sourceGuid'] for e in probe['elements']}
sample=dict(m);sample['elements']=[e for e in m['elements'] if e['sourceGuid'] in chosen]
sample['source']=dict(m['source'],scope='reference offset calibration subset');sample['issues']=[]
keys={e['payload']['typeKey'] for e in sample['elements']};sample['types']=[t for t in m['types'] if t['key'] in keys]
materials={l['materialKey'] for t in sample['types'] for l in t['layers']}
sample['materials']=[a for a in m['materials'] if guid(a.get('attributeId')) in materials]
sample.pop('manifestHash',None);sample['manifestHash']=digest(sample)
out=root/'artifacts/runtime/offset-fixture';write_json(out/'manifest.json',sample);write_json(out/'mapping.json',dict(config,connectWalls=False))
print('Walls ready',sum(e['kind']=='Wall' and e['status']=='ready' for e in m['elements']),'offset fixture',len(sample['elements']))
