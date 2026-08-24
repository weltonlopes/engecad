"""APARAR e ESTENDER.

As arestas de corte sao reduzidas a duas formas primitivas -- segmento e
circulo/arco -- e as intersecoes com circulo sao calculadas de forma EXATA,
nao achatando o circulo em segmentos. Achatar deixaria a linha aparada alguns
milimetros fora da aresta, o que numa planta cadastral aparece na conferencia.
"""

from __future__ import annotations

import math

from .entities import entity_polylines
from .geometry import (
    EPS,
    Vec2,
    angle_in_range,
    circle_circle_intersection,
    closest_point_on_segment,
    line_circle_intersection,
    line_intersection,
)

TRIMMABLE = {"LINE", "LWPOLYLINE", "CIRCLE", "ARC"}
EXTENDABLE = {"LINE", "ARC"}


# ---------------- arestas de corte ----------------


def collect_shapes(entities, sagitta: float = 0.01, exclude=()) -> list[tuple]:
    """Reduz as entidades a ('seg', a, b) e ('circle', centro, raio, ini, fim)."""
    shapes: list[tuple] = []
    for e in entities:
        if e in exclude or not e.is_alive:
            continue
        t = e.dxftype()
        dxf = e.dxf
        if t == "CIRCLE":
            shapes.append(
                ("circle", Vec2(dxf.center.x, dxf.center.y), float(dxf.radius), None, None)
            )
        elif t == "ARC":
            shapes.append(
                (
                    "circle",
                    Vec2(dxf.center.x, dxf.center.y),
                    float(dxf.radius),
                    float(dxf.start_angle),
                    float(dxf.end_angle),
                )
            )
        else:
            for poly in entity_polylines(e, sagitta):
                for i in range(len(poly) - 1):
                    shapes.append(("seg", poly[i], poly[i + 1]))
    return shapes


def _on_arc(p: Vec2, c: Vec2, sa, ea) -> bool:
    if sa is None:
        return True
    return angle_in_range(math.degrees((p - c).angle) % 360.0, sa, ea)


def intersect_segment(a: Vec2, b: Vec2, shapes) -> list[Vec2]:
    out: list[Vec2] = []
    for s in shapes:
        if s[0] == "seg":
            ip = line_intersection(a, b, s[1], s[2], as_segments=True)
            if ip is not None:
                out.append(ip)
        else:
            _, c, r, sa, ea = s
            for ip in line_circle_intersection(a, b, c, r, as_segment=True):
                if _on_arc(ip, c, sa, ea):
                    out.append(ip)
    return out


def intersect_circle(c: Vec2, r: float, shapes) -> list[Vec2]:
    out: list[Vec2] = []
    for s in shapes:
        if s[0] == "seg":
            out.extend(line_circle_intersection(s[1], s[2], c, r, as_segment=True))
        else:
            _, c2, r2, sa, ea = s
            for ip in circle_circle_intersection(c, r, c2, r2):
                if _on_arc(ip, c2, sa, ea):
                    out.append(ip)
    return out


# ---------------- utilidades de parametro ----------------


def _dedupe_sorted(values: list[float], tol: float = 1e-9) -> list[float]:
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _interval_containing(bounds: list[float], value: float):
    for i in range(len(bounds) - 1):
        if bounds[i] - 1e-9 <= value <= bounds[i + 1] + 1e-9:
            return bounds[i], bounds[i + 1]
    return None


def _cumulative(pts: list[Vec2]) -> list[float]:
    cum = [0.0]
    for i in range(len(pts) - 1):
        cum.append(cum[-1] + pts[i].distance_to(pts[i + 1]))
    return cum


def _param_on_path(pts: list[Vec2], cum: list[float], p: Vec2) -> float:
    best_d, best_s = float("inf"), 0.0
    for i in range(len(pts) - 1):
        q = closest_point_on_segment(p, pts[i], pts[i + 1])
        d = p.distance_to(q)
        if d < best_d:
            best_d = d
            best_s = cum[i] + pts[i].distance_to(q)
    return best_s


def _point_at(pts: list[Vec2], cum: list[float], s: float) -> Vec2:
    if s <= 0:
        return pts[0]
    if s >= cum[-1]:
        return pts[-1]
    for i in range(len(pts) - 1):
        if cum[i] <= s <= cum[i + 1]:
            seg = cum[i + 1] - cum[i]
            if seg < EPS:
                return pts[i]
            t = (s - cum[i]) / seg
            return pts[i] + (pts[i + 1] - pts[i]) * t
    return pts[-1]


def _subpath(pts: list[Vec2], cum: list[float], s0: float, s1: float) -> list[Vec2]:
    if s1 - s0 < EPS:
        return []
    out = [_point_at(pts, cum, s0)]
    for i, s in enumerate(cum):
        if s0 + EPS < s < s1 - EPS:
            out.append(pts[i])
    out.append(_point_at(pts, cum, s1))
    dedup = [out[0]]
    for p in out[1:]:
        if dedup[-1].distance_to(p) > EPS:
            dedup.append(p)
    return dedup if len(dedup) >= 2 else []


# ---------------- aparar ----------------


def trim_entity(doc, target, shapes, click: Vec2) -> list | None:
    """Remove do alvo o trecho onde o usuario clicou.

    Devolve as entidades resultantes (pode ser lista vazia se o alvo sumir
    inteiro), ou None se nao houver aresta cortando o alvo.
    """
    if not target.is_alive or target.dxftype() not in TRIMMABLE:
        return None
    t = target.dxftype()
    layer = target.dxf.get("layer", None)

    if t == "LINE":
        return _trim_line(doc, target, shapes, click, layer)
    if t == "ARC":
        return _trim_arc(doc, target, shapes, click, layer)
    if t == "CIRCLE":
        return _trim_circle(doc, target, shapes, click, layer)
    if t == "LWPOLYLINE":
        return _trim_polyline(doc, target, shapes, click, layer)
    return None


def _trim_line(doc, target, shapes, click, layer):
    dxf = target.dxf
    a = Vec2(dxf.start.x, dxf.start.y)
    b = Vec2(dxf.end.x, dxf.end.y)
    ab = b - a
    ll = ab.length_sq
    if ll < EPS:
        return None

    def param(p: Vec2) -> float:
        return max(0.0, min(1.0, (p - a).dot(ab) / ll))

    cuts = [param(ip) for ip in intersect_segment(a, b, shapes)]
    interior = [t for t in _dedupe_sorted(cuts) if 1e-9 < t < 1 - 1e-9]
    if not interior:
        return None

    bounds = [0.0, *interior, 1.0]
    rng = _interval_containing(bounds, param(click))
    if rng is None:
        return None
    lo, hi = rng
    made = []
    length = math.sqrt(ll)
    if (lo - bounds[0]) * length > EPS:
        made.append(doc.add_line(a + ab * bounds[0], a + ab * lo, layer=layer))
    if (bounds[-1] - hi) * length > EPS:
        made.append(doc.add_line(a + ab * hi, a + ab * bounds[-1], layer=layer))
    return made


def _trim_arc(doc, target, shapes, click, layer):
    dxf = target.dxf
    c = Vec2(dxf.center.x, dxf.center.y)
    r = float(dxf.radius)
    sa = float(dxf.start_angle)
    ea = float(dxf.end_angle)
    span = (ea - sa) % 360.0 or 360.0

    def rel(p: Vec2) -> float:
        return (math.degrees((p - c).angle) - sa) % 360.0

    cuts = [rel(ip) for ip in intersect_circle(c, r, shapes)]
    interior = [x for x in _dedupe_sorted(cuts) if 1e-9 < x < span - 1e-9]
    if not interior:
        return None

    bounds = [0.0, *interior, span]
    rng = _interval_containing(bounds, rel(click))
    if rng is None:
        return None
    lo, hi = rng
    made = []
    if lo - bounds[0] > 1e-9:
        made.append(doc.add_arc(c, r, sa + bounds[0], sa + lo, layer=layer))
    if bounds[-1] - hi > 1e-9:
        made.append(doc.add_arc(c, r, sa + hi, sa + bounds[-1], layer=layer))
    return made


def _trim_circle(doc, target, shapes, click, layer):
    dxf = target.dxf
    c = Vec2(dxf.center.x, dxf.center.y)
    r = float(dxf.radius)
    hits = intersect_circle(c, r, shapes)
    angs = _dedupe_sorted([math.degrees((ip - c).angle) % 360.0 for ip in hits])
    if len(angs) < 2:
        return None
    click_a = math.degrees((click - c).angle) % 360.0
    for i, lo in enumerate(angs):
        hi = angs[(i + 1) % len(angs)]
        if angle_in_range(click_a, lo, hi):
            # o circulo vira um arco cobrindo todo o resto
            return [doc.add_arc(c, r, hi, lo, layer=layer)]
    return None


def _trim_polyline(doc, target, shapes, click, layer):
    raw = list(target.get_points("xyseb"))
    if any(abs(p[4]) > 1e-12 for p in raw):
        return None  # polilinha com bulge: nao aparamos ainda
    pts = [Vec2(p[0], p[1]) for p in raw]
    if bool(target.closed) and pts[0].distance_to(pts[-1]) > EPS:
        pts.append(pts[0])
    if len(pts) < 2:
        return None
    cum = _cumulative(pts)
    total = cum[-1]
    if total < EPS:
        return None

    cuts = []
    for i in range(len(pts) - 1):
        for ip in intersect_segment(pts[i], pts[i + 1], shapes):
            cuts.append(cum[i] + pts[i].distance_to(ip))
    interior = [s for s in _dedupe_sorted(cuts) if 1e-9 < s < total - 1e-9]
    if not interior:
        return None

    bounds = [0.0, *interior, total]
    rng = _interval_containing(bounds, _param_on_path(pts, cum, click))
    if rng is None:
        return None
    lo, hi = rng
    made = []
    for s0, s1 in ((bounds[0], lo), (hi, bounds[-1])):
        piece = _subpath(pts, cum, s0, s1)
        if len(piece) >= 2:
            made.append(doc.add_lwpolyline(piece, closed=False, layer=layer))
    return made


# ---------------- estender ----------------


def extend_entity(doc, target, shapes, click: Vec2) -> bool:
    """Estica a extremidade mais proxima do clique ate a primeira aresta."""
    if not target.is_alive or target.dxftype() not in EXTENDABLE:
        return False
    if target.dxftype() == "LINE":
        return _extend_line(doc, target, shapes, click)
    return _extend_arc(doc, target, shapes, click)


def _extend_line(doc, target, shapes, click) -> bool:
    dxf = target.dxf
    a = Vec2(dxf.start.x, dxf.start.y)
    b = Vec2(dxf.end.x, dxf.end.y)
    move_end = click.distance_to(b) <= click.distance_to(a)
    moving, fixed = (b, a) if move_end else (a, b)
    d = (moving - fixed)
    if d.length < EPS:
        return False
    d = d.normalized()

    reach = max(_reach(shapes, moving), 1.0)
    far = moving + d * reach
    best, best_t = None, float("inf")
    for ip in intersect_segment(fixed, far, shapes):
        t = (ip - fixed).dot(d)
        if t > (moving - fixed).dot(d) + 1e-9 and t < best_t:
            best, best_t = ip, t
    if best is None:
        return False
    with doc.editing([target], "estender"):
        if move_end:
            dxf.end = (best.x, best.y, 0.0)
        else:
            dxf.start = (best.x, best.y, 0.0)
    return True


def _extend_arc(doc, target, shapes, click) -> bool:
    dxf = target.dxf
    c = Vec2(dxf.center.x, dxf.center.y)
    r = float(dxf.radius)
    sa, ea = float(dxf.start_angle), float(dxf.end_angle)
    p_start = Vec2.polar(c, math.radians(sa), r)
    p_end = Vec2.polar(c, math.radians(ea), r)
    move_end = click.distance_to(p_end) <= click.distance_to(p_start)

    span = (ea - sa) % 360.0 or 360.0
    best = None
    for ip in intersect_circle(c, r, shapes):
        ang = math.degrees((ip - c).angle) % 360.0
        if move_end:
            delta = (ang - ea) % 360.0
        else:
            delta = (sa - ang) % 360.0
        if delta < 1e-9 or delta > 360.0 - span - 1e-9:
            continue
        if best is None or delta < best[0]:
            best = (delta, ang)
    if best is None:
        return False
    with doc.editing([target], "estender"):
        if move_end:
            dxf.end_angle = best[1]
        else:
            dxf.start_angle = best[1]
    return True


def _reach(shapes, origin: Vec2) -> float:
    """Ate onde faz sentido projetar o raio de extensao."""
    far = 0.0
    for s in shapes:
        if s[0] == "seg":
            far = max(far, origin.distance_to(s[1]), origin.distance_to(s[2]))
        else:
            far = max(far, origin.distance_to(s[1]) + s[2])
    return far * 1.5
