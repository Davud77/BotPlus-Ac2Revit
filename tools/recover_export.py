"""Offline recovery of a JSON export that failed after API metadata reads."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.recover import recover

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--session', type=Path, required=True)
    args = p.parse_args()
    folder = args.session.parent if args.session.name == 'session.json' else args.session
    state = json.loads((folder/'session.json').read_text(encoding='utf-8-sig'))
    report = recover(folder/'source', state['projectId'])
    print(json.dumps({k: report[k] for k in ('complete', 'counts', 'statuses', 'metadataCoverage')}, ensure_ascii=False))
