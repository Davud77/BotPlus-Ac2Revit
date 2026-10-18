"""Register IFC world coordinates to Archicad using exact column identities.

Only insertion points from explicit IFC ObjectPlacement and API origin are
paired. No nearest-neighbour search or bounding-box matching is used.
"""
import math
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'exporter'))
from ac2r.jsonio import canonical_digest


def fit_frame(ifc_points, source_points, tolerance=1e-6):
    a, b = np.asarray(ifc_points, dtype=float), np.asarray(source_points, dtype=float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[1] != 3 or len(a) < 3 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError('At least three finite, paired IFC/source insertion points are required')
    x, y = a[:, :2]-a[:, :2].mean(axis=0), b[:, :2]-b[:, :2].mean(axis=0)
    if np.linalg.matrix_rank(x, tol=1e-6) < 2:
        raise ValueError('IFC/source anchors must not be collinear')
    angle = math.atan2(float((x[:, 0]*y[:, 1]-x[:, 1]*y[:, 0]).sum()), float((x*y).sum()))
    if abs(angle) < 1e-9: angle = 0.0
    c, s = math.cos(angle), math.sin(angle)
    r = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    translation = (b-a@r.T).mean(axis=0)
    translation[np.abs(translation) < 1e-9] = 0
    matrix = np.eye(4); matrix[:3, :3] = r; matrix[:3, 3] = translation
    errors = np.linalg.norm(a@r.T+translation-b, axis=1)
    if float(errors.max()) > tolerance:
        raise ValueError(f'IFC/source insertion points do not share one rigid frame: maximum residual {errors.max():.9g} m')
    return {'matrix': matrix.tolist(), 'rotationRadians': angle, 'translationMeters': translation.tolist(),
            'anchors': len(a), 'maxResidualMeters': float(errors.max()), 'toleranceMeters': tolerance}


def apply_frame(points, frame):
    p = np.asarray(points, dtype=float); matrix = np.asarray(frame['matrix'], dtype=float)
    return p@matrix[:3, :3].T+matrix[:3, 3]


def infer_frame(manifest, model, identities=None):
    import ifcopenshell.util.placement
    import ifcopenshell.util.unit
    from source_ifc import SourceIfcIndex
    identities = identities or SourceIfcIndex(manifest, model)
    scale = ifcopenshell.util.unit.calculate_unit_scale(model)
    a, b, evidence = [], [], []
    for element in manifest['elements']:
        if element['kind'] != 'Column': continue
        d = element['raw'].get('details', {})
        if d.get('coreAnchor') != 'Center' or d.get('isSlanted') or 'profileId' in d or d.get('wrapping'): continue
        matches = identities.matches(element)
        if len(matches) != 1 or not getattr(matches[0], 'ObjectPlacement', None): continue
        obj = matches[0]
        world = ifcopenshell.util.placement.get_local_placement(obj.ObjectPlacement)[:3, 3]*scale
        source = [d['origin']['x'], d['origin']['y'], d['zCoordinate']]
        a.append(world); b.append(source)
        evidence.append({'sourceGuid': element['sourceGuid'], 'ifcGlobalId': obj.GlobalId,
                         'ifcInsertionMeters': world.tolist(), 'sourceInsertionMeters': source})
    frame = fit_frame(a, b)
    frame.update(format='ac2r.ifc-frame/1', projectId=manifest['source']['projectId'],
                 method='Rigid Z rotation/translation from exact IFC ObjectPlacement/API insertion point pairs; all anchors validated',
                 evidence=evidence)
    frame['hash'] = canonical_digest(frame)
    print(f"IFC → Archicad: {frame['anchors']} exact anchors, residual {frame['maxResidualMeters']:.9g} m, translation {frame['translationMeters']}", flush=True)
    return frame
