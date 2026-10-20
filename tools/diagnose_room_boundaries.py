"""Find native room boundary segments inside the captured source zone.

This is diagnostic geometry, never a request to turn off room bounding. Source
display visibility and automatic story visibility do not establish zone rules.
"""
import argparse
import collections
import csv
import json
from pathlib import Path

from shapely.geometry import LineString, Point, Polygon
from shapely.validation import explain_validity


def diagnose(room, source_elements, clearance=.03, minimum_length=.1):
    polygon = Polygon([p[:2] for p in room['sourceLoops'][0]],
                      [[p[:2] for p in ring] for ring in room['sourceLoops'][1:]])
    if not polygon.is_valid:
        return dict(sourceGuid=room['sourceGuid'], status='invalidSourcePolygon',
                    error=explain_validity(polygon), candidates=[])
    original = source_elements.get(room['sourceGuid'], {})
    relation = original.get('raw', {}).get('relations', {}).get('zoneRelations')
    owners = {p.get('elementId', {}).get('guid', '').upper()
              for p in (relation or {}).get('wallParts', [])}
    interior = polygon.buffer(-clearance)
    candidates = []
    native_owners = set()
    for ring in room.get('boundaryModes', {}).get('Finish', []):
        for segment in ring:
            actual = segment.get('element', {})
            key = actual.get('sourceOwnerGuid', actual.get('sourceGuid', '')).upper()
            if key:native_owners.add(key)
            # Curved native tessellation has no recorded sagitta bound. Retain
            # it in the original audit, but do not classify it with a chord.
            if segment.get('curve') != 'Line' or len(segment.get('points', [])) < 2:
                continue
            line = LineString([p[:2] for p in segment['points']])
            inside_length = line.intersection(interior).length
            if inside_length < minimum_length:
                continue
            wall = source_elements.get(key, {})
            details = wall.get('raw', {}).get('details', {})
            candidates.append(dict(sourceGuid=key, revitElementId=actual.get('id'),
                elementClass=actual.get('elementClass', ''), lengthMeters=line.length,
                lengthInsideSourceMeters=inside_length, points=segment['points'],
                confirmedSourceBoundaryOwner=None if relation is None else key in owners,
                sourceFloorIndex=wall.get('raw', {}).get('floorIndex'),
                sourceZoneRelation=details.get('zoneRel'),
                sourceStoryVisibility=details.get('visibility'),
                sourceAutomaticStoryVisibility=details.get('isAutoOnStoryVisibility')))
    point = room.get('sourcePoint')
    return dict(sourceGuid=room['sourceGuid'], name=room.get('name', ''), levelKey=room.get('levelKey', ''),
        status='noNativeEnclosure' if room.get('probeArea') == 0 else 'interiorNativeBoundary' if candidates else 'noInteriorLineFound',
        sourceAreaSquareMeters=room.get('sourceArea'), nativeAreaSquareMeters=room.get('probeArea'),
        sourcePointInsidePolygon=None if point is None else polygon.contains(Point(point['x'], point['y'])),
        sourceBoundaryRelations='captured' if relation is not None else 'notCaptured',
        sourceBoundaryOwnerCount=None if relation is None else len(owners),
        nativeBoundaryOwnerCount=len(native_owners),
        sourceOwnersAbsentFromNativeBoundary=None if relation is None else sorted(owners-native_owners),
        nativeOwnersAbsentFromSourceRelations=None if relation is None else sorted(native_owners-owners),
        ownerComparisonNote='Diagnostic identity comparison; joined native faces can report a different owner. Absence alone does not prove an import error.',
        candidates=candidates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding='utf-8-sig'))
    manifest = json.loads(args.manifest.read_text(encoding='utf-8-sig'))
    if audit['sourceProject'] != manifest['source']['projectId']:
        raise ValueError('Audit and manifest belong to different source projects')
    elements = {e['sourceGuid'].upper(): e for e in manifest['elements']}
    rows = [diagnose(room, elements) for room in audit['rooms']]
    args.out.mkdir(parents=True, exist_ok=False)
    result = dict(format='ac2r.room-boundary-diagnosis/1', sourceProject=audit['sourceProject'],
        audit=str(args.audit.resolve()), manifest=str(args.manifest.resolve()),
        clearanceMeters=.03, minimumInteriorLengthMeters=.1,
        sourceDisplaySagittaMeters=.001,
        note='Diagnostic only. No source/model changed. Interior lines do not prove the cause. '
             'Display visibility does not establish zone enclosure, especially with automatic story visibility. '
             'Missing source relations are unknown, not evidence of absence.',
        outcomes=dict(collections.Counter(r['status'] for r in rows)), rooms=rows)
    (args.out/'diagnosis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    with (args.out/'interior-boundaries.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['roomGuid', 'roomName', 'levelKey', 'wallGuid',
            'revitElementId', 'insideLengthMeters', 'confirmedSourceBoundaryOwner', 'automaticStoryVisibility'])
        writer.writeheader()
        for room in rows:
            for candidate in room['candidates']:
                writer.writerow(dict(roomGuid=room['sourceGuid'], roomName=room['name'], levelKey=room['levelKey'],
                    wallGuid=candidate['sourceGuid'], revitElementId=candidate['revitElementId'],
                    insideLengthMeters=candidate['lengthInsideSourceMeters'],
                    confirmedSourceBoundaryOwner=candidate['confirmedSourceBoundaryOwner'],
                    automaticStoryVisibility=candidate['sourceAutomaticStoryVisibility']))
    print(json.dumps(dict(outcomes=result['outcomes'], candidates=sum(len(r['candidates']) for r in rows),
                          output=str(args.out.resolve())), ensure_ascii=True))


if __name__ == '__main__':
    main()
