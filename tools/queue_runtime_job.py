"""Queue a diagnostic import from explicit inputs without a source-specific preset."""
import argparse
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def queue(manifest,template,out):
    for path in (manifest,template):
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():raise ValueError('Input must exist inside workspace')
    out=out.resolve()
    if not out.is_relative_to(ROOT) or out==ROOT:raise ValueError('Output must be a new workspace directory')
    inbox=ROOT/'artifacts/runtime/job.json'
    if inbox.exists():raise FileExistsError('A diagnostic job is already pending')
    out.mkdir(parents=True,exist_ok=False);inbox.parent.mkdir(parents=True,exist_ok=True)
    job={'operation':'import','manifestPath':str(manifest.resolve()),'templatePath':str(template.resolve()),
         'outputRvt':str(out/'Translated_model.rvt'),'reportPath':str(out/'import-report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,ensure_ascii=False,indent=2),encoding='utf-8');os.replace(temp,inbox)
    return job

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--template',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(queue(args.manifest,args.template,args.out),ensure_ascii=False,indent=2))
