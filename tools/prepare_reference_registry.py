"""Reconcile the live source census with the latest importer report, one row per GUID."""
import collections
import csv
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
folder=root/'artifacts/reference-exchange'
source=json.loads((folder/'source-20261013/wall-identity.json').read_text(encoding='utf-8'))
manifest=json.loads((root/'artifacts/runtime/geometry-offsets/manifest.json').read_text(encoding='utf-8'))
report=json.loads((root/'artifacts/runtime/geometry-offsets/import-v2-20261013-134145.json').read_text(encoding='utf-8'))
source_keys={r['sourceGuid'] for r in source['walls']}
manifest_keys={r['sourceGuid'] for r in manifest['elements'] if r['kind']=='Wall'}
if not source['complete'] or source_keys!=manifest_keys:
    raise ValueError('Live wall inventory differs from the imported manifest; refresh baseline')
outcomes=collections.defaultdict(list)
for item in report['Items']:
    if item['Kind']=='Wall':outcomes[item['SourceKey']].append(item)
rows=[]
for wall in source['walls']:
    values=outcomes[wall['sourceGuid']]
    created=[i for i in values if i['Status']=='created']
    if len(created)>1:raise ValueError('Duplicate creation for '+wall['sourceGuid'])
    rejected=[i for i in values if i['Status'] in ('blocked','failed','conflict')]
    rows.append({'sourceGuid':wall['sourceGuid'],'ifcGlobalId':wall['ifc'].get('ifcId',''),
                 'status':'native' if created else 'notCreated','revitUniqueId':created[0]['RevitUniqueId'] if created else '',
                 'reason':' | '.join(sorted({i['Message'] for i in rejected}))})
with (folder/'wall-registry.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
problem_keys={r['sourceGuid'] for r in rows if r['status']=='notCreated'}
probe=json.loads((root/'artifacts/reference-face-probe/source-boxes.json').read_text(encoding='utf-8'))
selected=sorted(problem_keys|{e['sourceGuid'] for e in probe['elements']})
(folder/'priority-wall-guids.json').write_text(json.dumps(selected,indent=2),encoding='utf-8')
summary={'sourceCount':len(rows),'dispositions':dict(collections.Counter(r['status'] for r in rows)),
         'reasonCounts':dict(collections.Counter(r['reason'] for r in rows if r['reason'])),
         'priorityGeometryChecks':len(selected),'note':'Inventory equality is checked; model version/parameter equality still requires geometric comparison.'}
(folder/'registry-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=False,indent=2))
