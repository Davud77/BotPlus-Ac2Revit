"""Queue a bounded diagnostic import for the loaded Ac2Revit add-in."""
from pathlib import Path
import datetime
import json
import os
import sys

root = Path(__file__).resolve().parents[1]
case = sys.argv[1] if len(sys.argv) > 1 else 'sample'
if case not in ('sample', 'geometry','join-fixture','geometry-joins','geometry-offsets','offset-fixture'):
    raise SystemExit('Unknown runtime case')
folder = root/'artifacts/runtime'
inbox = folder/'job.json'
if inbox.exists():
    raise SystemExit('A diagnostic job is already pending')
stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
out = folder/case
job = {'operation': 'import', 'manifestPath': str(out/'manifest.json'),
       'templatePath': str(next(root.glob('*.rte'))),
       'outputRvt': str(out/f'Abakan_{case}_v2_{stamp}.rvt'),
       'reportPath': str(out/f'import-v2-{stamp}.json'),
       'resultPath': str(out/f'result-v2-{stamp}.json')}
if case=='geometry-offsets' and '--fresh' not in sys.argv[2:]:
    job['operation']='repairCopy'
    job['sourceRvt']=str(root/'artifacts/runtime/geometry-joins/Abakan_geometry-joins_v2_20261013-120918.rvt')
temp = inbox.with_suffix('.tmp')
temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
os.replace(temp, inbox)
print(json.dumps(job, ensure_ascii=True, indent=2))
