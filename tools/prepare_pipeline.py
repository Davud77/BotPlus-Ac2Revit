"""Assemble explicit, reviewable inputs for the normal Import JSON ribbon command."""
import argparse
import datetime
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(out,inputs,column_behavior=None,room_separators=None,hatch_mapping=None):
    """Compose a pipeline from an explicit input map, resolving paths against that map."""
    schema=json.loads((ROOT/'contracts/pipeline.schema.json').read_text(encoding='utf-8'))
    values=json.loads(inputs.read_text(encoding='utf-8-sig'))
    values.setdefault('pipelineFormat','ac2r.pipeline/1')
    if values['pipelineFormat']!='ac2r.pipeline/1':raise ValueError('Unsupported pipeline format')
    missing=set(schema['required'])-values.keys()
    if missing:raise ValueError('Missing pipeline inputs: '+', '.join(sorted(missing)))
    unknown=values.keys()-schema['properties'].keys()
    if unknown:raise ValueError('Unknown pipeline inputs: '+', '.join(sorted(unknown)))
    for key,value in [('columnBehavior',column_behavior),('roomSeparators',room_separators),('hatchMapping',hatch_mapping)]:
        if value is not None:values[key]=str(value.resolve())
    for key,value in list(values.items()):
        if schema['properties'][key].get('$ref')!='#/$defs/path':continue
        file=(inputs.resolve().parent/value).resolve()
        if not file.is_file():raise FileNotFoundError(file)
        values[key]=os.path.relpath(file,out.resolve())
    values.setdefault('rebuildRooms',True);values.setdefault('nativeCutBeams',True);values.setdefault('roomAuditLimit',20)
    out.mkdir(parents=True,exist_ok=False)
    path=out/'pipeline.json';path.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
    return path


def queue(plan,source,limit,template=None):
    for file in (plan,source or template):
        if file is None:raise ValueError('Source RVT or template required')
        if not file.resolve().is_relative_to(ROOT) or not file.is_file():raise ValueError('Input must exist in workspace')
    inbox=ROOT/'artifacts/runtime/job.json'
    inbox.parent.mkdir(parents=True,exist_ok=True)
    if inbox.exists():raise FileExistsError('Another job is pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/('complete-pipeline-'+('trial-' if limit else 'all-')+stamp);out.mkdir(parents=True,exist_ok=False)
    job={'operation':'pipelineCopy','manifestPath':str(plan.resolve()),'sourceRvt':str(source.resolve()) if source else '',
         'templatePath':str(template.resolve()) if template else '', 'limit':limit,
         'outputRvt':str(out/'Translated_model.rvt'),'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    return job


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--inputs',type=Path,required=True);a.add_argument('--out',type=Path,required=True);a.add_argument('--column-behavior',type=Path);a.add_argument('--room-separators',type=Path);a.add_argument('--hatch-mapping',type=Path)
    a=sub.add_parser('queue');a.add_argument('--plan',type=Path,required=True)
    g=a.add_mutually_exclusive_group(required=True);g.add_argument('--source',type=Path);g.add_argument('--template',type=Path)
    a.add_argument('--limit',type=int,default=0)
    a=p.parse_args()
    print(prepare(a.out,a.inputs,a.column_behavior,a.room_separators,a.hatch_mapping) if a.command=='prepare' else json.dumps(queue(a.plan,a.source,a.limit,a.template),indent=2))
