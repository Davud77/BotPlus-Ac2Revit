"""Rebuild a manifest from an archived snapshot without connecting to Archicad."""
import argparse
import collections
import json
from pathlib import Path
from .normalize import normalize
from .export import write_json


def main():
    p = argparse.ArgumentParser()
    p.add_argument('snapshot')
    p.add_argument('--project-id', required=True)
    args = p.parse_args()
    path = Path(args.snapshot)
    snapshot = json.loads(path.read_text(encoding='utf-8'))
    m = normalize(snapshot, args.project_id)
    write_json(path.parent/'manifest.json', m)
    report_path = path.parent/'export-report.json'
    report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}
    report.update(counts=dict(collections.Counter(e['kind'] for e in m['elements'])),
                  statuses=dict(collections.Counter(e['status'] for e in m['elements'])), issues=m['issues'])
    write_json(report_path, report)
    print(json.dumps({'statuses': report['statuses'], 'types': len(m['types']), 'elements': len(m['elements'])}))


if __name__ == '__main__': main()
