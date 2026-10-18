"""Queue the geometry-only wall fallback in a NEW RVT copy."""
import datetime
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
inbox=root/'artifacts/runtime/job.json'
if inbox.exists():raise FileExistsError('Runtime job already pending')
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
folder=root/'artifacts/reference-exchange'/('fallback-test-'+stamp);folder.mkdir()
job={'operation':'referenceFallbackCopy',
     'sourceRvt':str(root/'artifacts/runtime/geometry-offsets/Abakan_geometry-offsets_v2_20261013-134145.rvt'),
     'meshManifestPath':str(root/'artifacts/reference-exchange/fallback-120.json'),
     'outputRvt':str(folder/'Abakan_native_with_IFC_walls.rvt'),
     'reportPath':str(folder/'report.json'),'resultPath':str(folder/'result.json')}
temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
print(json.dumps(job,indent=2))
