import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient
import json

api = TapirClient(archive='artifacts/metadata-probe', timeout=30)
walls = json.loads(Path('artifacts/probe/Wall.json').read_text(encoding='utf-8'))['ids'][:1]
for name, args in [('GetAllPropertyIdsOfElements', {'elements': walls}), ('GetAllClassificationSystems', None)]:
    response = api.call(name, args, tapir=False)
    print(name, json.dumps(response, ensure_ascii=True)[:5000], flush=True)
