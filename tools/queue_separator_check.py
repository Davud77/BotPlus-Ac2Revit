"""Queue an independent native-curve audit, optionally testing a repeated stage in a copy."""
import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--plan',type=Path,required=True)
    p.add_argument('--manifest',type=Path,required=True)
    mode=p.add_mutually_exclusive_group();mode.add_argument('--check-copy',action='store_true');mode.add_argument('--rebuild-rooms',action='store_true')
    p.add_argument('--mapping',type=Path)
    a=p.parse_args()
    for f in (a.source,a.plan,a.manifest):
        if not f.is_file() or not f.resolve().is_relative_to(ROOT):raise ValueError('Expected existing workspace input')
    if a.rebuild_rooms and (a.mapping is None or not a.mapping.is_file() or not a.mapping.resolve().is_relative_to(ROOT)):raise ValueError('Room rebuilding requires an existing workspace mapping')
    inbox=ROOT/'artifacts/runtime/job.json'
    inbox.parent.mkdir(parents=True,exist_ok=True)
    if inbox.exists():raise FileExistsError('Another runtime job is pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/quality'/('separator-rooms-trial' if a.rebuild_rooms else 'separator-repeat' if a.check_copy else 'separator-audit')/stamp
    out.mkdir(parents=True,exist_ok=False)
    job={'operation':'roomSeparatorsAndRoomsCopy' if a.rebuild_rooms else 'roomSeparatorsCheckCopy' if a.check_copy else 'roomSeparatorAudit',
         'sourceRvt':str(a.source.resolve()),'manifestPath':str(a.plan.resolve()),'meshManifestPath':str(a.manifest.resolve()),
         'reportPath':str(out/'separator-audit.json'),'resultPath':str(out/'result.json')}
    if a.check_copy or a.rebuild_rooms:job['outputRvt']=str(out/'Translated_repeat_separators.rvt')
    if a.rebuild_rooms:job['mappingPath']=str(a.mapping.resolve())
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    print(json.dumps(job,indent=2))
