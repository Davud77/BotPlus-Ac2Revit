import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient

root=Path(__file__).resolve().parents[1]
m=json.loads((root/'artifacts/runtime/geometry-joins/manifest.json').read_text(encoding='utf-8'))
chosen=[]
for key in ['CoreCenter','Outside','Inside','CoreOutside','CoreInside']:
    candidates=[e for e in m['elements'] if e['kind']=='Wall' and e['raw'].get('details',{}).get('referenceLineLocation')==key
                and abs(e['raw']['details'].get('arcAngle',0))<1e-10 and e['raw']['details'].get('geometryType')=='Straight']
    if key!='CoreCenter': candidates=[e for e in candidates if abs(e['raw']['details'].get('offset',0))>1e-9]
    chosen.extend(candidates[:2])
api=TapirClient(archive=root/'artifacts/reference-face-probe',timeout=60)
ids=[{'elementId':{'guid':e['sourceGuid']}} for e in chosen]
boxes=api.call('Get3DBoundingBoxes',{'elements':ids})
out={'elements':chosen,'response':boxes}
(root/'artifacts/reference-face-probe/source-boxes.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'details':[(e['sourceGuid'],e['raw']['details']['referenceLineLocation'],e['raw']['details']['flipped'],e['raw']['details'].get('offset'),e['raw']['details']['begCoordinate'],e['raw']['details']['endCoordinate']) for e in chosen],'boxes':boxes},ensure_ascii=True)[:12000])
