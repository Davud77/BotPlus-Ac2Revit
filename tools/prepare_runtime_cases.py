"""Prepare lighter geometry diagnostics; original export and metadata remain untouched."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.export import write_json
from ac2r.normalize import digest

root = Path(__file__).resolve().parents[1]
full_path = root/'artifacts/abakan-v1/manifest.json'
full = json.loads(full_path.read_text(encoding='utf-8'))
for label, manifest in [('sample', json.loads((root/'artifacts/sample/manifest.json').read_text(encoding='utf-8'))), ('geometry', full)]:
    manifest.pop('propertyDefinitions', None)
    manifest.pop('classificationSystems', None)
    for element in manifest['elements']:
        raw = element['raw']
        element['raw'] = {k: raw[k] for k in ('sourceGuid', 'type', 'id', 'floorIndex', 'layerIndex', 'drawIndex', 'hotlinkId', 'details') if k in raw}
        element['raw']['metadataManifestPath'] = str(full_path)
    manifest['source']['scope'] += '; geometry diagnostic; metadata linked to original export'
    manifest.pop('manifestHash', None)
    manifest['manifestHash'] = digest(manifest)
    out = root/'artifacts/runtime'/label
    write_json(out/'manifest.json', manifest)
    write_json(out/'mapping.json', {'allowPartial': True, 'zoneMode': 'room', 'families': {}})
    print(label, len(manifest['elements']), (out/'manifest.json').stat().st_size, flush=True)
