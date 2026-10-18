"""Read source wall topology into an existing geometry manifest, preserving the original."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient, chunks, aligned
from ac2r.normalize import guid,digest
from ac2r.export import write_json

root=Path(__file__).resolve().parents[1]
source=root/'artifacts/runtime/geometry/manifest.json'
out=root/'artifacts/runtime/geometry-joins'
m=json.loads(source.read_text(encoding='utf-8'))
api=TapirClient(archive=out/'raw',timeout=60)
project=api.call('GetProjectInfo')
expected=m['source'].get('project',{}).get('projectName')
if expected and project.get('projectName') != expected: raise RuntimeError('Wrong active Archicad project')
elements={e['sourceGuid']:e for e in m['elements']}
ids=[{'elementId':{'guid':e['sourceGuid']}} for e in m['elements'] if e['kind']=='Wall']
fields={'connectedToBeginPoint':0,'connectedToEndPoint':1,'connectedWithReferenceLineToEndPoints':None,
        'connectedToReferenceLine':None,'crossingReferenceLine':None}
for offset,batch in enumerate(chunks(ids,100)):
    current=api.call('GetDetailsOfElements',{'elements':batch,'fields':['floorIndex','details']})
    keys=('begCoordinate','endCoordinate','zCoordinate','bottomOffset','height','offset','flipped','referenceLineLocation','begThickness','endThickness','compositeId','buildingMaterialId','profileId','arcAngle')
    for item,row in aligned(current,'detailsOfElements',batch):
        original=elements[guid(item['elementId'])]['raw']
        if row.get('floorIndex')!=original.get('floorIndex') or any(row.get('details',{}).get(k)!=original.get('details',{}).get(k) for k in keys):
            raise RuntimeError('Wall geometry changed since snapshot: '+guid(item['elementId'])+'; re-export before joining')
    response=api.call('GetRelationsOfElements',{'elements':batch,'otherElementType':'Wall'})
    for item,row in aligned(response,'relations',batch):
        e=elements[guid(item['elementId'])];e['raw']['relations']=row;e['wallConnections']=[]
        for relation,neighbors in row.get('wallConnections',{}).items():
            for neighbor in neighbors:
                target=guid(neighbor.get('elementId'))
                if target and target!=e['sourceGuid']:
                    e['wallConnections'].append({'targetGuid':target,'relation':relation,'ownerEnd':fields.get(relation),
                                                 'targetWithBeginPoint':bool(neighbor.get('connectedWithBeginPoint'))})
        e['contentHash']=digest({k:v for k,v in e.items() if k!='contentHash'})
    if offset%10==0:print('Wall relations',min((offset+1)*100,len(ids)),'/',len(ids),flush=True)
m.pop('manifestHash',None);m['manifestHash']=digest(m)
write_json(out/'manifest.json',m)
write_json(out/'mapping.json',{'allowPartial':True,'zoneMode':'room','roomAreaToleranceSquareMeters':1.0,
                              'connectWalls':True,'wallJoinToleranceMeters':0.001,'families':{}})
print('Connections',sum(len(e.get('wallConnections',[])) for e in m['elements']),flush=True)
