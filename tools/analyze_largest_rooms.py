"""Compare actual Revit boundary readback with source zone polygons."""
import argparse
import csv
import html
import json
from pathlib import Path

from shapely import set_precision
from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union, polygonize
from shapely.validation import explain_validity


def boundary_polygon(rings):
    if not isinstance(rings, list) or not rings:
        return Polygon()
    polygons = []
    for ring in rings:
        points = []
        for segment in ring:
            points.extend(p[:2] for p in segment['points'][:-1])
        if len(points) >= 3:
            poly = Polygon(points)
            if not poly.is_valid:
                raise ValueError('Invalid returned boundary ring; no automatic repair: '+explain_validity(poly))
            polygons.append(poly)
    # Nested rings alternate solid/hole/island. Symmetric difference retains
    # those semantics without relying on Revit's ring orientation.
    result = Polygon()
    for polygon in sorted(polygons, key=lambda p: -p.area):
        result = result.symmetric_difference(polygon)
    return result


def svg_geometry(geometry):
    if geometry.is_empty:
        return ''
    if geometry.geom_type == 'Polygon':
        loops = [geometry.exterior, *geometry.interiors]
        return ''.join('M'+' L'.join(f'{x:.5f},{-y:.5f}' for x,y in loop.coords)+' Z' for loop in loops)
    return ' '.join(svg_geometry(g) for g in geometry.geoms if g.geom_type in ('Polygon','MultiPolygon'))


def raw_boundary_card(room, reason):
    """Show failed rings as source edges without silently repairing their topology."""
    source = room['sourceLoops']
    actual = [[p for segment in ring for p in segment['points'][:-1]]
              for ring in room['boundaryModes'].get('Finish', [])]
    points = [p for ring in source+actual for p in ring]
    def trace(rings, color):
        paths = ''.join('M'+' L'.join(f'{p[0]:.6f},{-p[1]:.6f}' for p in ring)+' Z' for ring in rings if ring)
        return f'<path d="{paths}" fill="none" stroke="{color}"/>'
    drawing = ''
    if points:
        x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
        y0, y1 = min(p[1] for p in points), max(p[1] for p in points)
        pad = max(x1-x0, y1-y0)*0.035 or 1
        drawing = f'<svg viewBox="{x0-pad} {-y1-pad} {x1-x0+2*pad} {y1-y0+2*pad}" style="width:100%;height:480px">'+trace(source,'#2563eb')+trace(actual,'#dc2626')+'</svg>'
    probe = f"{room['probeArea']:.6f}" if room['probeArea'] is not None else 'ошибка'
    return (f'<article><h2>{room["rank"]}. {html.escape(room["name"])} — {html.escape(room["levelName"])}</h2>'
            f'<p>{room["sourceGuid"]}</p><p>Исходная площадь {room["sourceArea"]:.6f} м²; Revit {probe} м².</p>'
            f'<p><strong>Контур требует отдельного разбора:</strong> {html.escape(reason)}. Показаны исходные рёбра без исправления; булевы площади не вычислялись.</p>{drawing}</article>')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audit', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    data = json.loads(args.audit.read_text(encoding='utf-8-sig'))
    args.out.mkdir(parents=True, exist_ok=True)
    rows, cards, detailed = [], [], []
    for room in data['rooms']:
        source = Polygon([p[:2] for p in room['sourceLoops'][0]], [[p[:2] for p in l] for l in room['sourceLoops'][1:]])
        if not source.is_valid:
            reason='Invalid source polygon; no automatic repair: '+explain_validity(source)
            detailed.append({'guid':room['sourceGuid'],'error':reason})
            cards.append(raw_boundary_card(room, reason))
            continue
        try:
            actual = boundary_polygon(room['boundaryModes'].get('Finish'))
        except ValueError as exc:
            detailed.append({'guid':room['sourceGuid'],'error':str(exc),'mode':'Finish'})
            cards.append(raw_boundary_card(room, str(exc)))
            continue
        holes = unary_union([Polygon([p[:2] for p in l]) for l in room['sourceLoops'][1:]])
        column_polygons = []
        for item in room['nearbyPhysicalElements']:
            segments = item.get('sectionSegments') or []
            if segments:
                # Only diagnostic sections are snapped (0.01 mm) to close
                # floating-point tessellation seams; source/model stay intact.
                lines = set_precision(unary_union([LineString([p[:2] for p in s]) for s in segments]), 0.00001)
                column_polygons.extend(polygonize(lines))
        columns = unary_union(column_polygons)
        extra, missing = actual.difference(source), source.difference(actual)
        covered_holes = holes.intersection(actual)
        row = {'rank':room['rank'],'guid':room['sourceGuid'],'name':room['name'],'level':room['levelKey'],
               'source_m2':source.area,'revit_m2':room['probeArea'],'finish_boundary_m2':actual.area if not actual.is_empty else None,
               'signed_delta_m2':room['probeArea']-source.area if room['probeArea'] is not None else None,
               'source_holes':len(room['sourceLoops'])-1,'source_holes_m2':holes.area,
               'extra_native_m2':extra.area,'missing_native_m2':missing.area,
               'source_holes_filled_in_revit_m2':covered_holes.area,
               'filled_holes_over_columns_m2':covered_holes.intersection(columns).area,
               'all_extra_area_over_columns_m2':extra.intersection(columns).area,
               'error':room['error']}
        modes={}
        for mode,rings in room['boundaryModes'].items():
            if not isinstance(rings,list):
                modes[mode]=rings
                continue
            try:modes[mode]=boundary_polygon(rings).area
            except ValueError as exc:modes[mode]={'error':str(exc)}
        detailed.append({'summary':row,'modes':modes,
                         'boundary_source_guids':sorted({seg['element'].get('sourceGuid','') for ring in room['boundaryModes'].get('Finish',[]) for seg in ring})})
        rows.append(row)
        bounds = unary_union([source,actual]).bounds
        x0,y0,x1,y1 = bounds;pad=max(x1-x0,y1-y0)*0.035 or 1
        paths = [f'<path d="{svg_geometry(source)}" fill="#dbeafe" stroke="#2563eb"/>',
                 f'<path d="{svg_geometry(columns)}" fill="#9ca3af" stroke="#6b7280"/>',
                 f'<path d="{svg_geometry(extra)}" fill="#fca5a5" stroke="none"/>',
                 f'<path d="{svg_geometry(missing)}" fill="#fcd34d" stroke="none"/>',
                 f'<path d="{svg_geometry(actual)}" fill="none" stroke="#dc2626"/>']
        svg = f'<svg viewBox="{x0-pad} {-y1-pad} {x1-x0+2*pad} {y1-y0+2*pad}" style="width:100%;height:480px" fill-rule="evenodd">'+''.join(paths)+'</svg>'
        actual_label = f"{room['probeArea']:.3f}" if room['probeArea'] is not None else 'ошибка'
        cards.append(f'<article><h2>{room["rank"]}. {html.escape(room["name"])} — {html.escape(room["levelName"])}</h2><p>{room["sourceGuid"]}</p><p>Исходная площадь {source.area:.3f} м²; Revit {actual_label} м². Исходных отверстий: {len(room["sourceLoops"])-1}. Площадь исходных отверстий, включённая в Revit: {covered_holes.area:.3f} м².</p>{svg}</article>')
    (args.out/'analysis.json').write_text(json.dumps({'source':str(args.audit.resolve()),'boundaryModeForAreas':data['areaBoundaryMode'],
        'note':'Geometric evidence only; column section precision 0.01 mm. No model or source polygon changed.','rooms':detailed},ensure_ascii=False,indent=2),encoding='utf-8')
    if rows:
        with (args.out/'largest-rooms.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    (args.out/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Разбор крупных помещений</title><style>body{font:16px system-ui;background:#eef2f6;color:#172033;max-width:1200px;margin:32px auto;padding:0 20px}article{background:white;padding:24px;margin:24px 0;border-radius:12px}svg path{stroke-width:.025}p{line-height:1.5}</style><h1>Крупнейшие непринятые помещения</h1><p>Синий контур — исходная зона. Красный — фактическая граница Finish Revit. Красная заливка — лишняя площадь Revit, жёлтая — недостающая. Серый — диагностические сечения колонн. Все пробные помещения созданы с откатом.</p>'+''.join(cards)+'</html>',encoding='utf-8')
    print(json.dumps({'rooms':len(data['rooms']),'validBooleanAnalyses':len(rows),'boundaryFailures':len(detailed)-len(rows),'output':str(args.out.resolve()),'largest':rows[:5]},ensure_ascii=True,indent=2))


if __name__=='__main__':
    main()
