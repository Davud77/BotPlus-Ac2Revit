import json
from pathlib import Path
import sys

root=Path(__file__).resolve().parents[1]
report=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
probe=json.loads((root/'artifacts/reference-face-probe/source-boxes.json').read_text(encoding='utf-8'))
source={e['sourceGuid']:r['boundingBox3D'] for e,r in zip(probe['elements'],probe['response']['boundingBoxes3D'])}
records={p['SourceGuid']:p for p in report.get('WallOffsets',[])}
results=[]
for key,p in records.items():
    if key not in source:continue
    box=source[key];errors={}
    for axis in 'XYZ':
        errors[axis+'Min']=abs(p['BodyMinimum'][axis]-box[axis.lower()+'Min'])
        errors[axis+'Max']=abs(p['BodyMaximum'][axis]-box[axis.lower()+'Max'])
    results.append({'sourceGuid':key,'referenceLine':p['ReferenceLine'],'offsetM':p['ReferenceOffsetMeters'],
                    'errorsM':errors,'maxErrorM':max(errors.values()),'passed':max(errors.values())<1e-6})
out=Path(sys.argv[1]).with_suffix('.calibration.json')
out.write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'compared':len(results),'passed':sum(r['passed'] for r in results),'maxErrorM':max((r['maxErrorM'] for r in results),default=0)},indent=2))
if not results or not all(r['passed'] for r in results):raise SystemExit(1)
