"""Compare only physical IFC fillings; empty wall openings are audited separately."""
import argparse
import json
from pathlib import Path
from compare_column_geometry import compare

p=argparse.ArgumentParser();p.add_argument('reference',type=Path);p.add_argument('actual',type=Path)
p.add_argument('--kind',choices=['Door','Window'],required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
data=json.loads(a.reference.read_text(encoding='utf-8'))
data['elements']=[r for r in data['elements'] if r['kind']==a.kind and not r.get('emptyOpening') and 'vertices' in r]
a.out.parent.mkdir(parents=True,exist_ok=True)
reference=a.out.parent/(a.out.name+'-reference.json');reference.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
print(json.dumps(compare(reference,a.actual,a.out,a.kind),indent=2))
