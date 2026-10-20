"""Compare serial metadata batch sizes against an unchanged live source model."""
import argparse
import collections
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.client import TapirClient,aligned,chunks
from ac2r.normalize import guid
from ac2r.jsonio import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--snapshot',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);p.add_argument('--port',type=int,default=19723);a=p.parse_args()
    snapshot=json.loads(a.snapshot.read_text(encoding='utf-8'));api=TapirClient(a.port,a.out/'raw')
    before=api.call('GetProjectInfo')
    if before!=snapshot['project']:raise ValueError('Different source project is open')
    # Include multiple element types and property sets, rather than only walls.
    selected=collections.defaultdict(list)
    for row in snapshot['elements']:
        if len(selected[row['type']])<10:selected[row['type']].append({'elementId':{'guid':row['sourceGuid']}})
    ids=[row for rows in selected.values() for row in rows]
    definitions={guid(r['propertyId']):r for r in snapshot['propertyDefinitions']['properties']}
    results=[];reference=None
    for size in (20,100):
        start=time.perf_counter();values={};calls=0
        for batch in chunks(ids,size):
            groups={};response=api.call('GetAllPropertyIdsOfElements',{'elements':batch},tapir=False);calls+=1
            for element,row in aligned(response,'propertyIdsOfElements',batch):
                properties=row.get('propertyIdsOfElement',{}).get('propertyIds',[])
                keys=tuple(sorted(guid(r['propertyId']) for r in properties if definitions.get(guid(r['propertyId']),{}).get('propertyType')=='Custom'))
                groups.setdefault(keys,[]).append(element)
                values[guid(element['elementId'])]={}
            for keys,elements in groups.items():
                for key_batch in chunks(list(keys),100):
                    response=api.call('GetPropertyValuesOfElements',{'elements':elements,'properties':[{'propertyId':{'guid':k}} for k in key_batch]},tapir=False);calls+=1
                    for element,row in aligned(response,'propertyValuesForElements',elements):
                        properties=row.get('propertyValues',[])
                        if len(properties)!=len(key_batch):raise ValueError('Property value response alignment changed')
                        values[guid(element['elementId'])].update(zip(key_batch,properties))
        if reference is None:reference=values
        equal=values==reference
        results.append(dict(batch=size,seconds=time.perf_counter()-start,calls=calls,elements=len(values),equal=equal))
        if not equal:raise ValueError('Metadata values differ with larger batches')
    if api.call('GetProjectInfo')!=before:raise ValueError('Source changed during benchmark')
    write_json(a.out/'summary.json',dict(status='verified',selectedKinds={k:len(v) for k,v in selected.items()},results=results))
    print(json.dumps(results),flush=True)


if __name__=='__main__':main()
