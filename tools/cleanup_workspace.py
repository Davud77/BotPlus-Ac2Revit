"""Remove owned intermediate models/builds while retaining source inputs and current evidence."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import os
import stat
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
KEEP = {
    'artifacts/stages/complete-pipeline-all-20261015-142732/Abakan_complete.rvt',
    'artifacts/stages/complete-pipeline-all-20261016-120342/Abakan_complete.rvt',
    'artifacts/stages/complete-pipeline-all-20261016-135339/Abakan_complete.rvt',
    'artifacts/runtime/Project1_before_tests_20261013.rvt',
}


def checked(relative):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT or path.is_relative_to(ROOT/'Исходники'):
        raise ValueError('Protected/outside workspace target: '+relative)
    return path


def build_plan():
    rows = []
    retained_packages={'Revit2026-v27','Revit2026-v36','Revit2026-v38','Revit2026-v39'}
    # Always preserve the currently registered local package, including future versions.
    addin=Path(os.environ.get('APPDATA',''))/'Autodesk/Revit/Addins/2026/Ac2Revit.addin'
    if addin.is_file():
        installed=ET.parse(addin).findtext('.//Assembly')
        if installed:
            package=Path(installed).resolve().parent
            if package.parent==(ROOT/'dist').resolve():retained_packages.add(package.name)
    for folder in ['artifacts/stages', 'artifacts/runtime', 'artifacts/quality', 'artifacts/hatches']:
        for path in (ROOT/folder).rglob('*.rvt'):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in KEEP:
                rows.append({'path':rel, 'bytes':path.stat().st_size, 'kind':'file',
                             'reason':'Owned intermediate RVT or numbered test backup; JSON evidence retained'})
    for path in (ROOT/'dist').iterdir():
        if path.is_dir() and (path/'Ac2Revit.addin').is_file() and path.name not in retained_packages:
            rows.append({'path':path.relative_to(ROOT).as_posix(), 'bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()),
                         'kind':'directory', 'reason':'Superseded owned add-in package'})
    for relative in ['vendor/tapir','debug.log','artifacts/build.log']:
        path=ROOT/relative
        if path.exists():
            rows.append({'path':relative,'bytes':sum(p.stat().st_size for p in path.rglob('*') if p.is_file()) if path.is_dir() else path.stat().st_size,
                         'kind':'directory' if path.is_dir() else 'file','reason':'Duplicate research checkout or generated debug/build log'})
    return {'createdUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(ROOT),
            'retainedModels':sorted(KEEP),'protected':['Исходники','research/tapir-source-1.7','artifacts/reference-exchange/exports',
            'all JSON/CSV/HTML audit evidence','pipeline manifests, raw API archives, family assets, source code, Python runtime'],
            'files':rows,'count':len(rows),'bytes':sum(r['bytes'] for r in rows)}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path);p.add_argument('--apply',type=Path);p.add_argument('--resume',action='store_true');a=p.parse_args()
    if bool(a.plan)==bool(a.apply):raise ValueError('Select --plan or --apply')
    if a.plan:
        data=build_plan();a.plan.parent.mkdir(parents=True,exist_ok=True)
        with a.plan.open('x',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        print(json.dumps({k:v for k,v in data.items() if k!='files'},ensure_ascii=True))
    else:
        data=json.loads(a.apply.read_text(encoding='utf-8'));current=build_plan()
        if data['root']!=str(ROOT):raise ValueError('Different workspace')
        previous={r['path']:r for r in data['files']}
        if not a.resume and data['files']!=current['files']:raise ValueError('Cleanup targets changed; create a fresh plan')
        if a.resume:
            for row in current['files']:
                old=previous.get(row['path'])
                if old is None or old['kind']!=row['kind'] or row['bytes']>old['bytes']:raise ValueError('New or enlarged cleanup target')
        protected={r:hashlib.sha256((ROOT/r).read_bytes()).hexdigest() for r in KEEP if (ROOT/r).is_file()}
        # Validate every absolute recursive target before any mutation.
        for row in data['files']:
            target=checked(row['path'])
            if row['kind']=='directory' and not (target.parent==ROOT/'dist' or target==ROOT/'vendor/tapir'):
                raise ValueError('Unexpected recursive cleanup target')
        for row in data['files']:
            path=checked(row['path'])
            if not path.exists():continue
            if row['kind']=='directory':
                def readonly_retry(function, filename, error):
                    child=Path(filename).resolve()
                    if not child.is_relative_to(path) or not isinstance(error, PermissionError):raise error
                    os.chmod(child,stat.S_IWRITE);function(child)
                shutil.rmtree(path,onexc=readonly_retry)
            else:path.unlink()
        for relative,digest in protected.items():
            if hashlib.sha256((ROOT/relative).read_bytes()).hexdigest()!=digest:raise ValueError('Protected model changed')
        data['completedUtc']=datetime.datetime.now(datetime.timezone.utc).isoformat();data['protectedModelSha256']=protected
        out=a.apply.with_name(a.apply.stem.replace('-plan','')+'-completed.json');out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'removedTargets':data['count'],'freedBytes':data['bytes'],'report':str(out)},ensure_ascii=True))
