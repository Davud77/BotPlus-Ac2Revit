"""Assemble explicit, reviewable inputs for the normal Import JSON ribbon command."""
import argparse
import datetime
import json
import os
import shutil
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def prepare(out, column_behavior=None, room_separators=None, hatch_mapping=None):
    out.mkdir(parents=True,exist_ok=False)
    template=ROOT/'revit/assets/AC2R_Void.rft'
    if not template.exists():shutil.copy2(Path('C:/ProgramData/Autodesk/RVT 2026/Family Templates/English/Metric Generic Model.rft'),template)
    values={'pipelineFormat':'ac2r.pipeline/1',
        'sourceManifest':'artifacts/runtime/geometry-offsets/manifest.json',
        'mapping':'artifacts/runtime/geometry-offsets/mapping.json',
        'gridsManifest':'artifacts/stages/columns-grids-bodies-20261014/manifest.json',
        'columnNativeManifest':'artifacts/stages/columns-native-20261014-155906/manifest.json',
        'columnNativeMapping':'artifacts/stages/columns-native-20261014-155906/mapping.json',
        'columnBodies':'artifacts/stages/all-column-bodies-20261014/column-bodies.json',
        'columnExtrusions':'artifacts/stages/extruded-preflight-v2-20261015/extrusions.json',
        'columnTemplate':'revit/assets/AC2R_StructuralColumn.rft',
        'beamBodies':'artifacts/stages/all-beam-bodies-20261015/beam-bodies.json',
        'rectangularBeams':'artifacts/stages/native-beams-plan-20261015/native-beams.json',
        'beamFamily':'revit/assets/AC2R_RectangularBeam.rfa',
        'voidTemplate':str(template.relative_to(ROOT)),
        'openingsPlan':'artifacts/stages/openings-plan-v2-20261015/openings.json',
        'wallBodies':'artifacts/stages/wall-completion-bodies-20261015/wall-bodies.json'}
    if column_behavior is not None:
        values['columnBehavior']=str(column_behavior.resolve())
    if room_separators is not None:
        values['roomSeparators']=str(room_separators.resolve())
    if hatch_mapping is not None:
        values['hatchMapping']=str(hatch_mapping.resolve())
    for key,value in list(values.items()):
        if key=='pipelineFormat':continue
        file=ROOT/value
        if not file.is_file():raise FileNotFoundError(file)
        values[key]=os.path.relpath(file,out)
    values.update(rebuildRooms=True,nativeCutBeams=True,roomAuditLimit=20)
    p=out/'pipeline.json';p.write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
    return p


def queue(plan,source,limit,template=None):
    for file in (plan,source or template):
        if file is None:raise ValueError('Source RVT or template required')
        if not file.resolve().is_relative_to(ROOT) or not file.is_file():raise ValueError('Input must exist in workspace')
    inbox=ROOT/'artifacts/runtime/job.json'
    if inbox.exists():raise FileExistsError('Another job is pending')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')
    out=ROOT/'artifacts/stages'/('complete-pipeline-'+('trial-' if limit else 'all-')+stamp);out.mkdir(parents=True,exist_ok=False)
    job={'operation':'pipelineCopy','manifestPath':str(plan.resolve()),'sourceRvt':str(source.resolve()) if source else '',
         'templatePath':str(template.resolve()) if template else '', 'limit':limit,
         'outputRvt':str(out/'Abakan_complete.rvt'),'reportPath':str(out/'report.json'),'resultPath':str(out/'result.json')}
    temp=inbox.with_suffix('.tmp');temp.write_text(json.dumps(job,indent=2),encoding='utf-8');os.replace(temp,inbox)
    return job


if __name__=='__main__':
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare');a.add_argument('--out',type=Path,required=True);a.add_argument('--column-behavior',type=Path);a.add_argument('--room-separators',type=Path);a.add_argument('--hatch-mapping',type=Path)
    a=sub.add_parser('queue');a.add_argument('--plan',type=Path,required=True)
    g=a.add_mutually_exclusive_group(required=True);g.add_argument('--source',type=Path);g.add_argument('--template',type=Path)
    a.add_argument('--limit',type=int,default=0)
    a=p.parse_args()
    print(prepare(a.out,a.column_behavior,a.room_separators,a.hatch_mapping) if a.command=='prepare' else json.dumps(queue(a.plan,a.source,a.limit,a.template),indent=2))
