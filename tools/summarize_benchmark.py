"""Produce a local, source-specific report from measured translation attempts."""
import argparse
import collections
import csv
import json
from pathlib import Path


def load(path):
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return {}


def duration(seconds):
    if seconds is None:return '—'
    return f'{seconds/60:.2f} мин'


def latest_readback(folder):
    for path in sorted(folder.glob('actual-audit*.json'),key=lambda p:p.stat().st_mtime,reverse=True):
        data=load(path)
        if isinstance(data.get('axes'),list) and isinstance(data.get('rooms'),list):return data
    return {}


def repair_revisions(suite, attempts):
    """Report bounded repairs separately, without inventing a fresh benchmark."""
    manifests = {(Path(row['session'])/'source/manifest.json').resolve() for _, row in attempts}
    revisions = []
    for file in suite.glob('*/job.json'):
        job = load(file)
        if job.get('operation') not in ('repairPresentationCopy','completeColumnsCopy') or Path(job.get('manifestPath', '')).resolve() not in manifests:
            continue
        result = load(Path(job.get('resultPath', '')))
        report = load(Path(job.get('reportPath', '')))
        output = Path(job.get('outputRvt', ''))
        if result.get('status') not in ('complete', 'completeWithIssues') or not output.is_file() or not report:
            continue
        revisions.append((report.get('CreatedUtc', ''), file.parent, job, result, report))
    return sorted(revisions, key=lambda r:r[0])


def summarize(suite,sources,out):
    attempts=collections.defaultdict(list)
    for file in suite.glob('*/summary.json'):
        row=load(file)
        if row.get('source'):attempts[Path(row['source']).name].append((file,row))
    lines=['# Последовательная проверка перевода PLN → RVT','',
        'Отчёт составлен по сохранённым результатам. Незавершённая попытка не считается готовой моделью. Наличие GUID не доказывает равенство геометрии или нативную редактируемость.','']
    for source in sorted(sources.glob('*.pln')):
        rows=sorted(attempts.get(source.name,[]),key=lambda x:x[1].get('startedUtc',''))
        lines.extend([f'## {source.name}',''])
        if not rows:lines.extend(['Перевод в этой серии ещё не запускался.','']);continue
        successful=[r for r in rows if r[1]['status'] in ('complete','completeWithIssues')]
        file,latest=(successful or rows)[-1];case=file.parent
        revisions=repair_revisions(suite,rows)
        lines.extend([f"Последний результат: **{latest['status']}**. Каталог: `{case.resolve()}`.",'',
            '| Попытка | Результат | Время | Режим |','|---|---|---:|---|'])
        for attempt,result in rows:
            mode='из сохранённого RVT' if result.get('checkpointRecovery') else 'автономная подготовка снимка' if result.get('offlinePreparation') else 'продолжение подготовки' if result.get('warmRecovery') else 'свежий экспорт'
            lines.append(f"| {attempt.parent.name} | {result['status']} | {duration(result.get('seconds'))} | {mode} |")
        if revisions:
            lines.extend(['',f"Последняя исправленная RVT-копия: `{Path(revisions[-1][2]['outputRvt']).resolve()}`. Подробности и ограничения приведены ниже отдельно от замеров полного конвейера."])
        for attempt,_ in rows:
            interventions=load(attempt.parent/'manual-interventions.json')
            for intervention in interventions if isinstance(interventions,list) else []:
                lines.extend(['',f"Пауза в `{attempt.parent.name}`: {intervention.get('message','ручное вмешательство')}. {intervention.get('timingNote','')}"])
        build=load(case/'import-build.json')
        sha=build.get('sha256') or latest.get('importPluginSha256') or latest.get('pluginSha256')
        if sha:lines.extend(['',f'Контрольная сумма сборки импорта: `{sha}`.'])
        pre_import=load(case/'pre-import-changes.json')
        if pre_import:
            lines.extend(['',f"До построения обновлена сборка: {pre_import['exportStartedWithVersion']} → {pre_import['importPreparedVersion']}. Причина: {pre_import['reason']} Полный запуск этой сборки оценивается по итоговому RVT и аудитам; контрольный пример: `{pre_import['nativeFixtureResult']}`."])
        zero_source=load(case/'zero-thickness-source-evidence.json')
        if zero_source:
            lines.extend(['',f"В API явно нулевая толщина у {zero_source['zeroThicknessWalls']} стен; на {zero_source['explicitZoneBoundaryOwners']} из них ссылаются границы {zero_source['zonesReferencingZeroWalls']} исходных зон. Это характеристика источника, не подтверждение построения."])
        lines.extend(['','Ошибки попыток:',''])
        errors=[(p.parent.name,r.get('error','')) for p,r in rows if r.get('error')]
        lines.extend([f'- {name}: {message.replace(chr(92)+"r"+chr(92)+"n",chr(10)).replace(chr(92)+"n",chr(10)).splitlines()[0][:500]}' for name,message in errors] or ['Ошибки обработчика не зафиксированы.'])
        lines.extend(['','Длительности завершённых этапов экспорта и подготовки:','',
            'Времена вложенных этапов не суммируются с временем родительского этапа.','',
            '| Попытка | Этап | Вызовы | Время |','|---|---|---:|---:|'])
        for p,r in rows:
            grouped=collections.defaultdict(lambda:[0,0.])
            for stage in r.get('stages',[]):
                item=grouped[(stage['stage'],stage['status'])];item[0]+=1;item[1]+=stage['seconds']
            for (stage,status),(calls,seconds) in grouped.items():lines.append(f"| {p.parent.name} | {stage} ({status}) | {calls} | {duration(seconds)} |")
        if latest.get('importGateSeconds') is not None:lines.extend(['',f"Пауза перед сборкой для освобождения памяти приложений: {duration(latest['importGateSeconds'])}. Она входит в общее время попытки."])
        coverage=latest.get('coverage',{})
        repaired_readback=latest_readback(revisions[-1][1]) if revisions else {}
        if repaired_readback.get('coverageCounts'):
            coverage=repaired_readback['coverageCounts']
            lines.extend(['','Следующая таблица относится к последней исправленной копии и получена независимым чтением Revit. Времена попыток выше сохранены отдельно.'])
        if coverage:
            lines.extend(['','Фактическое покрытие исходных записей модели:','',
                '| Категория | Источник | Представлено | Отсутствует | Представления Revit |','|---|---:|---:|---:|---|'])
            for kind,c in sorted(coverage.items()):
                classes=', '.join(f'{k}: {v}' for k,v in sorted(c['representations'].items()))
                lines.append(f"| {kind} | {c['source']} | {c['represented']} | {c['missing']} | {classes} |")
        audit_results=list(case.glob('actual-audit*result.json'))
        actual=load(max(audit_results,key=lambda f:f.stat().st_mtime)) if audit_results else {}
        if actual.get('status')=='complete':
            audit=actual['audit'];lines.extend(['',f"Независимое чтение: оси {audit['verifiedAxes']}/{audit['gridRecords']}; проёмы {audit['openingRecords']}; результаты {audit['outcomes']}."])
            if 'roomOutcomes' in audit:lines.append(f"Площади сохранённых помещений после полного конвейера: {audit['roomOutcomes']} (исходных зон {audit['sourceRoomRecords']}).")
            if 'zeroWallBoundaryOutcomes' in audit:lines.append(f"Разделители нулевых стен после сохранения: {audit['zeroWallBoundaryOutcomes']} (ожидаемых записей {audit['zeroWallBoundaryRecords']}).")
            full=load(case/'actual-audit.json');missing=[r for r in full.get('openings',[]) if r['status']!='nativeCutVerified']
            if missing:lines.append(f"Неподтверждённые вырезы: {len(missing)}; из них пустые проёмы {sum(r['empty'] for r in missing)}.")
        hatch_files=list((case/'stages').rglob('hatch-final-audit.json'))
        join_summary=load(case/'wall-join-summary.json')
        if join_summary:
            lines.extend(['',f"Сопряжения стен: проверено концов {join_summary['sourceTopologyCheckedEnds']}; {join_summary['statuses']}. Это проверка соседей по исходной топологии; равенство тел и стыков слоёв требует отдельной геометрической проверки."])
        if hatch_files:
            hatch=load(max(hatch_files,key=lambda f:f.stat().st_mtime));lines.extend(['',
                f"Назначения штриховок материалов: проверено {hatch['verified']}/{hatch['expected']}; аналогов {hatch['analogues']}, пустых {hatch['empty']}, сплошных {hatch['solid']}; ошибок {hatch['failed']}. Растровые текстуры/UV отдельно не подтверждены."])
        coverage_files=list((case/'stages').rglob('source-coverage.json'))
        if coverage_files:
            raw=load(max(coverage_files,key=lambda f:f.stat().st_mtime));reasons=collections.Counter((r['kind'],r['reason'].splitlines()[0]) for r in raw['elements'] if not r['represented'])
            lines.extend(['','Основные причины отсутствия записей:',''])
            for (kind,reason),count in reasons.most_common(10):lines.append(f'- {kind}, {count}: {reason}')
        room_csv=case/'room-analysis/largest-rooms.csv'
        case_diagnosis=load(case/'room-diagnosis/diagnosis.json')
        if case_diagnosis:
            lines.extend(['',f"Диагностика крупнейших непринятых помещений: {case_diagnosis['outcomes']}. Отчёт: `{(case/'room-diagnosis/diagnosis.json').resolve()}`."])
        limitations=collections.Counter()
        for stage in ('profileColumns','rectangularBeams','nativeCutBeams','openingsAndFillings'):
            reports=[load(p).get('Items',[]) for p in (case/'stages').rglob(stage+'.json')]
            for item in max(reports,key=len,default=[]):
                if item.get('Status') in ('retained','blocked','incomplete'):
                    message=item.get('Message','').splitlines()
                    limitations[(stage,message[0] if message else '')]+=1
        if limitations:
            lines.extend(['','Ограничения нативных этапов (сохранённое представление указано в покрытии выше):',''])
            for (stage,reason),count in limitations.most_common(12):lines.append(f'- {stage}, {count}: {reason}')
        if room_csv.exists():
            room_rows=list(csv.DictReader(room_csv.open(encoding='utf-8-sig',newline='')))
            lines.extend(['','Крупнейшие расхождения помещений:','',
                '| Помещение | Источник, м² | Revit, м² | Отклонение, м² |','|---|---:|---:|---:|'])
            for room in room_rows[:5]:lines.append(f"| {room['name']} | {float(room['source_m2']):.2f} | {float(room['revit_m2']):.2f} | {float(room['signed_delta_m2']):+.2f} |")
        session=Path(latest['session']);census=load(case/'inventory-census/summary.json')
        if not census:
            for p,r in rows:
                for candidate in (p.parent/'inventory-census/summary.json',p.parent/'inventory-census-types/summary.json'):
                    found=load(candidate)
                    if found:census=found
        if census:
            lines.extend(['',f"Полная инвентаризация: {census['inventory']} GUID; подробно выгружено {census['detailed']}; вне основного отбора {census['outsideDetailScope']}. Ошибок чтения типов: {len(census['errors'])}.",'',
                'Чертёжные элементы, дочерние части и отдельные модельные записи учитываются раздельно. Вне основного отбора:', '',
                '| Тип | Количество |','|---|---:|'])
            for kind,c in sorted(census['kinds'].items()):
                if c['outsideDetailScope']:lines.append(f"| {kind} | {c['outsideDetailScope']} |")
        lines.extend(['','Пиковая приватная память по процессам (ГиБ):',''])
        for p,r in rows:
            peaks=', '.join(f'{name}: {value/1024**3:.2f}' for name,value in sorted(r.get('peakPrivateBytes',{}).items()))
            lines.append(f'- {p.parent.name}: {peaks or "нет измерений"}.')
        for _,revision,job,result,report in revisions:
            lines.extend(['','Последующая исправленная копия:', '',
                f"**{result['status']}** — `{Path(job['outputRvt']).resolve()}`.",
                f"Время только исправления: {result['seconds']:.2f} с. Это не полный перевод с нуля."])
            created=collections.Counter(i['Kind'] for i in report.get('Items',[]) if i.get('Status')=='created')
            if created:lines.append('Дополнительно создано: '+', '.join(f'{k}: {n}' for k,n in sorted(created.items()))+'.')
            if 'floors' in result:lines.append(f"Фактическое количество перекрытий после исправления: {result['floors']}.")
            lines.append('')
            for item in report.get('Items',[]):
                if item.get('Kind')=='ServiceVoid' or item.get('Status')=='incomplete':
                    message=' '.join(item.get('Message','').splitlines())
                    lines.append(f"- {item['Kind']}: {message}")
            lines.append('')
            readback=latest_readback(revision)
            if readback:
                labels=readback.get('axes',[])
                lines.append(f"Чтение исправленной модели: геометрия осей {readback['verifiedAxes']}/{readback['gridRecords']}; исходные подписи {sum(r.get('visibleLabelVerified') is True for r in labels)}/{len(labels)}.")
                lines.append(f"Вырезы: {readback['outcomes']}. Помещения: {readback['roomOutcomes']}.")
                if readback.get('zeroWallBoundaryRecords'):
                    lines.append(f"Разделители нулевых стен: {readback['zeroWallBoundaryOutcomes']}. Несовпадения с исходными осями не считаются точным переносом кривой; исходная поверхность стены и её номинальная ось учитываются отдельно.")
            diagnosis=load(revision/'room-diagnosis/diagnosis.json')
            if diagnosis:
                lines.append(f"Разбор крупнейших непринятых помещений: {diagnosis['outcomes']}. Сегменты нативных границ внутри исходного контура — диагностический признак, причина ещё требует проверки по связям Archicad.")
            lines.append(f"Детали исправления: `{Path(job['reportPath']).resolve()}`.")
        lines.extend(['',f'Отчёты исходника: `{session.resolve()}`.',''])
    out.parent.mkdir(parents=True,exist_ok=True);out.write_text('\n'.join(lines),encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--suite',type=Path,required=True);p.add_argument('--sources',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True);a=p.parse_args();summarize(a.suite,a.sources,a.out)
