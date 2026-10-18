"""Queue a native beam replacement trial in a new workspace copy."""
import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def queue(source,plan,family):
    for p in (source,plan,family):
        if not p.resolve().is_relative_to(ROOT) or not p.is_file():raise ValueError('Input must be a workspace file: '+str(p))
    inbox=ROOT/'artifacts/runtime/job.json'
    inbox.parent.mkdir(parents=True,exist_ok=True)
    if inbox.exists():raise FileExistsError('A runtime job is pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/('native-beams-'+stamp);out.mkdir(parents=True,exist_ok=False)
    job={'operation':'nativeRectangularBeamsCopy','sourceRvt':str(source.resolve()),'manifestPath':str(plan.resolve()),
         'templatePath':str(family.resolve()),'outputRvt':str(out/'Translated_native_beams.rvt'),
         'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    return job


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--family',type=Path,default=ROOT/'revit/assets/AC2R_RectangularBeam.rfa')
    a=p.parse_args();print(json.dumps(queue(a.source,a.plan,a.family),indent=2))
