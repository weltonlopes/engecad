"""Adaptador entre entidades DXF do ezdxf e a geometria que desenhamos/snapamos.

Toda entidade vira uma lista de polilinhas achatadas. Assim o renderizador e o
motor de snap nao precisam saber o que e um bulge, uma spline ou uma elipse --
o ezdxf.path faz o achatamento, e com tolerancia dependente do zoom para a
curva ficar lisa quando o usuario aproxima.
"""

from __future__ import annotations

import math
from collections import OrderedDict

import numpy as np
from ezdxf import bbox as ezbbox
from ezdxf.path import from_hatch_boundary_path, make_path

from .dimensions import DIMENSION_TYPES, dimension_primitives
from .geometry import BBox, Vec2
from .proxygraphic import proxy_point_lists

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
_MAX_PROXY_CACHE = 8_192
_proxy_cache: OrderedDict[str, list[list[tuple[float, float]]] | None] = OrderedDict()
_MAX_POLYLINE_POINTS = 1_000_000
_polyline_cache: OrderedDict[str, list[tuple[float, float, float]]] = OrderedDict()
_polyline_points = 0


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


_TEXTUAL = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
_block_text: dict[str, bool] = {}


def insert_has_text(insert) -> bool:
    """A referencia de bloco contribui algum texto para a tela?

    Um cadastro tem milhares de blocos de simbolo -- poste, arvore, bueiro --
    que sao pura geometria. Descobrir isso decompondo cada um custa 79 us por
    referencia; a resposta depende so da DEFINICAO do bloco, entao vale por nome.
    """
    if insert.attribs:
        return True
    name = insert.dxf.get("name", "")
    hit = _block_text.get(name)
    if hit is None:
        hit = _block_definition_has_text(insert.doc, name, set())
        _block_text[name] = hit
    return hit


def _block_definition_has_text(doc, name: str, seen: set[str]) -> bool:
    if doc is None or name in seen:
        return False
    seen.add(name)
    try:
        block = doc.blocks.get(name)
    except Exception:
        return False
    if block is None:
        return False
    for e in block:
        t = e.dxftype()
        if t in _TEXTUAL:
            return True
        if t == "INSERT" and _block_definition_has_text(doc, e.dxf.get("name", ""), seen):
            return True
    return False


_MAX_SEGMENT_CACHE = 512
_segment_cache: dict[tuple, tuple] = {}


def entity_segments(entity, sagitta: float):
    """Segmentos achatados da entidade, em arrays numpy. None se nao houver.

    Snap e picking medem a distancia do cursor ate a geometria a cada movimento
    do mouse. Um eixo de rodovia com 7951 vertices e bulge vira 177 mil pontos
    quando achatado: refazer isso por movimento custava 19 ms so nessa entidade,
    e percorrer os segmentos em Python custava outro tanto.

    O achatamento fica guardado por OITAVA de tolerancia -- mexer o mouse nao
    troca de oitava, entao o cache serve --, e a forma de array deixa a conta de
    distancia sair vetorizada.
    """
    handle = entity.dxf.get("handle")
    if handle is None:
        return _segments_of(entity_point_lists(entity, sagitta))
    octave = int(math.floor(math.log2(max(sagitta, 1e-9))))
    key = (handle, octave)
    hit = _segment_cache.get(key)
    if hit is None:
        hit = _segments_of(entity_point_lists(entity, 2.0**octave)) or ()
        if len(_segment_cache) >= _MAX_SEGMENT_CACHE:
            _segment_cache.clear()
        _segment_cache[key] = hit
    return hit or None


def _segments_of(polylines):
    ax, ay, bx, by = [], [], [], []
    for poly in polylines:
        for (x0, y0), (x1, y1) in zip(poly, poly[1:], strict=False):
            ax.append(x0)
            ay.append(y0)
            bx.append(x1)
            by.append(y1)
    if not ax:
        return None
    ax = np.array(ax)
    ay = np.array(ay)
    return (ax, ay, np.array(bx) - ax, np.array(by) - ay)


def closest_on_segments(segs, x: float, y: float):
    """(distancias, xs, ys) do ponto ate cada segmento, de uma vez."""
    ax, ay, dx, dy = segs
    ex, ey = x - ax, y - ay
    length = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.where(length > 0.0, (ex * dx + ey * dy) / length, 0.0)
    np.clip(u, 0.0, 1.0, out=u)
    px, py = ax + u * dx, ay + u * dy
    return np.hypot(x - px, y - py), px, py


def invalidate_primitives(handle: str | None = None) -> None:
    """Descarta o cache de primitivas de uma entidade, ou de todas."""
    global _polyline_points
    if handle is None:
        _primitive_cache.clear()
        _block_text.clear()
        _segment_cache.clear()
        _proxy_cache.clear()
        _polyline_cache.clear()
        _polyline_points = 0
    else:
        _primitive_cache.pop(handle, None)
        _proxy_cache.pop(handle, None)
        old = _polyline_cache.pop(handle, None)
        if old is not None:
            _polyline_points -= len(old)
        for key in [k for k in _segment_cache if k[0] == handle]:
            del _segment_cache[key]


def _proxy_points(entity):
    """Geometria proxy quente compartilhada por bbox, render, snap e hover."""
    handle = entity.dxf.get("handle")
    if handle is None:
        return proxy_point_lists(entity.proxy_graphic)
    if handle in _proxy_cache:
        hit = _proxy_cache[handle]
        _proxy_cache.move_to_end(handle)
        return hit
    hit = proxy_point_lists(entity.proxy_graphic)
    _proxy_cache[handle] = hit
    if len(_proxy_cache) > _MAX_PROXY_CACHE:
        _proxy_cache.popitem(last=False)
    return hit


def _cache_polyline(handle: str | None, points: list[tuple[float, float, float]]) -> None:
    """Guarda polilinhas grandes ja lidas pelo calculo de bbox."""
    global _polyline_points
    if handle is None or len(points) < 1024:
        return
    old = _polyline_cache.pop(handle, None)
    if old is not None:
        _polyline_points -= len(old)
    _polyline_cache[handle] = points
    _polyline_points += len(points)
    while _polyline_points > _MAX_POLYLINE_POINTS and len(_polyline_cache) > 1:
        _, old = _polyline_cache.popitem(last=False)
        _polyline_points -= len(old)


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
    if dxftype == "ACAD_PROXY_ENTITY":
        # O caminho rapido desistiu: o ezdxf decodifica o blob por inteiro, e o
        # resultado fica no cache de primitivas para nao se pagar de novo.
        out = []
        for primitive in entity_primitives(entity):
            if primitive.dxftype() not in POINT_LIKE:
                out.extend(entity_point_lists(primitive, sagitta, expand_blocks))
        return out
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
    dxf = entity.dxf
    # hasattr custa um decimo do get, e a extrusao quase nunca esta escrita.
    if not dxf.hasattr("extrusion"):
        return True
    e = dxf.extrusion
    return abs(e[0]) < 1e-12 and abs(e[1]) < 1e-12 and e[2] > 0


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
            if not _is_plan(entity):
                return None
            handle = entity.dxf.get("handle")
            raw = _polyline_cache.get(handle) if handle is not None else None
            if raw is not None:
                _polyline_cache.move_to_end(handle)
            else:
                raw = [(p[0], p[1], p[4]) for p in entity.lwpoints]
            # Ler os vertices e detectar bulge na MESMA passada. `has_arc`
            # percorre a polilinha inteira por conta propria, e uma curva de
            # nivel tem centenas de vertices: a checagem separada custava tanto
            # quanto o resto do achatamento.
            return _flatten_lwpolyline(raw, bool(entity.closed), sagitta)

        if dxftype == "HATCH":
            return _hatch_boundary_points(entity)

        if dxftype == "ACAD_PROXY_ENTITY":
            return _proxy_points(entity)

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


def _hatch_boundary_points(hatch):
    """Contornos de uma hachura cujas bordas sao poligonais retas.

    E o caso comum -- um lote, uma area de uso do solo. Sem isto, cada contorno
    passa pelo conversor generico do ezdxf e vira um Path com Beziers, so para
    ser achatado de volta em segmentos.
    """
    out: list[list[tuple[float, float]]] = []
    for boundary in hatch.paths:
        vertices = getattr(boundary, "vertices", None)
        if vertices is None:  # borda com arcos, elipses ou splines
            return None
        pts = []
        for v in vertices:
            if len(v) > 2 and v[2]:  # bulge
                return None
            pts.append((v[0], v[1]))
        if len(pts) < 2:
            continue
        if _apart(pts[0], pts[-1]):
            pts.append(pts[0])
        out.append(pts)
    return out


def _flatten_lwpolyline(raw, closed: bool, sagitta: float):
    """Achatamento direto de LWPOLYLINE, inclusive segmentos com bulge."""
    if len(raw) < 2:
        return []
    pairs = list(zip(raw, raw[1:], strict=False))
    if closed:
        pairs.append((raw[-1], raw[0]))
    out = [(raw[0][0], raw[0][1])]
    for start, end in pairs:
        x0, y0, bulge = start
        x1, y1 = end[0], end[1]
        if abs(bulge) <= 1e-15:
            out.append((x1, y1))
            continue
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        if chord <= 1e-15:
            out.append((x1, y1))
            continue
        theta = 4.0 * math.atan(bulge)
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        cx, cy = mx - dy / chord * offset, my + dx / chord * offset
        s = min(max(sagitta, 1e-12), radius)
        step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - s / radius)))
        count = max(1, min(4096, int(math.ceil(abs(theta) / max(step, 1e-6)))))
        a0 = math.atan2(y0 - cy, x0 - cx)
        delta = theta / count
        for i in range(1, count):
            angle = a0 + delta * i
            out.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        out.append((x1, y1))
    return [out]


def _lwpolyline_bbox(raw, closed: bool) -> BBox:
    """BBox exato dos vertices e quadrantes dos arcos definidos por bulge."""
    xs = [p[0] for p in raw]
    ys = [p[1] for p in raw]
    pairs = list(zip(raw, raw[1:], strict=False))
    if closed and len(raw) > 1:
        pairs.append((raw[-1], raw[0]))
    for start, end in pairs:
        x0, y0, bulge = start
        if abs(bulge) <= 1e-15:
            continue
        x1, y1 = end[0], end[1]
        dx, dy = x1 - x0, y1 - y0
        chord = math.hypot(dx, dy)
        if chord <= 1e-15:
            continue
        theta = 4.0 * math.atan(bulge)
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        offset = chord * (1.0 - bulge * bulge) / (4.0 * bulge)
        mx, my = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        cx, cy = mx - dy / chord * offset, my + dx / chord * offset
        a0 = math.atan2(y0 - cy, x0 - cx)
        for quadrant in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            span = (quadrant - a0) % math.tau if theta > 0 else (a0 - quadrant) % math.tau
            if span <= abs(theta) + 1e-12:
                xs.append(cx + radius * math.cos(quadrant))
                ys.append(cy + radius * math.sin(quadrant))
    return BBox(min(xs), min(ys), max(xs), max(ys))


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
        if t == "ACAD_PROXY_ENTITY":
            # O extrator generico do ezdxf custa 1.8 ms por proxy; num
            # levantamento com 126 mil pontos sao quatro minutos so de indice.
            polys = _proxy_points(entity)
            if polys is None:
                return None
            return _bbox_of(polys)

        if t == "LWPOLYLINE":
            raw = entity.get_points("xyb")
            if not raw:
                return BBox()
            _cache_polyline(dxf.get("handle"), [(p[0], p[1], p[2]) for p in raw])
            return _lwpolyline_bbox(raw, bool(entity.closed))
    except (AttributeError, TypeError, ValueError):
        return None
    return None


def _bbox_of(polylines) -> BBox:
    xs = [p[0] for poly in polylines for p in poly]
    if not xs:
        return BBox()
    ys = [p[1] for poly in polylines for p in poly]
    return BBox(min(xs), min(ys), max(xs), max(ys))


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
    # A tolerancia acompanha o porte da entidade: 1 mm num eixo de rodovia de
    # 5 km gera centenas de milhares de pontos de arco, e nenhum deles e vertice
    # de verdade -- os vertices reais entram no achatamento em qualquer tolerancia.
    box = entity_bbox(entity)
    sagitta = max(0.001, max(box.width, box.height) * 1e-4)
    for poly in entity_polylines(entity, sagitta=sagitta):
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
