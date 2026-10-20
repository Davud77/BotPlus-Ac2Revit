"""Measure one normal source-to-RVT session; GUI opening is handled separately.

No geometric settings are changed. The same revit_session factory and Revit
pipeline used by the ribbon perform the actual transfer.
"""
import argparse
import collections
import ctypes
from ctypes import wintypes as W
import datetime as dt
import functools
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'exporter'))
from ac2r.jsonio import write_json,file_sha256
import revit_session


def utc():return dt.datetime.now(dt.timezone.utc).isoformat()


def processes():
    """Read process memory and IO counters; no UI interaction or injection."""
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    psapi=ctypes.WinDLL('psapi',use_last_error=True)
    class Entry(ctypes.Structure):
        _fields_=[('size',W.DWORD),('usage',W.DWORD),('pid',W.DWORD),('heap',ctypes.c_size_t),
                  ('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),('flags',W.DWORD),('name',W.WCHAR*260)]
    class Memory(ctypes.Structure):
        _fields_=[('cb',W.DWORD),('faults',W.DWORD)]+[(n,ctypes.c_size_t) for n in
            ('peakWorkingSet','workingSet','peakPagedPool','pagedPool','peakNonPagedPool','nonPagedPool','pagefile','peakPagefile','private')]
    class IO(ctypes.Structure):
        _fields_=[(n,ctypes.c_ulonglong) for n in ('readOperations','writeOperations','otherOperations','readBytes','writeBytes','otherBytes')]
    kernel.CreateToolhelp32Snapshot.argtypes=[W.DWORD,W.DWORD];kernel.CreateToolhelp32Snapshot.restype=W.HANDLE
    kernel.Process32FirstW.argtypes=[W.HANDLE,ctypes.POINTER(Entry)];kernel.Process32NextW.argtypes=kernel.Process32FirstW.argtypes
    kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
    kernel.CloseHandle.argtypes=[W.HANDLE];psapi.GetProcessMemoryInfo.argtypes=[W.HANDLE,ctypes.POINTER(Memory),W.DWORD]
    kernel.GetProcessIoCounters.argtypes=[W.HANDLE,ctypes.POINTER(IO)]
    snapshot=kernel.CreateToolhelp32Snapshot(2,0);rows=[];entry=Entry();entry.size=ctypes.sizeof(entry)
    try:
        more=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while more:
            if entry.name.lower() in ('archicad.exe','revit.exe') or entry.pid==os.getpid():
                handle=kernel.OpenProcess(0x410,False,entry.pid)
                if handle:
                    try:
                        memory=Memory();memory.cb=ctypes.sizeof(memory);io=IO()
                        if psapi.GetProcessMemoryInfo(handle,ctypes.byref(memory),memory.cb):
                            kernel.GetProcessIoCounters(handle,ctypes.byref(io))
                            rows.append(dict(pid=entry.pid,name=entry.name,privateBytes=memory.private,workingSetBytes=memory.workingSet,
                                             peakWorkingSetBytes=memory.peakWorkingSet,readBytes=io.readBytes,writeBytes=io.writeBytes))
                    finally:kernel.CloseHandle(handle)
            more=kernel.Process32NextW(snapshot,ctypes.byref(entry))
    finally:kernel.CloseHandle(snapshot)
    return rows


class Benchmark:
    def __init__(self,folder):
        self.folder=folder;self.start=time.perf_counter();self.phase='preflight';self.events=[];self.peak=collections.defaultdict(int)
        self.stop=threading.Event();self.lock=threading.RLock();self.api=collections.defaultdict(lambda:{'count':0,'seconds':0.,'errors':0})
    def event(self,event,**data):
        row=dict(utc=utc(),elapsedSeconds=time.perf_counter()-self.start,event=event,**data)
        with self.lock:
            with (self.folder/'events.jsonl').open('a',encoding='utf-8') as out:out.write(json.dumps(row,ensure_ascii=False)+'\n')
    def instrument(self,module,name,label):
        original=getattr(module,name)
        @functools.wraps(original)
        def run(*args,**kwargs):
            previous=self.phase;self.phase=label;start=time.perf_counter();self.event('stageStart',stage=label)
            status='complete'
            try:return original(*args,**kwargs)
            except BaseException:status='failed';raise
            finally:
                row=dict(stage=label,seconds=time.perf_counter()-start,status=status);self.events.append(row)
                self.event('stageEnd',**row);self.phase=previous
        setattr(module,name,run)
    def sample(self):
        while not self.stop.is_set():
            try:
                rows=processes()
                for row in rows:self.peak[row['name']]=max(self.peak[row['name']],row['privateBytes'])
                self.event('resources',phase=self.phase,processes=rows,diskFreeBytes=shutil.disk_usage(ROOT).free)
            except Exception as exc:self.event('monitorError',message=str(exc))
            self.stop.wait(5)
    def install(self):
        for module,name,label in [('revit_session','export','jsonExport'),('revit_session','export_ifc','ifcExport'),
            ('revit_session','verify_source_geometry','sourceVerification'),('source_capture','seal','captureSeal'),('source_capture','verify','captureValidation'),
            ('revit_session','prepare_pipeline','geometryPreparation'),('export_room_separators','extract','separatorSource'),
            ('prepare_column_bodies','prepare','bodyPreparation'),('prepare_stage_plan','prepare','nativeColumnsAndAxes'),
            ('prepare_extruded_columns','prepare','columnProfiles'),('prepare_native_beams','prepare','beamProfiles'),
            ('prepare_openings','prepare','openingsPreparation'),('prepare_supplemental_bodies','prepare','supplementalPreparation')]:
            self.instrument(importlib.import_module(module),name,label)
        self.instrument(importlib.import_module('audit_export_inventory'),'audit','inventoryAudit')
        from ac2r.client import TapirClient
        original=TapirClient.call
        @functools.wraps(original)
        def call(client,command,*args,**kwargs):
            start=time.perf_counter();failed=False
            try:return original(client,command,*args,**kwargs)
            except BaseException:failed=True;raise
            finally:
                elapsed=time.perf_counter()-start;row=self.api[command];row['count']+=1;row['seconds']+=elapsed;row['errors']+=int(failed)
                self.event('api',command=command,seconds=elapsed,error=failed)
        TapirClient.call=call


def read_json(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


class ProgressWatch:
    """Report quiet progress without mistaking it for proof of a deadlock."""
    def __init__(self,threshold=180):
        self.threshold=threshold;self.signature=None;self.changed=None;self.reported=False

    def observe(self,result,now):
        signature=tuple(result.get(k) for k in ('status','phase','done','total'))
        if signature!=self.signature or self.changed is None:
            resumed=self.reported;self.signature=signature;self.changed=now;self.reported=False
            return dict(status='resumed',progress=result) if resumed else None
        quiet=now-self.changed
        if result.get('status')=='running' and quiet>=self.threshold and not self.reported:
            self.reported=True
            return dict(status='needsInspection',unchangedSeconds=quiet,progress=result,
                        note='Progress has not changed. Inspect the native application for a dialog or long operation; this is not proof of a hang. No UI action or cancellation was performed.')
        return None


def finalize_capture_handoff(folder):
    """Finish telemetry for an intentionally stopped, completed source capture."""
    folder=Path(folder).resolve();handoff=read_json(folder/'capture-handoff.json');summary=read_json(folder/'summary.json')
    if not folder.is_relative_to(ROOT/'artifacts') or handoff.get('status')!='capturedForOfflinePreparation':raise ValueError('No completed capture handoff')
    if summary.get('status')!='running':raise ValueError('Telemetry already finalized')
    rows=[json.loads(line) for line in (folder/'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    commands=collections.defaultdict(lambda:dict(count=0,seconds=0.,errors=0));peaks=collections.defaultdict(int)
    for row in rows:
        if row['event']=='api':
            item=commands[row['command']];item['count']+=1;item['seconds']+=row['seconds'];item['errors']+=int(row['error'])
        for process in row.get('processes',[]):peaks[process['name']]=max(peaks[process['name']],process['privateBytes'])
    summary.update(status='capturedForOfflinePreparation',handoffReason=handoff['reason'],completedUtc=handoff['utc'],
        seconds=handoff['stageEvent']['elapsedSeconds'],stages=[{k:r[k] for k in ('stage','seconds','status')} for r in rows if r['event']=='stageEnd'],
        apiCommands=dict(commands),peakPrivateBytes=dict(peaks),
        sourceCounts=read_json(Path(summary['session'])/'source/export-report.json')['counts'])
    summary['sourceHashUnchanged']=file_sha256(summary['source'])==summary['sourceSha256'];write_json(folder/'summary.json',summary)
    return dict(status=summary['status'],seconds=summary['seconds'],sourceCounts=summary['sourceCounts'])


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--case',type=Path,required=True)
    p.add_argument('--session',type=Path,required=True);p.add_argument('--translator',required=True);p.add_argument('--port',type=int,default=19723)
    p.add_argument('--assets',type=Path,required=True);p.add_argument('--template',type=Path,required=True);p.add_argument('--properties',action='store_true')
    p.add_argument('--gdl',action='store_true');p.add_argument('--job-version',type=int,default=46)
    p.add_argument('--resume-session',action='store_true',help='Use the ordinary Continue session preparation without re-exporting completed JSON/IFC')
    p.add_argument('--checkpoint-rvt',type=Path,help='Continue the same prepared source snapshot from a saved RVT checkpoint')
    p.add_argument('--import-gate',type=Path,help='After preparation, wait for this local signal before starting Revit import')
    p.add_argument('--offline-session',action='store_true',help='Prepare a sealed source capture with Archicad closed')
    p.add_argument('--seal-session',action='store_true',help='Seal completed legacy reads after a fresh source check');a=p.parse_args()
    folder=a.case.resolve()
    if not folder.is_relative_to(ROOT/'artifacts') or folder.exists():raise ValueError('Case must be a new artifacts directory')
    folder.mkdir(parents=True);bench=Benchmark(folder);bench.install()
    monitor=threading.Thread(target=bench.sample,daemon=True);monitor.start()
    summary=dict(status='running',source=str(a.source.resolve()),startedUtc=utc(),scope=dict(properties=a.properties,gdl=a.gdl,include2d=False),
                 template=str(a.template.resolve()),session=str(a.session.resolve()),version=a.job_version,warmRecovery=a.resume_session,
                 checkpointRecovery=str(a.checkpoint_rvt.resolve()) if a.checkpoint_rvt else None)
    summary['offlinePreparation']=a.offline_session
    result_path=folder/'revit-result.json'
    try:
        if shutil.disk_usage(ROOT).free<5_000_000_000:raise RuntimeError('Less than 5 GB disk space before export')
        summary['sourceSha256']=file_sha256(a.source)
        summary['pluginSha256']=file_sha256(a.assets.resolve().parent/'Ac2Revit.dll')
        write_json(folder/'summary.json',summary)
        if a.offline_session:
            saved=read_json(a.session/'session.json')
            if Path(saved['project']['projectPath']).resolve()!=a.source.resolve():raise ValueError('Captured session belongs to another source')
            state=revit_session.prepare_captured_session(a.session.resolve(),a.assets.resolve())
        elif a.seal_session:
            saved=read_json(a.session/'session.json')
            if Path(saved['project']['projectPath']).resolve()!=a.source.resolve():raise ValueError('Saved session belongs to another source')
            state=revit_session.capture_existing_session(argparse.Namespace(session=a.session.resolve(),port=a.port))
        elif a.checkpoint_rvt:
            if not a.checkpoint_rvt.is_file():raise FileNotFoundError(a.checkpoint_rvt)
            state=read_json(a.session/'session.json')
            if state.get('status')!='ready':raise ValueError('The saved source session is not ready')
            if Path(state['project']['projectPath']).resolve()!=a.source.resolve():raise ValueError('Checkpoint session belongs to another source')
        else:
            info=revit_session.probe(a.port)
            if Path(info['project']['projectPath']).resolve()!=a.source.resolve():raise ValueError('Different Archicad project is open')
            write_json(folder/'source-probe.json',info)
            args=argparse.Namespace(output=a.session.resolve(),port=a.port,project_id=info['projectId'],project_path=info['project']['projectPath'],
                                    translator=a.translator,assets=a.assets.resolve(),properties=a.properties,gdl=a.gdl)
            if a.resume_session:
                revit_session.continue_session(argparse.Namespace(session=a.session.resolve(),port=a.port,assets=a.assets.resolve()))
                state=read_json(a.session/'session.json')
            else:state=revit_session.run(args)
        summary['exportReadyUtc']=utc();summary['sourceCounts']=state['counts']
        if not a.checkpoint_rvt and not a.offline_session:
            from audit_export_inventory import audit
            census=audit(a.session.resolve(),folder/'inventory-census',a.port)
            summary['inventory']=dict(total=census['inventory'],detailed=census['detailed'],outsideDetailScope=census['outsideDetailScope'],errors=len(census['errors']))
        if state.get('status')=='captured':
            summary.update(status='captured',captureHash=state['captureHash'],memoryBudget=state['memoryBudget'],sourceHashUnchanged=file_sha256(a.source)==summary['sourceSha256'])
            return 0
        state['templatePath']=str(a.template.resolve());write_json(a.session/'session.json',state)
        if a.import_gate:
            gate=a.import_gate.resolve()
            if not gate.is_relative_to(folder) or gate.exists():raise ValueError('Import gate must be a new path inside the case directory')
            bench.phase='waitingForImport';bench.event('exportReady',pipeline=state['pipeline']);gate_start=time.perf_counter()
            write_json(folder/'ready-for-revit.json',dict(status='ready',pipeline=state['pipeline'],gate=str(gate)))
            write_json(folder/'summary.json',summary)
            while not gate.exists():time.sleep(1)
            summary['importGateSeconds']=time.perf_counter()-gate_start
            bench.event('importGateReleased',seconds=summary['importGateSeconds'])
        inbox=ROOT/'artifacts/runtime'/f'job-v{a.job_version}.json'
        if inbox.exists():raise FileExistsError('A Revit job is already pending')
        if shutil.disk_usage(ROOT).free<4_000_000_000:raise RuntimeError('Less than 4 GB disk space before Revit import')
        job=dict(operation='pipelineCopy',manifestPath=state['pipeline'],templatePath=str(a.template.resolve()),outputRvt=str(folder/'model.rvt'),
                 reportPath=str(folder/'pipeline-report.json'),resultPath=str(result_path))
        if a.checkpoint_rvt:job['sourceRvt']=str(a.checkpoint_rvt.resolve())
        summary['importPluginSha256']=file_sha256(a.assets.resolve().parent/'Ac2Revit.dll')
        write_json(folder/'import-build.json',dict(sha256=summary['importPluginSha256'],inboxVersion=a.job_version,queuedUtc=utc()))
        write_json(folder/'revit-job.json',job);inbox.parent.mkdir(parents=True,exist_ok=True)
        temporary=inbox.with_suffix('.tmp');write_json(temporary,job);os.replace(temporary,inbox)
        bench.phase='revitImport';bench.event('revitJobQueued');import_start=time.perf_counter();last_phase=None;seen=False;watch=ProgressWatch()
        while True:
            result=read_json(result_path)
            attention=watch.observe(result,time.perf_counter())
            if attention:
                write_json(folder/'attention.json',dict(utc=utc(),**attention));bench.event('progressAttention',**attention)
                print(json.dumps(attention,ensure_ascii=False),flush=True)
            phase=result.get('phase')
            if phase!=last_phase:bench.event('revitPhase',progress=result);last_phase=phase
            if result.get('status') and result['status']!='running':break
            alive=any(row['name'].lower()=='revit.exe' for row in processes());seen=seen or alive
            if seen and not alive:raise RuntimeError('Revit exited before completing the import; saved RVT checkpoints remain')
            if not seen and time.perf_counter()-import_start>180:raise RuntimeError('Revit is not running; the prepared job remains queued')
            time.sleep(2)
        summary['revit']=result;summary['revitSeconds']=time.perf_counter()-import_start
        if result['status'] not in ('complete','completeWithIssues'):raise RuntimeError('Revit import returned '+str(result))
        coverage_files=list((folder/'stages').rglob('source-coverage.json'))
        if not coverage_files:raise RuntimeError('Final source coverage report is missing')
        coverage=read_json(max(coverage_files,key=lambda f:f.stat().st_mtime));counts={}
        for row in coverage['elements']:
            kind=row['kind'];item=counts.setdefault(kind,dict(source=0,represented=0,missing=0,representations={}))
            item['source']+=1;item['represented']+=int(row['represented']);item['missing']+=int(not row['represented'])
            if row['represented']:item['representations'][row['representation']]=item['representations'].get(row['representation'],0)+1
        summary['coverage']=counts;summary['status']='completeWithIssues' if any(v['missing'] for v in counts.values()) else result['status']
        summary['sourceHashUnchanged']=file_sha256(a.source)==summary['sourceSha256']
    except BaseException as exc:
        summary.update(status='failed',phase=bench.phase,errorType=type(exc).__name__,error=str(exc));(folder/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        print(traceback.format_exc(),flush=True)
    finally:
        bench.stop.set();monitor.join(15);summary.update(completedUtc=utc(),seconds=time.perf_counter()-bench.start,stages=bench.events,
            apiCommands=dict(bench.api),peakPrivateBytes=dict(bench.peak));write_json(folder/'summary.json',summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 1 if summary['status']=='failed' else 0


if __name__=='__main__':raise SystemExit(main())
