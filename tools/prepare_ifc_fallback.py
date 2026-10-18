"""Prepare only explicitly blocked walls, using the audited IFC net meshes."""
import csv
import hashlib
import json
from pathlib import Path
import numpy as np

root=Path(__file__).resolve().parents[1]
folder=root/'artifacts/reference-exchange'
audit=json.loads((folder/'ifc-priority-full-20261014/audit.json').read_text(encoding='utf-8'))
manifest=json.loads((root/'artifacts/runtime/geometry-offsets/manifest.json').read_text(encoding='utf-8'))
ifc=root/'artifacts/reference-exchange/exports/Abakan_brep_all_materials_20261014.ifc'
if hashlib.sha256(ifc.read_bytes()).hexdigest()!=audit['summary']['ifcSha256']:raise ValueError('Reference IFC changed')
with (folder/'wall-registry.csv').open(encoding='utf-8-sig',newline='') as stream:
    blocked={row['sourceGuid']:row for row in csv.DictReader(stream) if row['status']=='notCreated'}
rows=[]
for row in audit['walls']:
    if row['sourceGuid'] not in blocked:continue
    if row['identityStatus']!='matched' or row['geometryStatus']!='extracted':raise ValueError('Missing geometry for blocked wall')
    mesh=np.load(root/row['mesh'],allow_pickle=False)
    rows.append({'sourceGuid':row['sourceGuid'],'ifcGlobalId':row['ifcGlobalId'],'ifcStepId':row['stepId'],
                 'vertices':[{'x':float(x),'y':float(y),'z':float(z)} for x,y,z in mesh['vertices']],
                 'faces':mesh['faces'].tolist(),'boundsMinMeters':row['boundsMinMeters'],'boundsMaxMeters':row['boundsMaxMeters'],
                 'reason':blocked[row['sourceGuid']]['reason']})
if len(rows)!=len(blocked):raise ValueError('Incomplete blocked-wall coverage')
payload={'projectId':manifest['source']['projectId'],'coordinates':manifest['coordinates'],
         'ifcPath':str(ifc),'ifcSha256':audit['summary']['ifcSha256'],'representation':'tessellatedNetIFC',
         'walls':rows}
out=folder/'fallback-120.json'
out.write_text(json.dumps(payload,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print('Prepared',len(rows),'walls;',out.stat().st_size,'bytes')
