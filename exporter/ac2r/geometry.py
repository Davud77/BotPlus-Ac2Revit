import math


def point(p, z=0):
    result = {k: float(p[k]) for k in ('x', 'y')}
    result['z'] = float(p.get('z', z))
    if not all(math.isfinite(v) for v in result.values()):
        raise ValueError('Non-finite coordinate')
    return result


def arc_midpoint(a, b, angle):
    """Signed Archicad sweep in radians; sagitta preserves both minor and major arcs."""
    if not 1e-10 < abs(angle) < 2*math.pi-1e-10:
        raise ValueError('Arc sweep must be nonzero and smaller than 2 pi')
    dx, dy = b['x']-a['x'], b['y']-a['y']
    length = math.hypot(dx, dy)
    if length < 1e-9:
        raise ValueError('Zero arc chord')
    sagitta = length/2 * math.tan(angle/4)
    return {'x': (a['x']+b['x'])/2 + dy/length*sagitta,
            'y': (a['y']+b['y'])/2 - dx/length*sagitta, 'z': a['z']}


def curve(a, b, angle=0):
    if math.dist(tuple(a.values()), tuple(b.values())) < 1e-9:
        raise ValueError('Zero length edge')
    result = {'kind': 'line', 'start': a, 'end': b}
    if abs(angle) > 1e-10:
        result.update(kind='arc', mid=arc_midpoint(a, b, angle))
    return result


def ring(data, z=0):
    vertices = [point(p, z) for p in data['polygonOutline']]
    if len(vertices) > 1 and math.dist(tuple(vertices[0].values()), tuple(vertices[-1].values())) < 1e-8:
        vertices.pop()
    if len(vertices) < 3:
        raise ValueError('Ring needs at least 3 vertices')
    arcs = {}
    for a in data.get('polygonArcs', []):
        # Tapir CreatePolyArcObjectState subtracts one from API_PolyArc indexes.
        i, j = int(a['begIndex']), int(a['endIndex'])
        if not (0 <= i < len(vertices) and j in ((i+1) % len(vertices), i+1)):
            raise ValueError('Invalid polygon arc indexes')
        if i in arcs:
            raise ValueError('Duplicate arc edge')
        arcs[i] = float(a['arcAngle'])
    return [curve(a, vertices[(i+1) % len(vertices)], arcs.get(i, 0)) for i, a in enumerate(vertices)]


def loops(data, z=0):
    return [ring(data, z)] + [ring(hole, z) for hole in data.get('holes', [])]


def morph_mesh(data):
    body = data['body']
    if body.get('wireEdges'):
        raise ValueError('Wire geometry requires a separate curve adapter')
    origin = point(data['origin'])
    # Tapir exposes tranmat ROWS under xAxis/yAxis/zAxis, not basis columns.
    rows = [point(data[k]) for k in ('xAxis', 'yAxis', 'zAxis')]
    a, b, c = [[r[k] for k in ('x', 'y', 'z')] for r in rows]
    determinant = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if abs(determinant) < 1e-12:
        raise ValueError('Singular morph transform')
    vertices = []
    for raw in body['vertices']:
        v = point(raw)
        vertices.append({axis: origin[axis]+sum(row[k]*v[k] for k in ('x', 'y', 'z'))
                         for axis, row in zip(('x', 'y', 'z'), rows)})
    faces = []
    for poly in body['polygons']:
        rings = [poly['vertexIds']] + [h['vertexIds'] for h in poly.get('holes', [])]
        for indices in rings:
            if len(indices) < 3 or len(set(indices)) != len(indices) or any(not isinstance(i, int) or not 0 <= i < len(vertices) for i in indices):
                raise ValueError('Invalid morph face indexes')
        faces.append([list(reversed(r)) if determinant < 0 else r[:] for r in rings])
    if not faces:
        raise ValueError('Morph has no faces')
    return {'vertices': vertices, 'faces': faces, 'closed': bool(body.get('isClosed')),
            'representation': 'facetedBody', 'bodyType': body.get('bodyType', '')}


def arc_circle(edge):
    """Circumcircle in a local frame, and signed sweep through the exact midpoint."""
    a,b,m=edge['start'],edge['end'],edge['mid']
    dx,dy=b['x']-a['x'],b['y']-a['y'];mx,my=m['x']-a['x'],m['y']-a['y']
    determinant=2*(dx*my-dy*mx)
    if abs(determinant)<1e-18:raise ValueError('Collinear arc points')
    b2,m2=dx*dx+dy*dy,mx*mx+my*my
    ox,oy=(b2*my-m2*dy)/determinant,(dx*m2-mx*b2)/determinant
    radius=math.hypot(ox,oy);start=math.atan2(-oy,-ox)
    ccw=(math.atan2(dy-oy,dx-ox)-start)%(2*math.pi)
    mid=(math.atan2(my-oy,mx-ox)-start)%(2*math.pi)
    sweep=ccw if mid<ccw else ccw-2*math.pi
    if not all(math.isfinite(v) for v in (ox,oy,radius,sweep)) or radius<=0 or abs(sweep)<1e-12:
        raise ValueError('Invalid arc circle')
    return a['x']+ox,a['y']+oy,radius,start,sweep


def on_arc(angle,start,sweep):
    relative=(angle-start)%(2*math.pi) if sweep>0 else (start-angle)%(2*math.pi)
    return relative<=abs(sweep)+1e-11


def curved_interior_point(edges,holes=()):
    """Analytic horizontal intersections; no chord replacement of room contours."""
    rings=[edges,*holes];circles={};critical=set()
    for r in rings:
        for e in r:
            critical.update((e['start']['y'],e['end']['y']))
            if e['kind']=='arc':
                cx,cy,radius,start,sweep=arc_circle(e);circles[id(e)]=(cx,cy,radius,start,sweep)
                for angle in (math.pi/2,3*math.pi/2):
                    if on_arc(angle,start,sweep):critical.add(cy+radius*math.sin(angle))
            elif e['kind']!='line':raise ValueError('Unsupported zone edge: '+e['kind'])
    def crossings(r,y):
        xs=[]
        for e in r:
            if e['kind']=='line':
                a,b=e['start'],e['end']
                if (a['y']>y)!=(b['y']>y):xs.append(a['x']+(y-a['y'])*(b['x']-a['x'])/(b['y']-a['y']))
            else:
                cx,cy,radius,start,sweep=circles[id(e)];height=y-cy
                if abs(height)>=radius:continue
                half=math.sqrt(max(0,(radius-height)*(radius+height)))
                for x in (cx-half,cx+half):
                    if on_arc(math.atan2(height,x-cx),start,sweep):xs.append(x)
        xs.sort()
        if len(xs)%2:raise ValueError('Unpaired analytic zone boundary intersections')
        return xs
    candidates=[];ys=sorted(critical)
    for lo,hi in zip(ys,ys[1:]):
        if hi-lo<1e-8:continue
        y=(lo+hi)/2;per_ring=[crossings(r,y) for r in rings]
        xs=sorted(x for row in per_ring for x in row)
        for left,right in zip(xs,xs[1:]):
            if right-left<1e-8:continue
            x=(left+right)/2
            inside=lambda row:sum(v>x for v in row)%2==1
            if inside(per_ring[0]) and not any(inside(row) for row in per_ring[1:]):
                candidates.append((min(right-left,hi-lo),x,y))
    if not candidates:raise ValueError('No analytic interior point')
    _,x,y=max(candidates)
    return {'x':x,'y':y,'z':edges[0]['start']['z']}


def interior_point(edges, holes=()):
    """Deterministic scanline inside lines/arcs and holes. Never use stamp position."""
    if any(e['kind'] != 'line' for ring_ in [edges, *holes] for e in ring_):
        return curved_interior_point(edges,holes)
    rings = [edges, *holes]
    ys = sorted({e['start']['y'] for r in rings for e in r})
    candidates = []
    for lo, hi in zip(ys, ys[1:]):
        if hi-lo < 1e-8:
            continue
        y = (lo+hi)/2
        xs = []
        for r in rings:
            for e in r:
                a, b = e['start'], e['end']
                if (a['y'] > y) != (b['y'] > y):
                    xs.append(a['x'] + (y-a['y'])*(b['x']-a['x'])/(b['y']-a['y']))
        xs.sort()
        for left, right in zip(xs[::2], xs[1::2]):
            candidates.append((min(right-left, hi-lo), (left+right)/2, y))
    if not candidates:
        raise ValueError('No interior point')
    _, x, y = max(candidates)
    return {'x': x, 'y': y, 'z': edges[0]['start']['z']}
