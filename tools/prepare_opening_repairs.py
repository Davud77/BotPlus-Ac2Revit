"""Recheck blocked physical fillings from a sealed capture into a separate plan."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'exporter'),str(ROOT/'tools')]
from ac2r.jsonio import canonical_digest,file_sha256,write_json
from source_capture import verify,memory_budget
from prepare_openings import prepare as openings


def merge_audit_plan(session,out):
    """A new audit plan must see repaired voids without changing the initial plan."""
    state=json.loads((session/'session.json').read_text(encoding='utf-8'))
    pipeline=Path(state['pipeline']);plan=json.loads(pipeline.read_text(encoding='utf-8'))
    resolve=lambda value:str((pipeline.parent/value).resolve())
    original=json.loads(Path(resolve(plan['openingsPlan'])).read_text(encoding='utf-8'))
    repair=json.loads((out/'fillings/openings.json').read_text(encoding='utf-8'))
    if any(original[k]!=repair[k] for k in ('projectId','ifcSha256','coordinates','ifcToSource')):
        raise ValueError('Repair opening frame/reference differs from original')
    replacements={r['sourceGuid']:r for r in repair['elements']}
    keys={r['sourceGuid'] for r in original['elements']}
    if not set(replacements)<=keys:raise ValueError('Repair contains foreign opening identities')
    combined={**original,'elements':[replacements.get(r['sourceGuid'],r) for r in original['elements']]}
    write_json(out/'audit-openings.json',combined,compact=True)
    path_fields={'sourceManifest','mapping','gridsManifest','columnNativeManifest','columnNativeMapping',
                 'columnBodies','columnExtrusions','columnTemplate','beamBodies','rectangularBeams',
                 'beamFamily','voidTemplate','openingsPlan','ifcCoordinateFrame','supplementalBodiesPlan',
                 'roomSeparators','hatchRules'}
    final={k:resolve(v) if k in path_fields and v else v for k,v in plan.items()}
    final['openingsPlan']=str((out/'audit-openings.json').resolve())
    write_json(out/'audit-pipeline.json',final)


def prepare(session,out):
    if out.exists():raise FileExistsError(out)
    started=time.monotonic();capture=verify(session)
    state=json.loads((session/'session.json').read_text(encoding='utf-8'))
    pipeline=Path(state['pipeline']);plan=json.loads(pipeline.read_text(encoding='utf-8'))
    resolve=lambda value:(pipeline.parent/value).resolve()
    source=session/'source/manifest.json';ifc=session/'source/model.ifc'
    frame=json.loads(resolve(plan['ifcCoordinateFrame']).read_text(encoding='utf-8'))
    if frame['projectId']!=capture['projectId'] or frame['hash']!=canonical_digest({k:v for k,v in frame.items() if k!='hash'}):
        raise ValueError('Invalid IFC coordinate proof')
    old=json.loads(resolve(plan['openingsPlan']).read_text(encoding='utf-8'))
    if old['projectId']!=capture['projectId'] or old['ifcSha256']!=capture['files']['source/model.ifc']:
        raise ValueError('Opening plan belongs to a different source capture')
    keys={r['sourceGuid'].upper() for r in old['elements'] if r['kind'] in ('Door','Window') and r['status']=='blocked'
          and not r.get('reason','').startswith('Empty opening')}
    budget=memory_budget(ifc)
    if budget['defer']:raise MemoryError('Close heavy applications before bounded IFC preparation: '+str(budget))
    out.mkdir(parents=True)
    print(f'Rechecking {len(keys)} blocked physical fillings against the sealed IFC.',flush=True)
    result=openings(out/'fillings',manifest_path=source,ifc_path=ifc,frame=frame,keys=keys)
    merge_audit_plan(session,out)
    verify(session)
    proof=dict(projectId=capture['projectId'],captureHash=capture['hash'],sourceManifestSha256=file_sha256(source),
               sourceManifest=str(source.resolve()),previousPlanSha256=file_sha256(resolve(plan['openingsPlan'])),
               selectedSourceGuids=sorted(keys),fillings=result,seconds=time.monotonic()-started)
    write_json(out/'summary.json',proof);print(json.dumps(dict(fillings=result,seconds=proof['seconds'],output=str(out)),ensure_ascii=False),flush=True)
    return proof


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();prepare(a.session.resolve(),a.out.resolve())
