"""Prepare exact IFC bodies for physical categories without native adapters."""
import argparse
import collections
import json
from pathlib import Path
import sys
import datetime
import os
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import write_json, file_sha256, canonical_digest
from prepare_column_bodies import prepare as prepare_bodies, body_products
from source_ifc import SourceIfcIndex


def prepare(args,ifc_model=None):
    import ifcopenshell
    import ifcopenshell.geom
    args.out.mkdir(parents=True,exist_ok=getattr(args,'resume',False))
    with args.manifest.open(encoding='utf-8') as stream:m=json.load(stream)
    with args.frame.open(encoding='utf-8') as stream:frame=json.load(stream)
    if frame['projectId']!=m['source']['projectId'] or canonical_digest({k:v for k,v in frame.items() if k!='hash'})!=frame['hash']:
        raise ValueError('Coordinate frame identity/hash mismatch')
    print('Reading IFC for supplemental physical bodies',flush=True)
    model=ifc_model if ifc_model is not None else ifcopenshell.open(str(args.ifc));batches=[];summary=collections.Counter();identities=SourceIfcIndex(m,model)
    ifc_sha=file_sha256(args.ifc)
    settings=ifcopenshell.geom.settings();settings.set('use-world-coords',True);settings.set('mesher-linear-deflection',.00025)
    for kind in ('Roof','Mesh','Stair','Railing','CurtainWall','Object','Shell','Skylight','Lamp'):
        keys=[e['sourceGuid'] for e in m['elements'] if e['kind']==kind]
        for n in range(0,len(keys),100):
            folder=args.out/(kind.lower()+'-'+str(n//100).zfill(4))
            source_keys={k.upper() for k in keys[n:n+100]};body_file=folder/(kind.lower()+'-bodies.json')
            result=None
            if getattr(args,'resume',False) and body_file.exists() and (folder/'audit.json').exists():
                with body_file.open(encoding='utf-8') as stream:previous=json.load(stream)
                with (folder/'audit.json').open(encoding='utf-8') as stream:audit=json.load(stream)
                recorded={row['sourceGuid'].upper() for row in audit[kind.lower()+'s']}
                if previous['projectId']!=m['source']['projectId'] or previous['ifcSha256']!=ifc_sha or previous.get('ifcToSource',{}).get('hash')!=frame['hash'] or recorded!=source_keys:
                    raise ValueError('Existing supplemental batch source/frame mismatch: '+str(folder))
                result=audit['summary'];del previous,audit
            if result is None:
                if folder.exists():
                    moved=args.out/('incomplete-'+folder.name+'-'+datetime.datetime.now().strftime('%H%M%S%f'))
                    if not folder.resolve().is_relative_to(args.out.resolve()) or not moved.resolve().is_relative_to(args.out.resolve()):raise ValueError('Unsafe batch recovery path')
                    folder.rename(moved)
                products={}
                for element in m['elements']:
                    if element['sourceGuid'].upper() not in source_keys:continue
                    matches=identities.matches(element)
                    if len(matches)==1:
                        try:
                            for part in body_products(matches[0],source_owner=identities.source_guid,owner_key=element['sourceGuid'].upper()):products[part.id()]=part
                        except ValueError:pass
                meshes={}
                if products:
                    iterator=ifcopenshell.geom.iterator(settings,model,min(4,os.cpu_count() or 1),include=list(products.values()))
                    if iterator.initialize():
                        while True:
                            shape=iterator.get()
                            meshes[shape.id]=(np.array(shape.geometry.verts).reshape(-1,3),np.array(shape.geometry.faces,dtype=int).reshape(-1,3))
                            if not iterator.next():break
                    del iterator
                result=prepare_bodies(args.manifest,args.ifc,folder,kind=kind,keys=source_keys,ifc_model=model,frame=frame,manifest=m,identities=identities,ifc_sha=ifc_sha,meshes=meshes)
                del meshes
            summary.update(result['statuses'])
            batches.append({'kind':kind,'path':str((folder/(kind.lower()+'-bodies.json')).resolve()),'bodyCount':result['statuses'].get('extracted',0)})
            write_json(args.out/'preparation-progress.json',{'kind':kind,'done':min(n+100,len(keys)),'total':len(keys),'outcomes':dict(summary)})
            print(kind,min(n+100,len(keys)),'/',len(keys),dict(summary),flush=True)
    plan={'format':'ac2r.supplemental/1','projectId':m['source']['projectId'],'sourceManifest':str(args.manifest.resolve()),
          'ifcSha256':ifc_sha,'ifcCoordinateFrame':str(args.frame.resolve()),'batches':batches,'outcomes':dict(summary)}
    if file_sha256(args.ifc)!=ifc_sha:raise ValueError('IFC changed during supplemental preparation')
    write_json(args.out/'supplemental.json',plan)
    print('SUPPLEMENTAL READY',dict(summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',type=Path,required=True);p.add_argument('--ifc',type=Path,required=True)
    p.add_argument('--frame',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args();lease=ROOT/'artifacts/runtime/ifc-reader-lease.json'
    write_json(lease,{'pid':os.getpid(),'operation':'supplemental IFC bodies'})
    try:prepare(args)
    finally:
        if lease.exists() and json.loads(lease.read_text(encoding='utf-8'))['pid']==os.getpid():lease.unlink()
