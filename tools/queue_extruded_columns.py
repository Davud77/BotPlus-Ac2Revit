"""Replace tagged meshes only in a new copy, after native geometry/flex checks."""
import argparse
import datetime
import json
import os
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--limit',type=int,default=6)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--plan',type=Path,required=True)
    a=p.parse_args()
    if a.limit<0:p.error('Negative limit')
    for path in (a.source,a.plan):
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():raise ValueError('Expected existing workspace input')
    inbox=ROOT/'artifacts/runtime/job.json'
    inbox.parent.mkdir(parents=True,exist_ok=True)
    if inbox.exists():raise FileExistsError('Another runtime job is pending')
    plan=json.loads(a.plan.read_text(encoding='utf-8'))
    rows=plan['columns']
    if a.limit:
        # Exercise a profiled member, unequal part tops and holes before filling
        # remaining controls with different section families.
        selected=[]
        for pred in [lambda c:bool(c['profileGuid']),lambda c:any(abs(p['topOffset'])>.01 for p in c['parts']),
                     lambda c:any(len(p['loops'])>1 for p in c['parts'])]:
            match=next((c for c in rows if pred(c) and c not in selected),None)
            if match:selected.append(match)
        seen={c['familyKey'] for c in selected}
        for c in rows:
            if len(selected)>=a.limit:break
            if c['familyKey'] not in seen:seen.add(c['familyKey']);selected.append(c)
        rows=selected[:a.limit]
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/('native-profiles-'+stamp);out.mkdir(parents=True)
    plan['columns']=rows;(out/'extrusions.json').write_text(json.dumps(plan,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    template=ROOT/'revit/assets/AC2R_StructuralColumn.rft'
    if not template.exists():shutil.copy2('C:/ProgramData/Autodesk/RVT 2026/Family Templates/English/Metric Structural Column.rft',template)
    job={'operation':'nativeExtrudedColumnsCopy','sourceRvt':str(a.source.resolve()),'outputRvt':str(out/'Translated_native_profiles.rvt'),
         'templatePath':str(template),'manifestPath':str(out/'extrusions.json'),'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    print(json.dumps(job,indent=2),flush=True)
