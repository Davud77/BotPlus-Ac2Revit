"""Resolve behavior by exact source core-material GUID, never by family name."""
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


def material_guid(value):
    return value.get('attributeId', value).get('guid', '').upper()


def build(extrusions, profiles, concrete_materials):
    concrete = {g.upper() for g in concrete_materials}
    profile_index = {p['attributeId']['guid'].upper(): p for p in profiles}
    groups = defaultdict(list)
    seen = set()
    for column in extrusions['columns']:
        guid = column['sourceGuid'].upper()
        if guid in seen:
            raise ValueError('Duplicate source column GUID: ' + guid)
        seen.add(guid)
        groups[column['familyKey']].append(column)
    decisions, skipped = [], []
    for family_key, columns in sorted(groups.items()):
        member_materials = []
        for column in columns:
            profile_guid = column.get('profileGuid', '').upper()
            if profile_guid:
                profile = profile_index.get(profile_guid, {})
                materials = {material_guid(s.get('buildingMaterialId', {}))
                             for s in profile.get('skins', []) if s.get('isCore') is True}
            else:
                details = column.get('rawSource', {}).get('details', {})
                materials = {material_guid(details.get('buildingMaterialId', {}))}
            member_materials.append(materials)
        union = set().union(*member_materials)
        if len(union) != 1 or '' in union or any(m != union for m in member_materials) or not union <= concrete:
            skipped.append({'familyKey': family_key, 'coreMaterialGuids': sorted(union),
                            'sourceColumnGuids': [c['sourceGuid'] for c in columns],
                            'reason': 'Missing, mixed or not explicitly approved concrete core material'})
            continue
        decisions.append({'familyKey': family_key, 'behavior': 'Concrete',
                          'coreMaterialGuids': sorted(union),
                          'sourceColumnGuids': [c['sourceGuid'] for c in columns],
                          'evidence': 'Every source member has the same exact source core material GUID; explicit concrete material mapping'})
    return {'format': 'ac2r.column-behavior/1', 'projectId': extrusions['projectId'],
            'families': decisions, 'skippedFamilies': skipped}


def prepare(extrusions_path, profiles_path, concrete_materials, output):
    extrusions = json.loads(extrusions_path.read_text(encoding='utf-8-sig'))
    raw = json.loads(profiles_path.read_text(encoding='utf-8-sig'))
    profiles = raw['response']['result']['addOnCommandResponse']['profiles']
    plan = build(extrusions, profiles, concrete_materials)
    plan['provenance'] = [{'path': str(p.resolve()), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                          for p in (extrusions_path, profiles_path)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--extrusions', type=Path, required=True)
    parser.add_argument('--profiles', type=Path, required=True)
    parser.add_argument('--concrete-material', action='append', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.extrusions, args.profiles, args.concrete_material, args.out)
    print(json.dumps({'mappedFamilies': len(result['families']), 'skippedFamilies': len(result['skippedFamilies'])}))
