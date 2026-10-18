import csv
import json
from pathlib import Path
import re
import statistics

root=Path(__file__).resolve().parents[1]
path=root/'artifacts/runtime/geometry/import-v2-20261013-090151.json'
report=json.loads(path.read_text(encoding='utf-8'))
rows=[]
for item in report['Items']:
    if item['Kind'] != 'Zone': continue
    match=re.search(r'source ([0-9.,Ee+-]+) m2, Revit ([0-9.,Ee+-]+) m2',item['Message'])
    if match:
        source,actual=[float(v.replace(',','.')) for v in match.groups()]
        delta=abs(actual-source)
        rows.append({'sourceGuid':item['SourceKey'],'sourceAreaM2':source,'revitAreaM2':actual,
                     'absoluteDifferenceM2':delta,'relativeDifferencePercent':100*delta/source,
                     'acceptedAt1M2':delta<=1.0})
out=root/'artifacts/runtime/room-area-analysis'
out.mkdir(parents=True,exist_ok=True)
with (out/'differences.csv').open('w',encoding='utf-8-sig',newline='') as stream:
    writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(sorted(rows,key=lambda r:-r['absoluteDifferenceM2']))
values=[r['absoluteDifferenceM2'] for r in rows]
summary={'baselineReport':str(path),'count':len(rows),'minM2':min(values),'medianM2':statistics.median(values),
         'maxM2':max(values),'within1M2':sum(v<=1 for v in values),'over1M2':sum(v>1 for v in values),
         'notEnclosed':65,'alreadyAccepted':2,'projectedAcceptedBeforeJoins':2+sum(v<=1 for v in values),
         'note':'Based on rounded baseline error messages. Wall joins can change the next results.'}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(summary,ensure_ascii=True,indent=2))
