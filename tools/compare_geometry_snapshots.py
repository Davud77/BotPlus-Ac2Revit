"""Compare two complete Revit GeometryAudit snapshots by source identity."""
import argparse
import collections
import json
from pathlib import Path
import numpy as np
from compare_column_geometry import probes, surface_distance


def triangles(row):
    vertices = np.asarray(row['vertices'], dtype=float)
    # Canonical triangles retain multiplicity, independent of enumeration order.
    keys = [tuple(np.rint(p / 1e-7).astype(np.int64)) for p in vertices]
    return collections.Counter(tuple(sorted(keys[i] for i in f)) for f in row['faces'])


def compare_rows(before, after):
    result = {'sourceGuid': before['sourceGuid'], 'elementId': after['elementId']}
    if before['geometryStatus'] != 'extracted' or after['geometryStatus'] != 'extracted':
        return dict(result, status='geometryError')
    if not before.get('meshSaved') or not after.get('meshSaved'):
        raise ValueError('Complete meshes are required for both snapshots')
    a, b = np.asarray(before['vertices']), np.asarray(after['vertices'])
    if not len(a) or not len(b):
        return dict(result, status='geometryError')
    bounds = float(max(abs(a.min(axis=0)-b.min(axis=0)).max(), abs(a.max(axis=0)-b.max(axis=0)).max()))
    volume = abs(before['nativeSolidVolumeM3']-after['nativeSolidVolumeM3'])
    same = triangles(before) == triangles(after)
    surface = 0.0 if same else max(
        surface_distance(probes(a, np.asarray(before['faces'])), b, np.asarray(after['faces'])),
        surface_distance(probes(b, np.asarray(after['faces'])), a, np.asarray(before['faces'])))
    accepted = bounds <= 1e-6 and surface <= 1e-6 and volume <= 1e-6
    return dict(result, status=('unchangedTessellation' if same else 'equivalentWithinTolerance') if accepted else 'changed',
                boundsDifferenceMeters=bounds, sampledSurfaceDifferenceMeters=surface,
                volumeDifferenceM3=volume, signedVolumeDifferenceM3=after['nativeSolidVolumeM3']-before['nativeSolidVolumeM3'])


def compare(before_path, after_path, output):
    def load(path):
        rows = [json.loads(s) for s in path.read_text(encoding='utf-8-sig').splitlines()]
        index = {r['sourceGuid']: r for r in rows}
        if '' in index or len(index) != len(rows):
            raise ValueError('Missing or duplicate source identity')
        return index
    before, after = load(before_path), load(after_path)
    rows = [compare_rows(before[k], after[k]) for k in sorted(before.keys() & after.keys())]
    rows.extend({'sourceGuid': k, 'status': 'missing'} for k in sorted(before.keys()-after.keys()))
    rows.extend({'sourceGuid': k, 'status': 'added'} for k in sorted(after.keys()-before.keys()))
    summary = {'before': len(before), 'after': len(after),
               'statuses': dict(collections.Counter(r['status'] for r in rows)),
               'maxBoundsDifferenceMeters': max((r.get('boundsDifferenceMeters', 0) for r in rows), default=0),
               'maxSampledSurfaceDifferenceMeters': max((r.get('sampledSurfaceDifferenceMeters', 0) for r in rows), default=0),
               'maxVolumeDifferenceM3': max((r.get('volumeDifferenceM3', 0) for r in rows), default=0),
               'note': 'Regression against saved baseline; this does not establish agreement with source IFC. Surface comparison is sampled.'}
    with output.open('x', encoding='utf-8') as stream:
        json.dump({'summary': summary, 'elements': rows}, stream, indent=2)
    return summary


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('before', type=Path)
    p.add_argument('after', type=Path)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    print(json.dumps(compare(a.before, a.after, a.out), indent=2))
