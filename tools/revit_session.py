"""Local worker for the Revit ribbon. No project-specific geometry paths.

The UI calls this with ProcessStartInfo.ArgumentList (no shell). A completed
session is published only after fresh JSON, IFC and every plan are prepared.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'exporter'))
from ac2r.client import TapirClient, aligned, chunks
from ac2r.export import export, write_json
from ac2r.normalize import digest
from ac2r.jsonio import file_sha256


def error_message(exc):
    if isinstance(exc, MemoryError):
        return str(exc) or 'Недостаточно памяти Python при обработке модели. Прочитанные ответы API сохранены в source/raw; подробности в failure.txt.'
    return str(exc) or type(exc).__name__


def verify_source_geometry(manifest_path, api):
    """Before creating a missing IFC, verify every exported physical element."""
    from export_room_separators import same_source
    before_manifest=file_sha256(manifest_path)
    with manifest_path.open(encoding='utf-8') as stream:
        manifest = json.load(stream)
    with (manifest_path.parent/'snapshot.json').open(encoding='utf-8') as stream:
        snapshot = json.load(stream)
    if not same_source(snapshot['stories']['stories'], api.call('GetStories').get('stories')):
        raise ValueError('Изменены исходные этажи. Нужен новый экспорт.')
    attribute_commands = {'Composite': ('GetComposites', 'composites'),
        'BuildingMaterial': ('GetBuildingMaterials', 'buildingMaterials'), 'Surface': ('GetSurfaces', 'surfaces'),
        'Profile': ('GetProfiles', 'profiles'), 'Fill': ('GetFills', 'fills'), 'PenTable': ('GetPenTables', 'penTables')}
    for kind, attr in snapshot.get('attributes', {}).items():
        headers = attr.get('headers', [])
        if not headers:
            continue  # Optional unsupported attribute kinds were empty in the source export.
        current = api.call('GetAttributesByType', {'attributeType': kind}).get('attributes', [])
        if not same_source(headers, current):
            raise ValueError('Изменён состав атрибутов '+kind+'. Нужен новый экспорт.')
        if kind in attribute_commands:
            command, result_key = attribute_commands[kind]
            details = []
            for batch in chunks([{'attributeId': h['attributeId']} for h in headers if 'attributeId' in h], 100):
                details.extend(api.call(command, {'attributeIds': batch}).get(result_key, []))
            if not same_source(attr.get('details', []), details):
                raise ValueError('Изменены исходные конструкции/материалы '+kind+'. Нужен новый экспорт.')
    source_project=snapshot['project']
    if api.call('GetProjectInfo')!=source_project:raise ValueError('Source project changed before geometry verification')
    del snapshot
    print('Исходные этажи, конструкции и атрибуты сверены', flush=True)
    expected = {e['sourceGuid'].upper(): e['raw'] for e in manifest['elements']}
    fields = ['type', 'id', 'floorIndex', 'layerIndex', 'drawIndex', 'hotlinkId', 'details']
    ids = []
    for kind in ('Wall', 'Slab', 'Door', 'Window', 'Zone', 'Column', 'Beam', 'Object', 'Lamp',
                 'Roof', 'Shell', 'Mesh', 'Morph', 'Stair', 'Railing', 'CurtainWall', 'Skylight', 'Opening'):
        rows = api.call('GetElementsByType', {'elementType': kind}, tapir=False).get('elements', [])
        keys = {e['elementId']['guid'].upper() for e in rows}
        wanted = {k for k, raw in expected.items() if raw.get('type') == kind}
        if keys != wanted:
            raise ValueError('Исходная модель изменена: состав '+kind+'. Нужен новый экспорт.')
        ids.extend(rows)
    for index, batch in enumerate(chunks(ids, 100)):
        result = api.call('GetDetailsOfElements', {'elements': batch, 'fields': fields})
        for elem, actual in aligned(result, 'detailsOfElements', batch):
            key = elem['elementId']['guid'].upper()
            wanted = {k: v for k, v in expected[key].items() if k in fields}
            if not same_source(wanted, actual):
                raise ValueError('Исходная геометрия изменена: '+key+'. Нужен новый экспорт.')
        if index % 25 == 0:
            print('Сверка исходной геометрии:', min((index+1)*100, len(ids)), '/', len(ids), flush=True)
    if api.call('GetProjectInfo')!=source_project or file_sha256(manifest_path)!=before_manifest:
        raise ValueError('Source project/manifest changed during geometry verification')
    return dict(complete=True,projectId=manifest['source']['projectId'],sourceProject=source_project,manifestSha256=before_manifest,
                counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])))


def export_ifc(api, project, translator, ifc_path):
    if api.call('GetProjectInfo') != project:
        raise ValueError('Source changed before IFC export')
    print('Экспорт IFC всего проекта: '+translator, flush=True)
    result = api.call('IFCFileOperation', {'method':'save', 'ifcFilePath':str(ifc_path), 'fileType':'ifc',
                      'translatorName':translator, 'elementsToExport':'EntireProject'})
    if not result.get('success', result.get('succeeded', False)) or not ifc_path.is_file() or ifc_path.stat().st_size < 1024:
        raise RuntimeError('Archicad не подтвердил экспорт IFC: '+str(result))


def project_identity(project, registry=None):
    value = project.get('projectPath', '')
    if not value or not Path(value).is_file():
        raise ValueError('Сначала сохраните открытый проект Archicad в PLN.')
    path = Path(value).resolve()
    registry = registry or ROOT/'config/project-registry.local.json'
    if registry.exists():
        for row in json.loads(registry.read_text(encoding='utf-8-sig'))['projects']:
            if (ROOT/row['source']).resolve() == path:
                return row['projectId']
    # A persistent user registry can retain this ID when moving an unknown PLN.
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'ac2r:pln:'+str(path).casefold()))


def probe(port):
    api = TapirClient(port, timeout=12)
    project = api.call('GetProjectInfo')
    return {'port': port, 'project': project, 'projectId': project_identity(project),
            'tapir': api.call('GetAddOnVersion'),
            'translators': api.call('GetIFCExportTranslators').get('translators', [])}


def write_separator_plan(source_path, manifest_path, output):
    from prepare_room_separators import build
    source = json.loads(source_path.read_text(encoding='utf-8-sig'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    plan = build(source, manifest)
    # Native Revit cleanup is recorded separately from immutable source curves,
    # following the accepted separator stage. Never snap the source references.
    plan['nativeCleanupPolicy'] = 'markAdjusted'
    plan['sourceManifestSha256'] = file_sha256(manifest_path)
    plan['sourceEvidence'] = {'path': str(source_path.resolve()), 'sha256': file_sha256(source_path)}
    write_json(output, plan)


def prepare_pipeline(session, manifest_path, ifc_path, assets):
    """Reuse the checked adapters, with exclusively this session's inputs."""
    from prepare_stage_plan import prepare as native_columns
    from prepare_column_bodies import prepare as bodies
    from prepare_extruded_columns import prepare as extrusions
    from prepare_native_beams import prepare as native_beams
    from prepare_openings import prepare as openings
    from prepare_supplemental_bodies import prepare as supplemental
    import ifcopenshell
    for name in ('AC2R_RectangularColumn.rfa', 'AC2R_StructuralColumn.rft',
                 'AC2R_RectangularBeam.rfa', 'AC2R_Void.rft'):
        if not (assets/name).is_file():
            raise FileNotFoundError(assets/name)
    print('Чтение IFC для всех этапов подготовки', flush=True)
    model=ifcopenshell.open(str(ifc_path))
    from ifc_coordinates import infer_frame
    with manifest_path.open(encoding='utf-8') as stream: frame=infer_frame(json.load(stream),model)
    write_json(session/'ifc-coordinate-frame.json',frame)
    print('Точная геометрия IFC: Column',flush=True)
    bodies(manifest_path,ifc_path,session/'column-bodies',kind='Column',ifc_model=model,frame=frame)
    print('Проверка простых колонн и осей IFC', flush=True)
    native_columns(manifest_path, ifc_path, session/'columns-grids',ifc_model=model,column_bodies_path=session/'column-bodies/column-bodies.json',frame=frame)
    native_path = session/'columns-grids/manifest.json'
    checked = json.loads(native_path.read_text(encoding='utf-8'))
    already = {e['sourceGuid'] for e in checked['elements'] if e['kind']=='Column'}
    mapping = {'allowPartial': True, 'zoneMode': 'room', 'roomAreaToleranceSquareMeters': 1.0,
               'connectWalls': True, 'wallJoinToleranceMeters': 0.001, 'families': {}}
    write_json(session/'mapping.json', mapping)
    write_json(session/'columns-mapping.json', dict(mapping, connectWalls=False, automaticSimpleColumns=True,
               columnFamilyFile=str((assets/'AC2R_RectangularColumn.rfa').resolve()), columnWidthParameter='b', columnDepthParameter='h'))
    print('Точная геометрия IFC: Beam',flush=True)
    bodies(manifest_path,ifc_path,session/'beam-bodies',kind='Beam',ifc_model=model,frame=frame)
    print('Распознавание профилей колонн и балок', flush=True)
    extrusions(session/'column-extrusions', session/'column-bodies/column-bodies.json', already)
    native_beams(session/'beam-bodies/beam-bodies.json', session/'native-beams')
    print('Окна, двери и проёмы IFC', flush=True)
    openings(session/'openings', manifest_path=manifest_path, ifc_path=ifc_path,ifc_model=model,frame=frame)
    print('Крыши, лестницы, ограждения и библиотечные объекты IFC',flush=True)
    supplemental(argparse.Namespace(out=session/'supplemental-bodies',manifest=manifest_path,ifc=ifc_path,
        frame=session/'ifc-coordinate-frame.json',resume=False),ifc_model=model)
    plan = {'pipelineFormat': 'ac2r.pipeline/1', 'sourceManifest': str(manifest_path),
            'mapping': str(session/'mapping.json'), 'gridsManifest': str(native_path),
            'columnNativeManifest': str(native_path), 'columnNativeMapping': str(session/'columns-mapping.json'),
            'columnBodies': str(session/'column-bodies/column-bodies.json'),
            'columnExtrusions': str(session/'column-extrusions/extrusions.json'),
            'columnTemplate': str(assets/'AC2R_StructuralColumn.rft'),
            'beamBodies': str(session/'beam-bodies/beam-bodies.json'),
            'rectangularBeams': str(session/'native-beams/native-beams.json'),
            'beamFamily': str(assets/'AC2R_RectangularBeam.rfa'), 'voidTemplate': str(assets/'AC2R_Void.rft'),
            'openingsPlan': str(session/'openings/openings.json'),
            'completeWallsFromIfc': True,
            'ifcCoordinateFrame':str(session/'ifc-coordinate-frame.json'),
            'supplementalBodiesPlan':str(session/'supplemental-bodies/supplemental.json'),
            'completeInitialWallJoins':True,
            'roomSeparators': str(session/'separators.json'),
            'hatchRules': str(ROOT/'config/hatch-analogues.ru.json'),
            'rebuildRooms': True, 'nativeCutBeams': True, 'roomAuditLimit': 20,
            'requireCompleteSeparatorCoverage': False, 'enforceSourceSnapshot': True}
    for key, value in list(plan.items()):
        if isinstance(value, str) and key != 'pipelineFormat':
            path = Path(value).resolve()
            if not path.is_file(): raise FileNotFoundError(path)
            plan[key] = os.path.relpath(path, session)
    write_json(session/'pipeline.json', plan)


def run(args):
    session = args.output.resolve()
    if not session.is_relative_to(ROOT/'artifacts/sessions'):
        raise ValueError('Session must be inside artifacts/sessions')
    if session.exists(): raise FileExistsError(session)
    info = probe(args.port)
    if info['projectId'] != args.project_id or info['project']['projectPath'] != args.project_path:
        raise ValueError('Активный проект Archicad изменился. Подключитесь повторно.')
    names = [t['name'] for t in info['translators']]
    if args.translator not in names: raise ValueError('Выбранный IFC-транслятор отсутствует')
    session.mkdir(parents=True)
    write_json(session/'session.json', dict(format='ac2r.session/1', status='exporting', selectedTranslator=args.translator, **info))
    phase = 'json-export'
    try:
        export_args = argparse.Namespace(output=str(session/'source'), port=args.port, timeout=180,
            resume=False, include_2d=False, limit=None, batch=100, metadata_batch=100,
            property_batch=100, property_scope='custom', properties=args.properties, gdl=getattr(args,'gdl',False), ifc=False,
            project_id=args.project_id)
        export(export_args)
        manifest_path = session/'source/manifest.json'
        api = TapirClient(args.port, session/'ifc-api', timeout=3600)
        phase = 'ifc-export'
        ifc_path = session/'source/model.ifc'
        export_ifc(api, info['project'], args.translator, ifc_path)
        from export_room_separators import extract
        phase = 'room-separators'
        print('Чтение исходных границ помещений', flush=True)
        extract(manifest_path, Path(args.project_path), session/'separator-source', port=args.port)
        write_separator_plan(session/'separator-source/elements.json', manifest_path, session/'separators.json')
        phase='source-capture'
        write_json(session/'source-verification.json',verify_source_geometry(manifest_path,api))
        from source_capture import seal,verify,memory_budget
        capture=seal(session,info)
        budget=memory_budget(ifc_path)
        if budget['defer']:
            manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
            state=dict(format='ac2r.session/1',status='captured',**info,selectedTranslator=args.translator,
                captureHash=capture['hash'],memoryBudget=budget,manifest=str(manifest_path),
                counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])))
            write_json(session/'session.json',state)
            print('Исходные JSON, IFC и границы полностью захвачены. Закройте Archicad и нажмите «Продолжить сеанс»: подготовка проверенного снимка выполняется без подключения к Archicad.',flush=True)
            return state
        phase = 'geometry-preparation'
        prepare_pipeline(session, manifest_path, ifc_path, args.assets.resolve())
        verify(session)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        state = dict(format='ac2r.session/1', status='ready', **info,
            completedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            pipeline=str(session/'pipeline.json'), manifest=str(manifest_path),
            manifestSha256=file_sha256(manifest_path),
            ifcSha256=file_sha256(ifc_path),
            counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])),
            limitations=['Элементы без адаптера сохраняются в JSON и отчёте; это не подтверждение их построения.',
                         'IFC-транслятор должен сохранять GUID в Tag и исходные координаты.',
                         'Во время экспорта нельзя редактировать исходную модель.'])
        write_json(session/'session.json', state)
        # Sole publication point: never points to a partial export or old project.
        write_json(ROOT/'artifacts/sessions/last-completed.json', {'session':str(session/'session.json')})
        print('Готово. План сборки: '+str(session/'pipeline.json'), flush=True)
        return state
    except BaseException as exc:
        write_json(session/'session.json', dict(format='ac2r.session/1', status='failed', error=error_message(exc),
                   errorType=type(exc).__name__, phase=phase, selectedTranslator=args.translator, **info))
        (session/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise


def continue_session(args):
    try:
        return _continue_session(args)
    except BaseException as exc:
        # Include failures before IFC/preparation folders exist, preserving old ready state.
        folder = args.session.resolve()
        if folder.name == 'session.json': folder = folder.parent
        if folder.is_relative_to(ROOT/'artifacts/sessions') and folder.is_dir():
            stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')
            failure = folder/('continuation-failure-'+stamp+'.txt')
            failure.write_text(type(exc).__name__+': '+error_message(exc)+'\n'+traceback.format_exc(), encoding='utf-8')
            print('Диагностика: '+str(failure), file=sys.stderr, flush=True)
        raise


def _continue_session(args):
    """Recover JSON if possible, create only a missing IFC, then prepare anew."""
    session=args.session.resolve()
    if session.name=='session.json':session=session.parent
    if not session.is_relative_to(ROOT/'artifacts/sessions'):raise ValueError('Session is outside the exchange folder')
    state_path=session/'session.json'
    state=json.loads(state_path.read_text(encoding='utf-8-sig'))
    if state.get('format')!='ac2r.session/1':raise ValueError('Выберите session.json сеанса транслятора')
    if (session/'capture.json').is_file():return prepare_captured_session(session,args.assets.resolve())
    manifest_path=session/'source/manifest.json';ifc_path=session/'source/model.ifc'
    report=json.loads((session/'source/export-report.json').read_text(encoding='utf-8'))
    if report.get('complete') is not True:
        from ac2r.recover import recover
        if not (session/'source/snapshot.json').is_file() or not (session/'source/raw').is_dir():
            raise ValueError('Чтение JSON не завершено и архив ответов отсутствует. Нужен новый экспорт Archicad.')
        print('Восстановление JSON из сохранённых ответов API',flush=True)
        recover(session/'source',state['projectId'])
    if ifc_path.exists():
        with ifc_path.open('rb') as stream:
            stream.seek(max(0,ifc_path.stat().st_size-4096))
            if b'END-ISO-10303-21;' not in stream.read():raise ValueError('Файл IFC ещё записывается или повреждён')
    info=probe(args.port)
    if info['projectId']!=state['projectId'] or info['project']['projectPath']!=state['project']['projectPath']:
        raise ValueError('Для проверки исходных данных откройте тот же проект Archicad, что указан в сеансе.')
    if not ifc_path.exists():
        api=TapirClient(args.port, session/('ifc-api-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')), timeout=3600)
        verify_source_geometry(manifest_path,api)
        names=[t['name'] for t in info['translators']]
        translator=state.get('selectedTranslator') or (names[0] if len(names)==1 else None)
        if translator not in names:
            raise ValueError('Не удалось восстановить выбор IFC-транслятора. Выполните новый экспорт.')
        export_ifc(api,info['project'],translator,ifc_path)
    if ifc_path.stat().st_size<1024:raise ValueError('IFC ещё не сохранён. Дождитесь окончания экспорта Archicad.')
    with ifc_path.open('rb') as stream:
        stream.seek(max(0,ifc_path.stat().st_size-4096))
        if b'END-ISO-10303-21;' not in stream.read():raise ValueError('Файл IFC ещё записывается или повреждён')
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    if manifest['source']['projectId']!=state['projectId']:raise ValueError('JSON belongs to a different source project')
    before_manifest=file_sha256(manifest_path)
    before_ifc=file_sha256(ifc_path)
    attempt=session/('prepared-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f'))
    attempt.mkdir()
    write_json(attempt/'attempt.json',{'status':'preparing','sourceManifestSha256':before_manifest,'ifcSha256':before_ifc})
    try:
        from export_room_separators import extract
        print('Проверка источника и чтение разделителей. Повторного IFC-экспорта нет.',flush=True)
        extract(manifest_path,Path(info['project']['projectPath']),attempt/'separator-source',port=args.port)
        write_separator_plan(attempt/'separator-source/elements.json',manifest_path,attempt/'separators.json')
        prepare_pipeline(attempt,manifest_path,ifc_path,args.assets.resolve())
        if before_manifest!=file_sha256(manifest_path) or before_ifc!=file_sha256(ifc_path):
            raise ValueError('Source JSON/IFC changed during preparation')
        state.update(status='ready',pipeline=str(attempt/'pipeline.json'),manifest=str(manifest_path),
            manifestSha256=before_manifest,ifcSha256=before_ifc,preparedFromExistingFiles=True,
            completedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])))
        for obsolete in ('error','errorType','phase'):state.pop(obsolete,None)
        write_json(attempt/'attempt.json',{'status':'ready','sourceManifestSha256':before_manifest,'ifcSha256':before_ifc})
        write_json(state_path,state)
        write_json(ROOT/'artifacts/sessions/last-completed.json',{'session':str(state_path)})
        print('Подготовка завершена: '+str(attempt/'pipeline.json'),flush=True)
    except BaseException as exc:
        write_json(attempt/'attempt.json',{'status':'failed','error':error_message(exc),'errorType':type(exc).__name__,'sourceManifestSha256':before_manifest,'ifcSha256':before_ifc})
        (attempt/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8')
        raise


def prepare_captured_session(session,assets):
    """Offline continuation is permitted only for a sealed, unchanged capture."""
    from source_capture import verify,memory_budget
    session=Path(session).resolve()
    if not session.is_relative_to(ROOT/'artifacts/sessions'):raise ValueError('Session is outside the exchange folder')
    capture=verify(session);state_path=session/'session.json'
    state=json.loads(state_path.read_text(encoding='utf-8-sig'))
    if state['projectId']!=capture['projectId'] or state['project']!=capture['sourceProject']:raise ValueError('Captured session project mismatch')
    budget=memory_budget(session/'source/model.ifc')
    if budget['defer']:raise MemoryError(f"Недостаточно доступной памяти для IFC: оценка {budget['requiredBytes']/1024**3:.1f} ГиБ, доступно {budget['availableCommitBytes']/1024**3:.1f} ГиБ. Закройте Archicad и другие большие модели; захват данных сохранён.")
    attempt=session/('prepared-offline-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f'));attempt.mkdir()
    manifest_path=session/'source/manifest.json';ifc_path=session/'source/model.ifc'
    write_json(attempt/'attempt.json',dict(status='preparing',captureHash=capture['hash']))
    try:
        write_separator_plan(session/'separator-source/elements.json',manifest_path,attempt/'separators.json')
        prepare_pipeline(attempt,manifest_path,ifc_path,Path(assets).resolve());verify(session)
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        state.update(status='ready',pipeline=str(attempt/'pipeline.json'),manifest=str(manifest_path),captureHash=capture['hash'],preparedOffline=True,
            manifestSha256=capture['files']['source/manifest.json'],ifcSha256=capture['files']['source/model.ifc'],
            counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])),completedUtc=datetime.datetime.now(datetime.timezone.utc).isoformat())
        for obsolete in ('error','errorType','phase','memoryBudget'):state.pop(obsolete,None)
        write_json(attempt/'attempt.json',dict(status='ready',captureHash=capture['hash']));write_json(state_path,state)
        write_json(ROOT/'artifacts/sessions/last-completed.json',{'session':str(state_path)})
        print('Готово. План автономной подготовки: '+state['pipeline'],flush=True);return state
    except BaseException as exc:
        write_json(attempt/'attempt.json',dict(status='failed',error=error_message(exc),errorType=type(exc).__name__,captureHash=capture['hash']))
        (attempt/'failure.txt').write_text(traceback.format_exc(),encoding='utf-8');raise


def capture_existing_session(args):
    """Seal completed legacy reads after a fresh full source verification."""
    from source_capture import seal,memory_budget
    session=args.session.resolve()
    if session.name=='session.json':session=session.parent
    if not session.is_relative_to(ROOT/'artifacts/sessions'):raise ValueError('Session is outside the exchange folder')
    state=json.loads((session/'session.json').read_text(encoding='utf-8-sig'))
    if state.get('format')!='ac2r.session/1':raise ValueError('Invalid session')
    info=probe(args.port)
    if info['projectId']!=state['projectId'] or info['project']!=state['project']:raise ValueError('Different source project is open')
    ifc=session/'source/model.ifc'
    with ifc.open('rb') as stream:
        stream.seek(max(0,ifc.stat().st_size-4096))
        if b'END-ISO-10303-21;' not in stream.read():raise ValueError('IFC capture is incomplete')
    api=TapirClient(args.port,session/('capture-verification-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S%f')),timeout=180)
    verification=verify_source_geometry(session/'source/manifest.json',api)
    write_json(session/'source-verification.json',verification)
    capture=seal(session,info)
    state.update(status='captured',captureHash=capture['hash'],capturedUtc=capture['capturedUtc'],memoryBudget=memory_budget(ifc),counts=verification['counts'])
    for obsolete in ('error','errorType','phase'):state.pop(obsolete,None)
    write_json(session/'session.json',state);return state


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('probe'); a.add_argument('--port', type=int, default=19723)
    a.add_argument('--result', type=Path, required=True)
    a = sub.add_parser('export'); a.add_argument('--port', type=int, required=True)
    a.add_argument('--project-id', required=True); a.add_argument('--project-path', required=True)
    a.add_argument('--translator', required=True); a.add_argument('--output', type=Path, required=True)
    a.add_argument('--assets', type=Path, required=True); a.add_argument('--properties', action='store_true')
    a.add_argument('--gdl', action='store_true', help='Full library parameters; may produce gigabytes of data')
    a=sub.add_parser('prepare');a.add_argument('--session',type=Path,required=True);a.add_argument('--port',type=int,default=19723)
    a.add_argument('--assets',type=Path,required=True)
    a=sub.add_parser('capture');a.add_argument('--session',type=Path,required=True);a.add_argument('--port',type=int,default=19723)
    args = p.parse_args()
    try:
        if args.command == 'probe': write_json(args.result, probe(args.port))
        elif args.command=='export': run(args)
        elif args.command=='capture':capture_existing_session(args)
        else:continue_session(args)
        return 0
    except Exception as exc:
        print('ОШИБКА: '+error_message(exc), file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__': sys.exit(main())
