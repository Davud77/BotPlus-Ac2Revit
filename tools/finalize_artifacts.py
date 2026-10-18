"""Offline QA and representative manifest subset; no access to model APIs."""
from pathlib import Path
import collections
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.normalize import normalize, guid, digest
from ac2r.export import write_json

root = Path(__file__).resolve().parents[1]
folder = root/'artifacts/abakan-v1'
snapshot = json.loads((folder/'snapshot.json').read_text(encoding='utf-8'))
write_json(folder/'snapshot.json', snapshot)
m = normalize(snapshot, '5e5a43ba-b71f-4cf1-9eb4-bcb98248b8fc')
write_json(folder/'manifest.json', m)
report = json.loads((folder/'export-report.json').read_text(encoding='utf-8'))
report.update(counts=dict(collections.Counter(e['kind'] for e in m['elements'])),
              statuses=dict(collections.Counter(e['status'] for e in m['elements'])), issues=m['issues'])
write_json(folder/'export-report.json', report)
inventory = {}
for e in m['elements']:
    if e['status'] == 'needsMapping':
        p = e['payload']; key = e['kind'] + ':' + (p.get('libraryGuid') or e['sourceGuid'])
        inventory.setdefault(key, {'kind': e['kind'], 'libraryName': p.get('libraryName', ''), 'sourceGuid': e['sourceGuid'], 'count': 0})['count'] += 1
write_json(folder/'mapping-inventory.json', inventory)
chosen = {}
for kind in ['Wall', 'Slab', 'Zone', 'Morph', 'Column', 'Beam', 'Object', 'Door', 'Window']:
    candidates = [e for e in m['elements'] if e['kind'] == kind and e['status'] != 'unsupported']
    if kind in ('Wall', 'Slab'):
        types = {t['key']: t for t in m['types']}
        candidates = [e for e in candidates if all(l['function'] == 'Membrane' or l['width'] >= 0.003175 for l in types[e['payload']['typeKey']]['layers'])]
    if candidates:
        e = min(candidates, key=lambda e: len(json.dumps(e)))
        chosen[e['sourceGuid']] = e
ids = {e['sourceGuid']: e for e in m['elements']}
for e in list(chosen.values()):
    for dependency in e['dependencies']:
        chosen[dependency] = ids[dependency]
sample = dict(m)
sample['source'] = dict(m['source'], scope='explicit representative subset; never use for deletion')
sample['elements'] = list(chosen.values())
type_keys = {e['payload']['typeKey'] for e in chosen.values() if 'typeKey' in e['payload']}
sample['types'] = [t for t in m['types'] if t['key'] in type_keys]
mat_keys = {l['materialKey'] for t in sample['types'] for l in t['layers'] if l['materialKey']}
mat_keys.update(e['payload'].get('materialKey', '') for e in chosen.values())
sample['materials'] = [a for a in m['materials'] if guid(a.get('attributeId')) in mat_keys]
level_keys = {e['levelKey'] for e in chosen.values()}
sample['levels'] = [l for l in m['levels'] if l['key'] in level_keys]
sample['issues'] = []
sample.pop('manifestHash', None)
sample['manifestHash'] = digest(sample)
write_json(root/'artifacts/sample/manifest.json', sample)
properties = [v for e in snapshot['elements'] for b in e.get('propertyBatches', []) for v in b['values'].get('propertyValues', [])]
summary = {'source': snapshot['project'], 'product': snapshot['product'], 'tapir': snapshot['tapir'],
           'levels': len(m['levels']), 'buildingElementCount': len(m['elements']), 'allElementInventoryCount': len(snapshot['elementInventory']),
           'elementCounts': report['counts'], 'normalizedStatuses': report['statuses'], 'constructionCount': len(m['types']),
           'attributeCounts': {k: len(v.get('headers', [])) for k, v in snapshot['attributes'].items()},
           'propertyDefinitionCount': len(snapshot['propertyDefinitions'].get('properties', [])),
           'propertyScope': report.get('propertyScope'), 'typedPropertyEntries': len(properties),
           'typedPropertyValues': sum('propertyValue' in p for p in properties),
           'typedPropertyStatuses': dict(collections.Counter(p.get('propertyValue', {}).get('status', 'error') for p in properties)),
           'elementMetadataCounts': {key: sum(key in e for e in snapshot['elements']) for key in ('propertyBatches', 'classifications', 'ifc', 'gdl')},
           'elementMetadataErrors': {key: sum('error' in e.get(key, {}) for e in snapshot['elements']) for key in ('classifications', 'ifc', 'gdl')},
           'metadataWarnings': snapshot['warnings'], 'exportComplete': report.get('complete'),
           'pythonRegressionTests': 11, 'revitPluginTarget': 'Revit 2026 / net8.0-windows',
           'revitRuntimeImportTest': 'not performed: Revit/WEAM endpoint was not connected',
           'fileBytes': {name: (folder/name).stat().st_size for name in ('manifest.json', 'snapshot.json')},
           'notes': ['Normalized ready is not successful Revit import.', 'Calculated built-in values were not fully exported.',
                     'Existing elements are not updated or deleted.', 'Mapped families require calibration.',
                     'Raw source metadata is preserved; unsupported elements need further adapters.']}
write_json(root/'artifacts/validation-summary.json', summary)
print(json.dumps(summary, ensure_ascii=True, indent=2))
