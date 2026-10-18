"""Add classification names/trees to the existing snapshot (read-only Archicad calls)."""
from pathlib import Path
import sys
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient
from ac2r.export import write_json

root = Path(__file__).resolve().parents[1]
folder = root/'artifacts/abakan-v1'
snapshot = json.loads((folder/'snapshot.json').read_text(encoding='utf-8'))
api = TapirClient(archive=folder/'raw')
if api.call('GetProjectInfo') != snapshot['project']:
    raise RuntimeError('Active project differs from snapshot')
trees = {}
for system in snapshot['classificationSystems'].get('classificationSystems', []):
    key = system['classificationSystemId']['guid']
    trees[key] = api.call('GetAllClassificationsInSystem', {'classificationSystemId': system['classificationSystemId']}, tapir=False)
snapshot['classificationSystems']['trees'] = trees
write_json(folder/'snapshot.json', snapshot)
print('Classification trees:', len(trees))
