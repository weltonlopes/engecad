"""Adaptador entre entidades DXF do ezdxf e a geometria que desenhamos/snapamos.

Toda entidade vira uma lista de polilinhas achatadas. Assim o renderizador e o
motor de snap nao precisam saber o que e um bulge, uma spline ou uma elipse --
o ezdxf.path faz o achatamento, e com tolerancia dependente do zoom para a
curva ficar lisa quando o usuario aproxima.
"""

from __future__ import annotations

import math

from ezdxf import bbox as ezbbox
from ezdxf.path import from_hatch_boundary_path, make_path

from .dimensions import DIMENSION_TYPES, dimension_primitives
from .geometry import BBox, Vec2

# Entidades que viram texto/marcador em vez de polilinha.
POINT_LIKE = {"POINT", "TEXT", "MTEXT", "INSERT", "ATTDEF"}

# Tipos cuja decomposicao em primitivas e cara: INSERT explode o bloco inteiro e
# DIMENSION reconstroi o bloco anonimo da cota. Medido: 79 us por INSERT e 314 us
# por DIMENSION -- inviavel de refazer a cada quadro ou a cada movimento do mouse.
_COMPOSITE = DIMENSION_TYPES | {"INSERT"}

# Cache das primitivas, por handle. E limpo pelo Document quando a entidade muda
# (ver Document._mark_dirty); o teto evita que um desenho gigante segure memoria
# indefinidamente.
_MAX_PRIMITIVE_CACHE = 30_000
_primitive_cache: dict[str, list] = {}


def entity_primitives(entity) -> list:
    """Primitivas graficas de INSERT/DIMENSION, com cache por handle."""
    handle = entity.dxf.get("handle")
    if handle is None:
        return _decompose(entity)
    hit = _primitive_cache.get(handle)
    if hit is not None:
        return hit
    out = _decompose(entity)
    if len(_primitive_cache) >= _MAX_PRIMITIVE_CACHE:
        _primitive_cache.clear()
    _primitive_cache[handle] = out
    return out


def _decompose(entity) -> list:
    t = entity.dxftype()
    if t in DIMENSION_TYPES:
        return list(dimension_primitives(entity))
    from ezdxf.disassemble import recursive_decompose

    try:
        out = list(recursive_decompose([entity]))
    except (TypeError, ValueError, AttributeError):
        return []
    if t == "INSERT":
        out.extend(entity.attribs)
    return out


def invalidate_primitives(handle: str | None = None) -> None:
    """Descarta o cache de primitivas de uma entidade, ou de todas."""
    if handle is None:
        _primitive_cache.clear()
    else:
        _primitive_cache.pop(handle, None)


def entity_polylines(
    entity, sagitta: float = 0.01, expand_blocks: bool = False
) -> list[list[Vec2]]:
    """Polilinhas achatadas da entidade, em Vec2 de mundo.

    sagitta = erro maximo tolerado no achatamento de curvas, em unidades do
    mundo. O canvas passa o equivalente a ~0.3 px, entao a curva e lisa em
    qualquer zoom sem gerar vertices demais quando esta longe.
    """
    return [
        [Vec2(x, y) for x, y in poly]
        for poly in entity_point_lists(entity, sagitta, expand_blocks)
    ]


def entity_point_lists(
    entity, sagitta: float = 0.01, expand_blocks: bool = False
) -> list[list[tuple[float, float]]]:
    """Como entity_polylines, mas em tuplas cruas.

    A display list consome milhoes de vertices por reconstrucao e joga cada um
    direto num QPainterPath: embrulhar tudo em Vec2 no caminho custava um terco
    do tempo de construcao.

    expand_blocks=True faz um INSERT devolver a geometria do bloco, em vez de
    nada. So a display list usa isso: para snap e picking um bloco continua
    sendo um ponto de insercao.
    """
    dxftype = entity.dxftype()

    fast = _fast_points(entity, dxftype, sagitta)
    if fast is not None:
        return fast

    if dxftype == "HATCH":
        out: list[list[tuple[float, float]]] = []
        for boundary in entity.paths:
            try:
                path = from_hatch_boundary_path(boundary)
                pts = [(v.x, v.y) for v in path.flattening(max(sagitta, 1e-9))]
            except (TypeError, ValueError):
                continue
            if len(pts) >= 2:
                if _apart(pts[0], pts[-1]):
                    pts.append(pts[0])
                out.append(pts)
        return out
    if dxftype in _COMPOSITE:
        if dxftype == "INSERT" and not expand_blocks:
            return []
        out = []
        for primitive in entity_primitives(entity):
            if primitive.dxftype() not in POINT_LIKE:
                out.extend(entity_point_lists(primitive, sagitta, expand_blocks))
        return out
    if dxftype in POINT_LIKE:
        return []
    try:
        path = make_path(entity)
    except (TypeError, ValueError):
        return []
    pts = [(v.x, v.y) for v in path.flattening(max(sagitta, 1e-9))]
    if len(pts) < 2:
        return []
    if path.is_closed and _apart(pts[0], pts[-1]):
        pts.append(pts[0])
    return [pts]


def _apart(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) > 1e-9 or abs(a[1] - b[1]) > 1e-9


def _is_plan(entity) -> bool:
    """A entidade esta no plano XY do mundo? Se nao, precisa da conversao OCS."""
    e = entity.dxf.get("extrusion", None)
    return e is None or (abs(e[0]) < 1e-12 and abs(e[1]) < 1e-12 and e[2] > 0)


def _fast_points(entity, dxftype: str, sagitta: float):
    """Achatamento direto dos tipos que dominam um desenho cadastral.

    O caminho generico (ezdxf.path.make_path + flattening) custa de 45 a 55 us
    por entidade -- so construir os tiles de uma vista cheia levava segundos.
    Lendo os atributos DXF direto o mesmo trabalho sai por ~4 us. Qualquer caso
    que fuja do trivial (extrusao fora do plano, bulge, elevacao) devolve None e
    cai no caminho generico, que continua sendo a referencia de correcao.
    """
    try:
        if dxftype == "LINE":
            if not _is_plan(entity):
                return None
            a, b = entity.dxf.start, entity.dxf.end
            return [[(a.x, a.y), (b.x, b.y)]]

        if dxftype == "LWPOLYLINE":
            if entity.has_arc or not _is_plan(entity):
                return None
            pts = [(p[0], p[1]) for p in entity.lwpoints]
            if len(pts) < 2:
                return []
            if entity.closed and _apart(pts[0], pts[-1]):
                pts.append(pts[0])
            return [pts]

        if dxftype == "CIRCLE":
            if not _is_plan(entity):
                return None
            c, r = entity.dxf.center, abs(float(entity.dxf.radius))
            if r <= 0:
                return []
            return [_arc_points(c.x, c.y, r, 0.0, math.tau, sagitta, closed=True)]

        if dxftype == "ARC":
            if not _is_plan(entity):
                return None
            c, r = entity.dxf.center, abs(float(entity.dxf.radius))
            if r <= 0:
                return []
            a0 = math.radians(float(entity.dxf.start_angle))
            a1 = math.radians(float(entity.dxf.end_angle))
            if a1 <= a0:
                a1 += math.tau
            return [_arc_points(c.x, c.y, r, a0, a1, sagitta)]
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    return None


def _arc_points(cx, cy, r, a0, a1, sagitta, closed: bool = False):
    """Poligonal do arco com erro de flecha <= sagitta."""
    span = a1 - a0
    s = min(max(sagitta, 1e-12), r)
    step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - s / r)))
    n = int(math.ceil(abs(span) / max(step, 1e-6)))
    # O teto so existe para um raio absurdo nao gerar milhoes de pontos; ate uma
    # razao raio/tolerancia de 1e6 ele nao chega a valer.
    n = max(4 if closed else 2, min(n, 4096))
    d = span / n
    pts = [(cx + r * math.cos(a0 + d * i), cy + r * math.sin(a0 + d * i)) for i in range(n + 1)]
    if closed:
        pts[-1] = pts[0]
    return pts


def entity_insert_point(entity) -> Vec2 | None:
    """Ponto de insercao de entidades pontuais (texto, bloco, ponto)."""
    dxf = entity.dxf
    for attr in ("insert", "location", "align_point"):
        if dxf.hasattr(attr):
            p = dxf.get(attr)
            return Vec2(p.x, p.y)
    return None


def entity_bbox(entity) -> BBox:
    """Bbox da entidade, em coordenadas do mundo.

    O extrator generico do ezdxf custa 63 us por entidade -- 11 s so para
    indexar um desenho de 200 mil. Os tipos que dominam um desenho cadastral
    tem o bbox calculavel direto dos atributos DXF, em 4.5 us; o ezdxf fica
    como reserva para o que sobra (texto, blocos, splines).
    """
    fast = _fast_bbox(entity)
    if fast is not None:
        return fast
    try:
        ext = ezbbox.extents([entity], fast=True)
        if ext.has_data:
            return BBox(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    except Exception:
        pass
    p = entity_insert_point(entity)
    return BBox(p.x, p.y, p.x, p.y) if p else BBox()


def _fast_bbox(entity) -> BBox | None:
    """Bbox direto dos atributos, ou None se o tipo exigir o extrator generico."""
    t = entity.dxftype()
    dxf = entity.dxf
    try:
        if t == "LINE":
            a, b = dxf.start, dxf.end
            return BBox(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
        if t == "POINT":
            p = dxf.location
            return BBox(p.x, p.y, p.x, p.y)
        if t == "CIRCLE":
            c, r = dxf.center, abs(float(dxf.radius))
            return BBox(c.x - r, c.y - r, c.x + r, c.y + r)
        if t == "ARC":
            return _arc_bbox(dxf.center, abs(float(dxf.radius)),
                             float(dxf.start_angle), float(dxf.end_angle))
        if t == "LWPOLYLINE":
            pts = entity.get_points("xy")
            if not pts:
                return BBox()
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            # O bulge pode estufar o arco para fora do poligono dos vertices; a
            # folga de meia corda cobre o caso sem achatar a polilinha inteira.
            slack = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.5 if entity.has_arc else 0.0
            return BBox(min(xs) - slack, min(ys) - slack, max(xs) + slack, max(ys) + slack)
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _arc_bbox(center, radius: float, start_deg: float, end_deg: float) -> BBox:
    """Bbox exato do arco: extremos mais os quadrantes que ele atravessa."""
    a0 = math.radians(start_deg) % math.tau
    a1 = math.radians(end_deg) % math.tau
    if a1 <= a0:
        a1 += math.tau
    xs = [center.x + radius * math.cos(a) for a in (a0, a1)]
    ys = [center.y + radius * math.sin(a) for a in (a0, a1)]
    q = a0 - a0 % (math.pi / 2)
    while q <= a1:
        if q >= a0:
            xs.append(center.x + radius * math.cos(q))
            ys.append(center.y + radius * math.sin(q))
        q += math.pi / 2
    return BBox(min(xs), min(ys), max(xs), max(ys))


def entity_snap_points(entity) -> list[tuple[str, Vec2]]:
    """Pontos notaveis para snap: (tipo, ponto)."""
    t = entity.dxftype()
    dxf = entity.dxf
    out: list[tuple[str, Vec2]] = []

    if t in DIMENSION_TYPES:
        for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5"):
            if dxf.hasattr(attr):
                p = dxf.get(attr)
                out.append(("end", Vec2(p.x, p.y)))
        if dxf.hasattr("text_midpoint"):
            p = dxf.text_midpoint
            out.append(("mid", Vec2(p.x, p.y)))
        return out

    if t == "LINE":
        a, b = Vec2(dxf.start.x, dxf.start.y), Vec2(dxf.end.x, dxf.end.y)
        out.append(("end", a))
        out.append(("end", b))
        out.append(("mid", (a + b) * 0.5))
        return out

    if t == "CIRCLE":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        out.append(("center", c))
        for ang in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            out.append(("quad", Vec2.polar(c, ang, r)))
        return out

    if t == "ARC":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        a0 = math.radians(dxf.start_angle)
        a1 = math.radians(dxf.end_angle)
        if a1 < a0:
            a1 += math.tau
        out.append(("center", c))
        out.append(("end", Vec2.polar(c, a0, r)))
        out.append(("end", Vec2.polar(c, a1, r)))
        out.append(("mid", Vec2.polar(c, (a0 + a1) / 2, r)))
        return out

    if t in POINT_LIKE:
        p = entity_insert_point(entity)
        if p:
            out.append(("node", p))
        return out

    # LWPOLYLINE, POLYLINE, SPLINE, ELLIPSE...: vertices + meios dos segmentos.
    for poly in entity_polylines(entity, sagitta=0.001):
        for i, p in enumerate(poly):
            out.append(("end", p))
            if i + 1 < len(poly):
                out.append(("mid", (p + poly[i + 1]) * 0.5))
    return out


def entity_summary(entity) -> str:
    """Descricao curta para barra de status e listagens."""
    t = entity.dxftype()
    layer = entity.dxf.get("layer", "0")
    return f"{t} na camada {layer}"
