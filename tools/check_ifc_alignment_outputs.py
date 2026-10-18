"""Independent check that coordinate normalization preserves the IFC meshes."""
import argparse
import json
from pathlib import Path
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import write_json,file_sha256


def check(previous,current):
    with (current/'ifc-coordinate-frame.json').open(encoding='utf-8') as stream:frame=json.load(stream)
    matrix=np.asarray(frame['matrix']);rows=[]
    for kind in ('column','beam'):
        name=kind+'-bodies/'+kind+'-bodies.json'
        with (previous/name).open(encoding='utf-8') as stream:old=json.load(stream)
        with (current/name).open(encoding='utf-8') as stream:new=json.load(stream)
        if old['ifcSha256']!=new['ifcSha256'] or new['ifcToSource']['hash']!=frame['hash']:raise ValueError('Reference/frame mismatch')
        before={e['sourceGuid']:e for e in old['elements']};after={e['sourceGuid']:e for e in new['elements']}
        if set(before)!=set(after):raise ValueError('Source GUID coverage changed')
        maximum=0.;volume_error=0.;faces=0
        for key,a in before.items():
            b=after[key]
            if a['ifcGlobalId']!=b['ifcGlobalId'] or a['faces']!=b['faces']:raise ValueError('IFC identity/topology changed: '+key)
            p=np.array([[v[c] for c in 'xyz'] for v in a['vertices']]);q=np.array([[v[c] for c in 'xyz'] for v in b['vertices']])
            expected=p@matrix[:3,:3].T+matrix[:3,3]
            error=float(np.linalg.norm(expected-q,axis=1).max());maximum=max(maximum,error)
            va=sum(part['approximateVolumeM3'] for part in a['parts']);vb=sum(part['approximateVolumeM3'] for part in b['parts'])
            volume_error=max(volume_error,abs(va-vb));faces+=len(a['faces'])
            if error>1e-8 or abs(va-vb)>max(1e-8,abs(va)*1e-8):raise ValueError('Rigid mesh transfer changed geometry: '+key)
        rows.append({'kind':kind,'bodies':len(before),'triangles':faces,'maxCoordinateResidualMeters':maximum,'maxVolumeDifferenceM3':volume_error})
    return {'status':'passed','frameSha256':file_sha256(current/'ifc-coordinate-frame.json'),
            'anchors':frame['anchors'],'maxAnchorResidualMeters':frame['maxResidualMeters'],
            'translationMeters':frame['translationMeters'],'bodyChecks':rows}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--previous',type=Path,required=True);p.add_argument('--current',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();result=check(args.previous,args.current);write_json(args.out,result);print(json.dumps(result,ensure_ascii=False))
