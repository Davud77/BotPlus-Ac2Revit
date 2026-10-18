"""Summarize measured room areas and native wall-end checks from a Revit report."""
import collections
import csv
import json
import statistics
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
report = json.loads(path.read_text(encoding='utf-8'))
areas = report.get('ZoneAreas', [])
joins = report.get('WallJoins', [])
if areas:
    with path.with_suffix('.areas.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(areas[0]))
        writer.writeheader()
        writer.writerows(areas)
positive = [a['AbsoluteDifferenceSquareMeters'] for a in areas if a['RevitAreaSquareMeters'] > 0]
summary = {
    'roomChecks': len(areas),
    'roomStatuses': dict(collections.Counter(a['Status'] for a in areas)),
    'acceptedRooms': sum(a['Accepted'] for a in areas),
    'positiveAreaDifferenceM2': {'min': min(positive), 'median': statistics.median(positive), 'max': max(positive)} if positive else {},
    'wallEndChecks': len(joins),
    'wallEndStatuses': dict(collections.Counter(a['Status'] for a in joins)),
    'wallJoinSummary': [i['Message'] for i in report['Items'] if i['SourceKey'] == 'wall-topology' and i['Status'] == 'joined'],
    'note': 'Wall-end checks are taken at each batch commit; later batches may alter the resulting native neighbor graph.'
}
path.with_suffix('.spatial-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False, indent=2))
