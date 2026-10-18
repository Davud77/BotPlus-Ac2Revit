"""Screen IFC net meshes against a read-only Revit snapshot; no geometric equality claim."""
import argparse
import collections
import csv
import json
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('ifc_audit',type=Path);parser.add_argument('native_jsonl',type=Path)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
audit=json.loads(args.ifc_audit.read_text(encoding='utf-8'))
native={}
with args.native_jsonl.open(encoding='utf-8-sig') as stream:
    for line in stream:
        row=json.loads(line);key=row['sourceGuid']
        if not key:continue
        if key in native:raise ValueError('Duplicate source wall GUID in Revit')
        native[key]=row
rows=[]
for reference in audit['walls']:
    row={'sourceGuid':reference['sourceGuid'],'ifcGlobalId':reference['ifcGlobalId'],
         'ifcStatus':reference['identityStatus'],'status':'notCompared'}
    actual=native.get(row['sourceGuid'])
    if actual is None:row['status']='missingInRevit'
    elif reference['identityStatus']!='matched':row['status']='missingInIFC'
    elif reference['geometryStatus']!='extracted' or actual['geometryStatus']!='extracted':row['status']='geometryUnavailable'
    else:
        # This particular comparison is deliberately limited to the validated identity coordinate transform.
        if audit['summary']['explicitIFCToArchicad']!={'translationMeters':[0,0,0],'rotationRadians':0}:
            raise ValueError('Nonidentity comparison requires transforming the geometry snapshot explicitly')
        delta=max(abs(a-b) for field in ('boundsMinMeters','boundsMaxMeters') for a,b in zip(reference[field],actual[field]))
        volume=reference.get('approximateMeshVolumeM3')
        usable=volume is not None and actual['solidCount']>0 and actual['meshOnlyCount']==0
        row.update(revitClass=actual['elementClass'],boundsDifferenceMeters=delta,boundsWithin1mm=delta<=0.001,
                   ifcMeshVolumeM3=volume,revitSolidVolumeM3=actual['nativeSolidVolumeM3'] if usable else None,
                   volumeDifferenceM3=abs(volume-actual['nativeSolidVolumeM3']) if usable else None,
                   status='boundsWithinTolerance' if delta<=0.001 else 'boundsMismatch')
    rows.append(row)
args.out.mkdir(parents=True,exist_ok=False)
summary={'sourceCount':len(rows),'revitTaggedWallCount':len(native),'statuses':dict(collections.Counter(r['status'] for r in rows)),
         'boundsToleranceMeters':0.001,'geometryEqualityProven':False,
         'note':'Bounds compare joined net IFC geometry with final Revit geometry. Volume values are screening metrics; tessellation and material-part overlaps limit their interpretation.'}
(args.out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
fields=list(dict.fromkeys(k for r in rows for k in r))
with (args.out/'walls.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(rows)
print(json.dumps(summary,ensure_ascii=False,indent=2))
