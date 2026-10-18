"""Small deterministic integration fixture: L/T joints and a vertical non-neighbor."""
import json
from pathlib import Path
import sys
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.geometry import point,curve,loops,interior_point
from ac2r.normalize import digest
from ac2r.export import write_json

root=Path(__file__).resolve().parents[1]
def id(name):return str(uuid.uuid5(uuid.NAMESPACE_URL,'ac2revit/join-test/'+name)).upper()
elements=[]
for name,a,b,level,z in [('south',(0,0),(10,0),'story:0',0),('east',(10,0),(10,10),'story:0',0),
                        ('north',(10,10),(0,10),'story:0',0),('west',(0,10),(0,0),'story:0',0),
                        ('upper',(0,0),(10,0),'story:1',4),('tee',(10,5),(12,5),'story:0',0),
                        ('south2',(20,0),(30,0),'story:0',0),('east2',(30,0),(30,10),'story:0',0),
                        ('north2',(30,10),(20,10),'story:0',0),('west2',(20,10),(20,0),'story:0',0)]:
    payload={'curve':curve(point({'x':a[0],'y':a[1]},z),point({'x':b[0],'y':b[1]},z)),
             'typeKey':'wall:fixture','height':3,'bottomOffset':0,'flipped':False,'referenceLine':'Center','referenceOffset':0,'roomBounding':True}
    e={'sourceGuid':id(name),'kind':'Wall','mark':name,'levelKey':level,'status':'ready','dependencies':[],
       'payload':payload,'raw':{'fixture':True,'name':name},'wallConnections':[]};e['contentHash']=digest(e);elements.append(e)
for name,poly in [('accepted',[(0.1,0.1),(9.9,0.1),(9.9,9.9),(0.1,9.9)]),
                  ('above-limit',[(20.1,0.1),(29.9,0.1),(29.9,9.9),(20.1,9.9)])]:
    profile=loops({'polygonOutline':[{'x':x,'y':y} for x,y in poly]})
    shift=0.3 if name=='above-limit' else 0.1
    shifted_x=profile[0][0]['start']['x']+shift
    profile[0][0]['start']['x']=shifted_x;profile[0][-1]['end']['x']=shifted_x
    e={'sourceGuid':id(name),'kind':'Zone','mark':name,'levelKey':'story:0','status':'ready','dependencies':[],
       'payload':{'loops':profile,'point':interior_point(profile[0]),'name':name,'number':name,'baseOffset':0},'raw':{'fixture':True}}
    e['contentHash']=digest(e);elements.append(e)
m={'schemaVersion':'1.0.0','source':{'application':'Archicad','projectId':id('project'),'scope':'synthetic integration test'},
   'units':{'length':'m','angle':'rad'},'coordinates':{'system':'ArchicadProject','transform':{'translation':{'x':0,'y':0,'z':0},'rotationRadians':0}},
   'levels':[{'key':'story:0','index':0,'name':'Fixture 0','elevation':0},{'key':'story:1','index':1,'name':'Fixture 1','elevation':4}],
   'materials':[],'types':[{'key':'wall:fixture','kind':'Wall','name':'Fixture 200','layers':[{'width':0.2,'function':'Core','materialKey':''}]}],
   'elements':elements,'manifestHash':''}
m['manifestHash']=digest(m)
write_json(root/'artifacts/runtime/join-fixture/manifest.json',m)
write_json(root/'artifacts/runtime/join-fixture/mapping.json',{'allowPartial':True,'zoneMode':'room','roomAreaToleranceSquareMeters':1.0,
                                                          'connectWalls':True,'wallJoinToleranceMeters':0.001,'families':{}})
