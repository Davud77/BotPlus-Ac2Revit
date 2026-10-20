"""Bounded-memory entity census for plain STEP IFC exports; no geometry changes."""
import argparse
import collections
from pathlib import Path
import re
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.jsonio import write_json


def profile(path,out):
    start=time.perf_counter();counts=collections.Counter();pattern=re.compile(rb'^#\d+\s*=\s*([A-Z0-9_]+)\s*\(')
    size=path.stat().st_size
    with path.open('rb') as stream:
        if not stream.read(64).lstrip().startswith(b'ISO-10303-21;'):raise ValueError('Expected plain STEP IFC')
        stream.seek(0)
        for line in stream:
            match=pattern.match(line.lstrip())
            if match:counts[match[1].decode('ascii')]+=1
    if path.stat().st_size!=size:raise ValueError('IFC changed during profiling')
    result=dict(bytes=size,entities=sum(counts.values()),entityTypes=dict(counts.most_common()),seconds=time.perf_counter()-start,
        scope='STEP entity declarations on their own lines; diagnostic census, not a geometry or coverage proof')
    write_json(out,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--ifc',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    r=profile(a.ifc,a.out);print(dict(bytes=r['bytes'],entities=r['entities'],seconds=r['seconds'],topEntities=list(r['entityTypes'].items())[:12]))
