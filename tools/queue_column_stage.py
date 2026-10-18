"""Queue a columns-only trial in a new copy, retaining every previous RVT."""
import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def queue(mode,source,reference,limit,exclude_report=None):
    for path in (source,reference):
        if not path.resolve().is_relative_to(ROOT) or not path.is_file():raise ValueError('Expected existing workspace input')
    inbox=ROOT/'artifacts/runtime/job.json'
    inbox.parent.mkdir(parents=True,exist_ok=True)
    if inbox.exists():raise FileExistsError('A runtime job is already pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/f'columns-{mode}-{stamp}';out.mkdir(parents=True,exist_ok=False)
    job={'operation':'stageImportCopy' if mode=='native' else 'columnBodyCopy','sourceRvt':str(source.resolve()),
         'outputRvt':str(out/'Translated_columns.rvt'),'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    if mode=='native':
        manifest=json.loads(reference.read_text(encoding='utf-8'))
        rows=[e for e in manifest['elements'] if e['kind']=='Column']
        if limit:
            ordered,seen=[],set()
            for e in rows:
                d=e['payload']['details'];key=(d['width'],d['depth'],d['height'],d['axisRotationAngle'])
                if key not in seen:seen.add(key);ordered.append(e)
            rows=(ordered+[e for e in rows if e not in ordered])[:limit]
        manifest['elements']=rows;manifest['manifestHash']=''
        (out/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
        mapping={'allowPartial':True,'connectWalls':False,'automaticSimpleColumns':True,
                 'columnFamilyFile':str(ROOT/'revit/assets/AC2R_RectangularColumn.rfa'),
                 'columnWidthParameter':'b','columnDepthParameter':'h','families':{}}
        (out/'mapping.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2),encoding='utf-8')
        job.update(manifestPath=str(out/'manifest.json'),referenceIFCPath=manifest['referenceIFC']['path'],referenceIFCSha256=manifest['referenceIFC']['sha256'])
    else:
        data=json.loads(reference.read_text(encoding='utf-8'))
        if exclude_report:
            report=json.loads(exclude_report.read_text(encoding='utf-8'))
            native={r['SourceKey'] for r in report['Items'] if r['Kind']=='Column' and r['Status']=='created'}
            data['elements']=[r for r in data['elements'] if r['sourceGuid'] not in native]
        if limit:
            profiled=[e for e in data['elements'] if 'profileId' in e['rawSource'].get('details',{})]
            simple=[e for e in data['elements'] if 'profileId' not in e['rawSource'].get('details',{})]
            data['elements']=(profiled[:max(1,limit//2)]+simple)[:limit]
        path=out/'column-bodies.json';path.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
        job['meshManifestPath']=str(path)
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    print(json.dumps(job,indent=2),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['native','body']);p.add_argument('--limit',type=int,default=0)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--reference',type=Path,required=True)
    p.add_argument('--exclude-native-report',type=Path)
    a=p.parse_args()
    if a.limit<0:p.error('Negative limit')
    if not a.source.is_file():p.error('Source RVT missing')
    queue(a.mode,a.source,a.reference,a.limit,a.exclude_native_report)
