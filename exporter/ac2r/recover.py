"""Reconstruct completed metadata reads from raw responses after an output failure.

No live requests, inference, or publication of a Revit build plan. A successful
terminal project/story check and exact positional element coverage are required.
"""
import collections
import json
from pathlib import Path

from .client import aligned, ApiError
from .jsonio import archive_reference, file_sha256, write_json
from .normalize import guid, normalize


COMMANDS = {
    'GetIFCIdsOfElements': ('elementIFCIds', 'ifcIdentifiers'),
    'GetRelationsOfElements': ('relations', 'relations'),
    'GetClassificationsOfElements': ('elementClassifications', 'classifications'),
    'GetIFCPropertiesOfElements': ('elementIFCProperties', 'ifc'),
    'GetPropertyValuesOfElements': ('propertyValuesForElements', 'propertyBatches'),
    'GetGDLParametersOfElements': ('gdlParametersOfElements', 'gdlReference'),
}


def read(path):
    with path.open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def body(item):
    response = item['response']
    if response.get('succeeded') is not True:
        raise ApiError(str(response.get('error', 'Archived request failed')))
    result = response.get('result', {})
    if item['request']['command'] == 'API.ExecuteAddOnCommand':
        result = result.get('addOnCommandResponse', {})
    if 'error' in result:
        raise ApiError(str(result['error']))
    return result


def recover(source, project_id):
    source = Path(source)
    report_path = source/'export-report.json'
    old_report = read(report_path)
    if old_report.get('complete') is True:
        return old_report
    snapshot = read(source/'snapshot.json')
    paths = sorted((source/'raw').glob('*.json'))
    metadata = [p for p in paths if p.stem.split('_', 1)[-1] in COMMANDS]
    if not metadata:
        raise ValueError('Нет сохранённых ответов метаданных для восстановления.')
    end = metadata[-1].name
    for command, field in (('GetProjectInfo', 'project'), ('GetStories', 'stories')):
        terminal = [p for p in paths if p.name > end and p.stem.endswith('_'+command)]
        if not terminal or body(read(terminal[-1])) != snapshot[field]:
            raise ValueError('Нет успешной завершающей проверки проекта/этажей: '+command)
    by_id = {row['sourceGuid']: row for row in snapshot['elements']}
    if len(by_id) != len(snapshot['elements']):
        raise ValueError('Duplicate source element GUID in checkpoint')
    # Never duplicate property batches if the same recovery is retried.
    for row in by_id.values():
        for field in ('ifcIdentifiers', 'relations', 'classifications', 'ifc', 'propertyBatches', 'gdl', 'gdlReference'):
            row.pop(field, None)
    coverage = collections.defaultdict(set)
    failures = []
    for index, path in enumerate(metadata):
        command = path.stem.split('_', 1)[1]
        item = read(path)
        params = item['request'].get('parameters', {})
        if item['request']['command'] == 'API.ExecuteAddOnCommand':
            if params['addOnCommandId']['commandName'] != command:
                raise ValueError('Archive command/filename mismatch: '+path.name)
            params = params.get('addOnCommandParameters', {})
        elif item['request']['command'] != 'API.'+command:
            raise ValueError('Archive command/filename mismatch: '+path.name)
        try:
            response = body(item)
        except ApiError as exc:
            failures.append(path.name+': '+str(exc))
            continue
        result_key, field = COMMANDS[command]
        inputs = params['elements']
        reference = archive_reference(path, source, '/response/result/addOnCommandResponse/'+result_key) if field == 'gdlReference' else None
        for ordinal, (elem, value) in enumerate(aligned(response, result_key, inputs)):
            key = guid(elem['elementId'])
            if key not in by_id:
                raise ValueError('Metadata refers to an unknown source GUID: '+key)
            if field != 'propertyBatches' and key in coverage[command]:
                raise ValueError('Duplicate metadata read: '+command+' '+key)
            coverage[command].add(key)
            if reference:
                by_id[key][field] = dict(reference, jsonPointer=reference['jsonPointer']+'/'+str(ordinal))
            elif field == 'propertyBatches':
                by_id[key].setdefault(field, []).append({'properties': params['properties'], 'values': value})
            else:
                by_id[key][field] = value
        del value, response, item
        if (index+1) % 200 == 0:
            print('Восстановление метаданных:', index+1, '/', len(metadata), flush=True)
    expected = set(by_id)
    for command in ('GetIFCIdsOfElements', 'GetClassificationsOfElements'):
        if command == 'GetClassificationsOfElements' and not snapshot.get('classificationSystems', {}).get('classificationSystems'):
            continue
        if coverage[command] != expected:
            raise ValueError('Неполное покрытие метаданных: '+command)
    gdl = old_report.get('gdl', bool(coverage['GetGDLParametersOfElements']))
    if gdl:
        expected_gdl = {k for k, v in by_id.items() if v.get('type') in ('Door', 'Window', 'Object', 'Lamp', 'Zone', 'Skylight', 'Label')}
        if coverage['GetGDLParametersOfElements'] != expected_gdl:
            raise ValueError('Не все GDL-параметры прочитаны; нужен новый экспорт.')
    manifest = normalize(snapshot, project_id)
    report = dict(old_report, complete=True, recoveredFromRaw=True,
        message='Recovered completed API reads; IFC and Revit preparation are separate steps',
        counts=dict(collections.Counter(e['kind'] for e in manifest['elements'])),
        statuses=dict(collections.Counter(e['status'] for e in manifest['elements'])),
        warnings=snapshot.get('warnings', [])+failures, issues=manifest['issues'],
        gdl=gdl, gdlStorage='archivedResponsesWithSha256References',
        properties=old_report.get('properties', any(p.stem.endswith('_GetAllPropertyIdsOfElements') for p in paths)),
        propertyScope=old_report.get('propertyScope', 'notRecordedByLegacyExporter'),
        metadataCoverage={k: len(v) for k, v in coverage.items()},
        inventoryCount=len(snapshot.get('elementInventory', [])),
        snapshotConsistency='archived terminal document/story checks; source must be reverified before IFC/build',
        recoveryPreviousManifestSha256=file_sha256(source/'manifest.json'))
    write_json(source/'snapshot.json', snapshot)
    write_json(source/'manifest.json', manifest)
    # Commit marker written last. No last-completed build pointer is changed.
    write_json(report_path, report)
    return report
