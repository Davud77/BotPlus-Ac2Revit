import argparse
import datetime
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('source',type=Path)
parser.add_argument('--name',required=True)
parser.add_argument('--category',choices=['walls','columns','beams','doors','windows'],default='walls')
parser.add_argument('--all-meshes',action='store_true',help='Save complete meshes, including every wall')
args=parser.parse_args()
source=args.source.resolve()
if not source.is_relative_to(root) or not source.exists() or source.stat().st_size<10000:
    raise ValueError('Source RVT must be a valid-sized workspace file')
if not args.name.replace('-','').isalnum():raise ValueError('Invalid audit name')
inbox=root/'artifacts/runtime/job.json'
if inbox.exists():raise FileExistsError('Another runtime job is pending')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
folder=root/'artifacts/reference-exchange'/f'{args.name}-{stamp}'
folder.mkdir()
job={'operation':'geometryAudit','sourceRvt':str(source),'geometryCategory':args.category,
     'reportPath':str(folder/(args.category+'.jsonl')),'resultPath':str(folder/'result.json')}
if args.category=='walls' and not args.all_meshes:job['meshKeysPath']=str(root/'artifacts/reference-exchange/priority-wall-guids.json')
temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
print(json.dumps(job,indent=2))
