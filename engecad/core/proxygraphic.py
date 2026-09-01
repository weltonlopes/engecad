"""Leitura rapida do grafico embutido de uma entidade proxy.

Uma ACAD_PROXY_ENTITY e um objeto de aplicacao que este CAD nao conhece -- um
ponto do Civil 3D, um poste de um aplicativo de topografia. O DXF nao traz a
geometria dela em tags: traz um blob binario com os comandos de desenho que o
AutoCAD usa quando o object enabler nao esta instalado. Fora desse blob nao ha
nem coordenada, entao ate a bbox da entidade depende de decodifica-lo.

O ezdxf decodifica esse blob, e corretamente, mas construindo entidades DXF de
verdade: um levantamento com 126 mil pontos custava 686 us por ponto -- 87
segundos so para saber onde eles estao, e outros tantos no extrator de bbox.
Metade disso e o construtor do ProxyGraphic, que relista as tabelas de camada,
tipo de linha e estilo do documento a cada chamada; a outra metade e criar um
POLYLINE do ezdxf com um Vertex por ponto.

Aqui so lemos as coordenadas. Os comandos que nao sabemos ler devolvem None, e
quem chamou volta para o caminho do ezdxf -- que continua sendo a referencia de
correcao, agora paga so onde e preciso.
"""

from __future__ import annotations

import struct

# Codigos dos comandos, do ezdxf.proxygraphic.ProxyGraphicTypes.
_CIRCLE = 2
_POLYLINE = 6
_POLYGON = 7
_PUSH_MATRIX = 29
_PUSH_MATRIX2 = 30
_POP_MATRIX = 31
_POLYLINE_WITH_NORMALS = 32

#: Comandos sem geometria: atributos de estilo, recorte, texto. Texto entra aqui
#: porque o passe de geometria nao desenha texto -- quem desenha e o passe de
#: rotulo, pelo caminho do ezdxf.
_IGNORED = frozenset(
    {
        1,  # EXTENTS
        10, 11, 36, 38,  # TEXT, TEXT2, UNICODE_TEXT, UNICODE_TEXT2
        14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,  # atributos
        27, 28,  # PUSH_CLIP, POP_CLIP
        34, 35,  # ATTRIBUTE_MATERIAL, ATTRIBUTE_MAPPER
        37,  # UNKNOWN_37
    }
)

_HEADER = struct.Struct("<2L")
_COUNT = struct.Struct("<L")
_VERTEX = struct.Struct("<3d")
_MATRIX = struct.Struct("<16d")
_CIRCLE_DATA = struct.Struct("<3dd3d")

CIRCLE_SEGMENTS = 16  # um marcador de proxy nunca e grande na tela


def proxy_point_lists(data: bytes) -> list[list[tuple[float, float]]] | None:
    """Polilinhas do blob, em coordenadas do mundo.

    None quer dizer "tem desenho aqui que eu nao sei ler" -- nunca "esta vazio".
    """
    if not data or len(data) < 8:
        return []
    out: list[list[tuple[float, float]]] = []
    stack: list[tuple] = []
    index = 8
    size = len(data)
    while index < size:
        try:
            chunk, kind = _HEADER.unpack_from(data, index)
        except struct.error:
            return None
        if chunk < 8 or index + chunk > size:
            return None
        payload = data[index + 8 : index + chunk]
        index += chunk

        if kind in _IGNORED:
            continue
        if kind == _POP_MATRIX:
            if stack:
                stack.pop()
            continue
        if kind in (_PUSH_MATRIX, _PUSH_MATRIX2):
            m = _read_matrix(payload)
            if m is None:
                return None
            stack.append(m)
            continue
        if kind in (_POLYLINE, _POLYGON, _POLYLINE_WITH_NORMALS):
            pts = _read_vertices(payload, drop_last=kind == _POLYLINE_WITH_NORMALS)
            if pts is None:
                return None
            if len(pts) < 2:
                continue
            if kind == _POLYGON and pts[0] != pts[-1]:
                pts.append(pts[0])
            out.append(_apply(stack, pts))
            continue
        if kind == _CIRCLE:
            pts = _read_circle(payload)
            if pts is None:
                return None
            out.append(_apply(stack, pts))
            continue
        return None  # malha, spline, arco: o ezdxf que resolva
    return out


def _read_vertices(payload: bytes, drop_last: bool):
    try:
        count = _COUNT.unpack_from(payload, 0)[0]
    except struct.error:
        return None
    if drop_last:
        count += 1  # o ultimo "vertice" e a normal
    need = 4 + count * 24
    if count < 0 or need > len(payload):
        return None
    pts = []
    at = 4
    for _ in range(count):
        x, y, _z = _VERTEX.unpack_from(payload, at)
        pts.append((x, y))
        at += 24
    if drop_last and pts:
        pts.pop()
    return pts


def _read_circle(payload: bytes):
    try:
        cx, cy, _cz, radius, nx, ny, nz = _CIRCLE_DATA.unpack_from(payload, 0)
    except struct.error:
        return None
    if abs(nx) > 1e-12 or abs(ny) > 1e-12 or nz <= 0:
        return None  # circulo fora do plano: caminho generico
    import math

    step = math.tau / CIRCLE_SEGMENTS
    pts = [
        (cx + radius * math.cos(step * i), cy + radius * math.sin(step * i))
        for i in range(CIRCLE_SEGMENTS + 1)
    ]
    pts[-1] = pts[0]
    return pts


def _read_matrix(payload: bytes):
    try:
        v = _MATRIX.unpack_from(payload, 0)
    except struct.error:
        return None
    # O ezdxf transpoe a matriz lida; com vetor-linha, x' = x*m0 + y*m1 + z*m2 + m3.
    return (v[0], v[1], v[3], v[4], v[5], v[7])


def _apply(stack, pts):
    """Aplica a matriz do topo da pilha, se houver."""
    if not stack:
        return pts
    a, b, tx, c, d, ty = stack[-1]
    return [(x * a + y * b + tx, x * c + y * d + ty) for x, y in pts]
