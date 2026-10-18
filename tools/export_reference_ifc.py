"""Export an entire source project with an explicit existing IFC translator."""
import argparse
import json
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'exporter'))
from ac2r.client import TapirClient

parser=argparse.ArgumentParser()
parser.add_argument('--out',type=Path,required=True)
parser.add_argument('--translator',default='Точный Экспорт Геометрии')
args=parser.parse_args()
target=args.out.resolve()
if not target.is_relative_to(root.resolve()) or target.suffix.lower()!='.ifc':
    raise ValueError('Export must be an IFC file inside the workspace')
if target.exists():raise FileExistsError(target)
target.parent.mkdir(parents=True,exist_ok=True)
api=TapirClient(archive=target.with_suffix('.api'),timeout=3600)
project=api.call('GetProjectInfo')
expected=(root/'ЖК Абакан Финальная посадка 7.pln').resolve()
if Path(project.get('projectPath','')).resolve()!=expected:
    raise ValueError('Unexpected source project: '+str(project))
translators=api.call('GetIFCExportTranslators')['translators']
if args.translator not in {t['name'] for t in translators}:raise ValueError('Unknown translator')
print('Exporting entire project with',args.translator,flush=True)
result=api.call('IFCFileOperation',{'method':'save','ifcFilePath':str(target),'fileType':'ifc',
                                 'translatorName':args.translator,'elementsToExport':'EntireProject'})
print(json.dumps(result,ensure_ascii=False),flush=True)
if not result.get('success',result.get('succeeded',False)):
    raise RuntimeError('IFC export did not confirm success: '+str(result))
if not target.exists() or target.stat().st_size<1024:raise RuntimeError('Exported file missing/too small')
print('IFC bytes:',target.stat().st_size,flush=True)
