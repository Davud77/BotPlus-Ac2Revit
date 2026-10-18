from pathlib import Path
import collections
import json
import sys

report_path = Path(sys.argv[1]).resolve()
report = json.loads(report_path.read_text(encoding='utf-8'))
by_kind = collections.defaultdict(collections.Counter)
problems = {}
created_keys = {i['SourceKey'] for i in report['Items'] if i['Status'] == 'created'}
def status(item):
    # Legacy importer records Revit warnings as revitFailure. A successfully committed
    # element proves its message was non-fatal: errors cause transaction rollback.
    if item['Status'] == 'revitFailure' and item['SourceKey'] in created_keys:
        return 'warning'
    return item['Status']
for item in report['Items']:
    by_kind[item['Kind']][status(item)] += 1
    if status(item) in ('failed', 'blocked', 'conflict', 'revitFailure', 'warning'):
        key = status(item)+' | '+item['Kind']+' | '+item['Message']
        group = problems.setdefault(key, {'status': status(item), 'kind': item['Kind'], 'message': item['Message'], 'count': 0, 'examples': []})
        group['count'] += 1
        if len(group['examples']) < 5: group['examples'].append(item['SourceKey'])
summary = {'report': str(report_path), 'createdUtc': report['CreatedUtc'],
           'byKind': {kind: dict(counts) for kind, counts in by_kind.items()},
           'outcomes': dict(collections.Counter(status(i) for i in report['Items'])),
           'warningClassification': 'Legacy revitFailure on a committed element is classified as warning; original report retained.',
           'problems': sorted(problems.values(), key=lambda p: -p['count'])}
report_path.with_suffix('.summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
md = ['# Проверка импорта в Revit 2026', '', f'Отчёт: `{report_path.name}`', '', '| Категория | Результаты |', '|---|---|']
for kind, counts in summary['byKind'].items(): md.append(f'| {kind} | '+', '.join(f'{k}: {v}' for k, v in counts.items())+' |')
md += ['', '## Ошибки и ограничения', '']
for p in summary['problems']:
    md += [f'### {p["status"]} — {p["kind"]}: {p["count"]}', '', p['message'], '', 'Примеры GUID: '+', '.join(p['examples']), '']
report_path.with_suffix('.summary.md').write_text('\n'.join(md), encoding='utf-8')
print(json.dumps({'byKind': summary['byKind'], 'outcomes': summary['outcomes'],
                  'problems': [{k:v for k,v in p.items() if k != 'examples'} for p in summary['problems'][:20]]}, ensure_ascii=True, indent=2))
