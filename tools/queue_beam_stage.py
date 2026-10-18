"""Queue verified beam geometry into a new RVT, never overwriting a previous result."""
import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def queue(source,reference,limit=0):
    source=source.resolve();reference=reference.resolve()
    if not source.is_relative_to(ROOT) or not source.is_file():raise ValueError('Source RVT must exist inside workspace')
    if not reference.is_relative_to(ROOT):raise ValueError('Reference must be inside workspace')
    if limit<0:raise ValueError('Negative limit')
    data=json.loads(reference.read_text(encoding='utf-8'))
    rows=data['elements']
    if not rows or any(e['kind']!='Beam' for e in rows):raise ValueError('Expected beam records')
    if len({e['sourceGuid'].upper() for e in rows})!=len(rows):raise ValueError('Duplicate source GUID')
    if limit:
        # Exercise the sloping member and the most detailed net body first.
        special=[r for r in rows if r['rawSource']['details']['isSlanted']]
        special+=sorted(rows,key=lambda r:len(r['faces']),reverse=True)[:1]
        ordered={r['sourceGuid']:r for r in special+rows}
        data['elements']=list(ordered.values())[:limit]
    inbox=ROOT/'artifacts/runtime/job.json'
    if inbox.exists():raise FileExistsError('A runtime job is pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/('beams-'+('trial' if limit else 'all')+'-'+stamp)
    out.mkdir(parents=True,exist_ok=False)
    mesh=out/'beam-bodies.json'
    mesh.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    job={'operation':'beamBodyCopy','sourceRvt':str(source),'meshManifestPath':str(mesh),
         'outputRvt':str(out/'Abakan_beams.rvt'),'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    return job


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True)
    p.add_argument('--reference',type=Path,default=ROOT/'artifacts/stages/all-beam-bodies-20261015/beam-bodies.json')
    p.add_argument('--limit',type=int,default=0);a=p.parse_args()
    print(json.dumps(queue(a.source,a.reference,a.limit),indent=2))
