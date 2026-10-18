"""Copy the existing CPython standard runtime locally; no downloads or global installation."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def main():
    source=Path(sys.base_prefix).resolve()
    target=ROOT/'runtime/python'
    if target.exists():raise FileExistsError(target)
    if not (source/'python.exe').is_file() or not (source/'Lib/os.py').is_file():
        raise ValueError('Expected an existing Windows CPython distribution')
    target.mkdir(parents=True)
    for path in source.iterdir():
        if path.is_file() and (path.suffix.lower() in ('.dll','.zip') or path.name.lower() in ('python.exe','pythonw.exe','license.txt')):
            shutil.copy2(path,target/path.name)
    for name in ('Lib','DLLs'):
        shutil.copytree(source/name,target/name,ignore=shutil.ignore_patterns('site-packages','__pycache__'))
    rows=[{'path':str(p.relative_to(target)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in target.rglob('*') if p.is_file()]
    (ROOT/'runtime/python-source.json').write_text(json.dumps({'source':str(source),'version':sys.version,
        'scope':'CPython executables, DLLs, standard library and license; no global site-packages',
        'files':rows},indent=2),encoding='utf-8')
    config=ROOT/'.ifc-env/pyvenv.cfg'
    original=config.read_text(encoding='utf-8')
    (ROOT/'runtime/pyvenv.before-local-runtime.cfg').write_text(original,encoding='utf-8')
    updated=[]
    for line in original.splitlines():
        if line.startswith('home ='):line='home = '+str(target)
        elif line.startswith('executable ='):line='executable = '+str(target/'python.exe')
        elif line.startswith('command ='):line='command = local CPython runtime; packages retained in .ifc-env'
        updated.append(line)
    config.write_text('\n'.join(updated)+'\n',encoding='utf-8')
    try:
        subprocess.run([str(ROOT/'.ifc-env/Scripts/python.exe'),'-c',
            'import sys,ssl,json,ifcopenshell,numpy,shapely;print(json.dumps({"base":sys.base_prefix,"ifc":ifcopenshell.version,"numpy":numpy.__version__,"shapely":shapely.__version__}))'],check=True,cwd=ROOT)
    except BaseException:
        config.write_text(original,encoding='utf-8')
        raise
    print(json.dumps({'files':len(rows),'bytes':sum(r['bytes'] for r in rows),'target':str(target)}))


if __name__=='__main__':main()
