"""Seal complete source inputs so IFC preparation no longer needs live Archicad."""
import ctypes
import json
import os
from pathlib import Path
import datetime as dt
import collections

from ac2r.jsonio import canonical_digest,file_sha256,write_json

FILES=('source/manifest.json','source/snapshot.json','source/export-report.json','source/model.ifc',
       'separators.json','separator-source/elements.json','separator-source/export-report.json','source-verification.json')


def seal(session,info):
    session=Path(session)
    read=lambda name:json.loads((session/name).read_text(encoding='utf-8-sig'))
    report=read('source/export-report.json');verification=read('source-verification.json');separator=read('separator-source/export-report.json')
    manifest=read('source/manifest.json')
    if report.get('complete') is not True or verification.get('complete') is not True or separator.get('physicalSourceIdentical') is not True:
        raise ValueError('Source capture has no completed geometry verification')
    if verification.get('manifestSha256')!=file_sha256(session/'source/manifest.json') or verification.get('projectId')!=info['projectId']:
        raise ValueError('Source verification belongs to a different manifest/project')
    if manifest['source']['projectId']!=info['projectId'] or verification.get('sourceProject')!=info['project']:
        raise ValueError('Source capture project mismatch')
    counts=dict(collections.Counter(e['kind'] for e in manifest['elements']))
    plan=read('separators.json')
    if report.get('counts')!=counts or verification.get('counts')!=counts or separator.get('projectId')!=info['projectId']:
        raise ValueError('Source capture inventories differ')
    if plan.get('projectId')!=info['projectId'] or plan.get('sourceManifestSha256')!=verification['manifestSha256']:
        raise ValueError('Separator plan belongs to a different source capture')
    if read('source/snapshot.json').get('project')!=info['project'] or read('separator-source/elements.json').get('projectId')!=info['projectId']:
        raise ValueError('Source capture supplements belong to another project')
    with (session/'source/model.ifc').open('rb') as stream:
        stream.seek(max(0,(session/'source/model.ifc').stat().st_size-4096))
        if b'END-ISO-10303-21;' not in stream.read():raise ValueError('IFC capture is incomplete')
    capture=dict(format='ac2r.source-capture/1',projectId=info['projectId'],sourceProject=info['project'],
                 capturedUtc=dt.datetime.now(dt.timezone.utc).isoformat(),
                 files={name:file_sha256(session/name) for name in FILES})
    source=Path(info['project']['projectPath'])
    if source.is_file():capture['sourcePlnSha256']=file_sha256(source)
    capture['hash']=canonical_digest(capture)
    write_json(session/'capture.json',capture);return capture


def verify(session):
    session=Path(session);capture=json.loads((session/'capture.json').read_text(encoding='utf-8-sig'))
    if capture.get('format')!='ac2r.source-capture/1' or capture.get('hash')!=canonical_digest({k:v for k,v in capture.items() if k!='hash'}):
        raise ValueError('Invalid source capture hash')
    if set(capture['files'])!=set(FILES):raise ValueError('Incomplete source capture file set')
    for name,expected in capture['files'].items():
        if file_sha256(session/name)!=expected:raise ValueError('Captured input changed: '+name)
    return capture


def memory_budget(ifc_path):
    """Conservative preflight against available system commit, not address space."""
    required=Path(ifc_path).stat().st_size*12+1024**3
    if os.name!='nt':return dict(defer=False,requiredBytes=required,availableCommitBytes=None)
    class Status(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(name,ctypes.c_ulonglong) for name in
            ('totalPhys','availPhys','totalPageFile','availPageFile','totalVirtual','availVirtual','availExtendedVirtual')]
    status=Status();status.length=ctypes.sizeof(status)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):raise ctypes.WinError()
    return dict(defer=required>status.availPageFile,requiredBytes=required,availableCommitBytes=status.availPageFile)
