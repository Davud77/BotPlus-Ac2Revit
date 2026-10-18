import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient
import json

api = TapirClient(archive='artifacts/wall-relations-probe', timeout=30)
print(json.dumps(api.call('GetProjectInfo'), ensure_ascii=True), flush=True)
ids = [{'elementId': {'guid': g}} for g in ['37D734D9-543B-409A-94A6-B5E0AA0E1888','88B1C70C-2ED4-4107-B173-9403C91FF37B']]
print(json.dumps(api.call('GetRelationsOfElements', {'elements': ids, 'otherElementType': 'Wall'}), ensure_ascii=True), flush=True)
