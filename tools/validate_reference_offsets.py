"""Compare analytical face positions with measured Archicad bounding boxes."""
import json
import math
from pathlib import Path

root=Path(__file__).resolve().parents[1]
m=json.loads((root/'artifacts/runtime/geometry-joins/manifest.json').read_text(encoding='utf-8'))
probe=json.loads((root/'artifacts/reference-face-probe/source-boxes.json').read_text(encoding='utf-8'))
types=m['types'];results=[]
for e,row in zip(probe['elements'],probe['response']['boundingBoxes3D']):
    d=e['raw']['details'];box=row['boundingBox3D'];a=d['begCoordinate'];b=d['endCoordinate']
    dx,dy=b['x']-a['x'],b['y']-a['y'];length=math.hypot(dx,dy)
    nx,ny=-dy/length,dx/length
    if abs(nx)>1e-8 and abs(ny)>1e-8: continue # AABB is not a face measurement for oblique walls.
    if 'compositeId' in d:
        t=next(t for t in types if t['kind']=='Wall' and t.get('sourceCompositeGuid')==d['compositeId']['guid'])
        layers=t['layers']
    else:layers=[{'width':d['begThickness'],'function':'Core'}]
    total=sum(l['width'] for l in layers);core=[i for i,l in enumerate(layers) if l['function']=='Core']
    exterior=sum(l['width'] for l in layers[:core[0]]) if core else 0
    core_width=sum(layers[i]['width'] for i in core)
    sign=-1 if d['flipped'] else 1;offset=d.get('offset',0);ref=d['referenceLineLocation']
    distance={'Outside':offset,'Inside':total-offset,'CoreOutside':exterior+offset,
              'CoreInside':exterior+core_width-offset,'CoreCenter':exterior+core_width/2,'Center':total/2}[ref]
    expected=sorted([sign*distance,sign*(distance-total)])
    measured=sorted([(x-a['x'])*nx+(y-a['y'])*ny for x in (box['xMin'],box['xMax']) for y in (box['yMin'],box['yMax'])])
    actual=[measured[0],measured[-1]];error=max(abs(x-y) for x,y in zip(expected,actual))
    results.append({'sourceGuid':e['sourceGuid'],'referenceLine':ref,'flipped':d['flipped'],'offsetM':offset,
                    'expectedNormalRangeM':expected,'measuredNormalRangeM':actual,'maxErrorM':error,'passed':error<1e-6})
assert len(results)==10 and all(r['passed'] for r in results),results
(root/'artifacts/reference-face-probe/calibration.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print('PASS: 10 measured Archicad walls; zero/nonzero offsets and both flip orientations agree within 0.001 mm')
