"""Read-only Tapir fill/pen catalog, checked against frozen building material assignments."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.client import TapirClient,chunks

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--manifest',type=Path);p.add_argument('--project-id');p.add_argument('--source-pln',type=Path);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=True);api=TapirClient(archive=a.out/'raw',timeout=45)
    project=api.call('GetProjectInfo')
    if a.source_pln and Path(project.get('projectPath','')).resolve()!=a.source_pln.resolve():raise ValueError('Different active PLN')
    if a.manifest:m=json.loads(a.manifest.read_text(encoding='utf-8-sig'))
    else:
        if not a.project_id or not a.source_pln:raise ValueError('Catalog-only export requires explicit project ID and source PLN')
        m={'source':{'projectId':a.project_id},'materials':api.call('GetAttributesByType',{'attributeType':'BuildingMaterial'})['attributes']}
    headers=api.call('GetAttributesByType',{'attributeType':'Fill'})['attributes'];fills=[]
    for batch in chunks([{'attributeId':r['attributeId']} for r in headers],100):
        fills.extend(api.call('GetFills',{'attributeIds':batch})['fills'])
    materials=[]
    for batch in chunks([{'attributeId':r['attributeId']} for r in m['materials']],100):
        materials.extend(api.call('GetBuildingMaterials',{'attributeIds':batch})['buildingMaterials'])
    actual={r['attributeId']['guid'].upper():r for r in materials if 'attributeId' in r};fields=['cutFillIndex','cutFillPen','cutFillBackgroundPen','cutFillOrientation'];differences=[]
    for raw in m['materials']:
        guid=raw['attributeId']['guid'].upper();row=actual.get(guid)
        if row is None or (a.manifest and any(row.get(k)!=raw.get(k) for k in fields)):differences.append({'guid':guid,'expected':{k:raw.get(k) for k in fields},'actual':{k:row.get(k) for k in fields} if row else None})
    ph=api.call('GetAttributesByType',{'attributeType':'PenTable'})['attributes'];tables=[]
    for batch in chunks([{'attributeId':r['attributeId']} for r in ph],100):tables.extend(api.call('GetPenTables',{'attributeIds':batch})['penTables'])
    if api.call('GetProjectInfo')!=project:raise ValueError('Active Archicad project changed')
    result={'format':'ac2r.archicad-hatch-catalog/1','projectId':m['source']['projectId'],'sourceManifestSha256':hashlib.file_digest(a.manifest.open('rb'),'sha256').hexdigest() if a.manifest else None,
            'sourceProject':project,'materialsChecked':len(m['materials']),'materialDifferences':differences,'materials':materials,'fills':fills,'penTables':tables,
            'scope':'frozen material assignment verification' if a.manifest else 'catalog only; no geometry or Revit import tested'}
    (a.out/'archicad-catalog.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'fills':len(fills),'materialsChecked':len(m['materials']),'materialDifferences':len(differences),'penTables':len(tables),'activeModelPenTables':sum(r.get('isActiveForModel') is True for r in tables)},ensure_ascii=True))
    if differences:raise ValueError('Material cut assignments changed relative to frozen source; inspect differences before mapping')
