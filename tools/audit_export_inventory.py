"""Account for every source inventory GUID and the extractor's actual selection."""
import argparse
import collections
import csv
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.client import TapirClient,chunks
from ac2r.jsonio import write_json


def audit(session,out,port=19723):
    state=json.loads((session/'session.json').read_text(encoding='utf-8-sig'))
    report=json.loads((session/'source/export-report.json').read_text(encoding='utf-8-sig'))
    raw=session/'source/raw';entry=json.loads(next(raw.glob('*GetAllElements.json')).read_text(encoding='utf-8'))
    inventory=entry['response']['result']['addOnCommandResponse']['elements'];selected=set()
    for path in raw.glob('*GetDetailsOfElements.json'):
        request=json.loads(path.read_text(encoding='utf-8'))['request']['parameters']['addOnCommandParameters']
        for row in request['elements']:selected.add(row['elementId']['guid'].upper())
    if len(selected)!=sum(report['counts'].values()):raise ValueError('Detail selection does not match the completed export')
    out.mkdir(parents=True,exist_ok=False);api=TapirClient(port,out/'api',timeout=90)
    if api.call('GetProjectInfo')!=state['project']:raise ValueError('Different source project is open')
    start=time.perf_counter();counts=collections.defaultdict(lambda:dict(inventory=0,detailed=0,outsideDetailScope=0));seen=set();errors=[]
    with (out/'elements.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.writer(stream);writer.writerow(['sourceGuid','kind','sourceId','detailedExport','reason'])
        for batch in chunks(inventory,2000):
            # The official type lookup rejects many drawing elements. Tapir's
            # header-only details query also identifies dimensions, fills, etc.
            rows=api.call('GetDetailsOfElements',{'elements':batch,'fields':['type','id']})['detailsOfElements']
            if len(rows)!=len(batch):raise ValueError('Type response length mismatch')
            for source,row in zip(batch,rows):
                key=source['elementId']['guid'].upper();kind=row.get('type','Unknown')
                if key in seen:raise ValueError('Duplicate source inventory GUID')
                seen.add(key);yes=key in selected;counts[kind]['inventory']+=1;counts[kind]['detailed']+=int(yes);counts[kind]['outsideDetailScope']+=int(not yes)
                reason='' if yes else 'Inventory only in current detail scope; explicit room separators are read separately'
                if kind=='Unknown':errors.append(dict(sourceGuid=key,response=row));reason='Source type lookup failed'
                writer.writerow([key,kind,row.get('id',''),yes,reason])
            write_json(out/'progress.json',dict(done=len(seen),total=len(inventory),seconds=time.perf_counter()-start))
    if api.call('GetProjectInfo')!=state['project']:raise ValueError('Source changed during inventory audit')
    result=dict(inventory=len(inventory),detailed=len(selected),outsideDetailScope=len(inventory)-len(selected),kinds=dict(counts),
                errors=errors,seconds=time.perf_counter()-start,scope=dict(include2d=report['include2D'],properties=report['properties'],gdl=report['gdl']))
    write_json(out/'summary.json',result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--port',type=int,default=19723);a=p.parse_args();print(json.dumps(audit(a.session,a.out,a.port),ensure_ascii=False))
