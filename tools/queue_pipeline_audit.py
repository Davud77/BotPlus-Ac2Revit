import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--plan',type=Path,required=True);a=p.parse_args()
for file in (a.source,a.plan):
    if not file.resolve().is_relative_to(ROOT) or not file.is_file():raise ValueError('Expected existing workspace input')
inbox=ROOT/'artifacts/runtime/job.json'
if inbox.exists():raise FileExistsError('A runtime job is pending')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S');out=ROOT/'artifacts/reference-exchange'/('pipeline-audit-'+stamp);out.mkdir()
job={'operation':'pipelineAudit','sourceRvt':str(a.source.resolve()),'manifestPath':str(a.plan.resolve()),
     'reportPath':str(out/'audit.json'),'resultPath':str(out/'result.json')}
temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
print(json.dumps(job,indent=2))
