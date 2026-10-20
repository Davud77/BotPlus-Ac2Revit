"""Prepare missing-slab repairs and independently identified physical fillings.

The sealed source is never rewritten. IFC is read once for the bounded repair.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'exporter'),str(ROOT/'tools')]
from ac2r.jsonio import write_json,canonical_digest,file_sha256
from ac2r.normalize import normalize,digest
from source_capture import verify
from prepare_column_bodies import prepare as bodies
from prepare_openings import prepare as openings


def prepare(session,coverage_path,out):
    import ifcopenshell
    if out.exists():raise FileExistsError(out)
    capture=verify(session);state=json.loads((session/'session.json').read_text(encoding='utf-8'))
    source=session/'source/manifest.json';original=json.loads(source.read_text(encoding='utf-8'))
    coverage=json.loads(coverage_path.read_text(encoding='utf-8'))
    if coverage['projectId']!=original['source']['projectId']:raise ValueError('Coverage belongs to another source')
    missing={e['sourceGuid'].upper() for e in coverage['elements'] if not e['represented']}
    refreshed=normalize(json.loads((session/'source/snapshot.json').read_text(encoding='utf-8')),original['source']['projectId'])
    repairs=[e for e in refreshed['elements'] if e['kind']=='Slab' and e['sourceGuid'].upper() in missing and e['status']=='ready']
    patch={**original,'elements':repairs};patch['manifestHash']=digest({k:v for k,v in patch.items() if k!='manifestHash'})
    out.mkdir(parents=True);write_json(out/'slabs.json',patch)
    pipeline=Path(state['pipeline']);plan=json.loads(pipeline.read_text(encoding='utf-8'))
    resolve=lambda value:(pipeline.parent/value).resolve()
    frame=json.loads(resolve(plan['ifcCoordinateFrame']).read_text(encoding='utf-8'))
    if frame['projectId']!=original['source']['projectId'] or canonical_digest({k:v for k,v in frame.items() if k!='hash'})!=frame['hash']:raise ValueError('Invalid IFC coordinate proof')
    previous=json.loads(resolve(plan['openingsPlan']).read_text(encoding='utf-8'))
    filling_keys={r['sourceGuid'].upper() for r in previous['elements'] if r['sourceGuid'].upper() in missing
                  and r['kind'] in ('Door','Window') and r['status']=='blocked' and not r.get('reason','').startswith('Empty opening')}
    print(f'Bounded repair: {len(repairs)} slabs, {len(filling_keys)} physical filling candidates. Reading IFC once.',flush=True)
    model=ifcopenshell.open(str(session/'source/model.ifc'))
    slab_result=bodies(source,session/'source/model.ifc',out/'slab-reference',kind='Slab',keys={e['sourceGuid'].upper() for e in repairs},ifc_model=model,frame=frame,manifest=original)
    filling_result=openings(out/'fillings',manifest_path=source,ifc_path=session/'source/model.ifc',ifc_model=model,frame=frame,keys=filling_keys)
    proof=dict(projectId=original['source']['projectId'],captureHash=capture['hash'],sourceManifestSha256=file_sha256(source),
               sourceManifest=str(source.resolve()),slabRepairs=len(repairs),slabReference=slab_result,fillings=filling_result)
    verify(session);write_json(out/'summary.json',proof);print(json.dumps(proof,ensure_ascii=False),flush=True)
    return proof


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);p.add_argument('--coverage',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();prepare(a.session.resolve(),a.coverage.resolve(),a.out.resolve())
