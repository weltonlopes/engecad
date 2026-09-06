"""Renderizador vetorial OpenGL.

A geometria e achatada uma unica vez, dividida em lotes espaciais e enviada
para VBOs persistentes. Pan e zoom passam a alterar somente uniforms do shader:
nenhuma coordenada e recalculada na CPU durante a navegacao.

Coordenadas cadastrais (UTM) nao cabem com precisao suficiente em um ``float``
de GPU. Cada componente e portanto separado em duas parcelas float32. O shader
subtrai primeiro a parcela alta do centro da vista e so depois soma a baixa;
assim preservamos milimetros perto do cursor sem exigir suporte a double no GL.
"""

from __future__ import annotations

import math
import time
from array import array
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QTransform, QVector2D, QVector4D
from PySide6.QtOpenGL import (
    QOpenGLBuffer,
    QOpenGLShader,
    QOpenGLShaderProgram,
    QOpenGLVersionFunctionsFactory,
    QOpenGLVersionProfile,
)

from ..core.dimensions import DIMENSION_TYPES
from ..core.entities import (
    MAX_INTERACTIVE_PROXY_BYTES,
    POINT_LIKE,
    _proxy_points,
    entity_point_lists,
    insert_has_text,
)
from ..core.geometry import BBox, decimate
from .styles import aci_to_qcolor

GL_FLOAT = 0x1406
GL_LINES = 0x0001
GL_BLEND = 0x0BE2
GL_DEPTH_TEST = 0x0B71
GL_CULL_FACE = 0x0B44
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_COLOR_BUFFER_BIT = 0x00004000

# 16 e potencia de dois: a parcela alta e representavel exatamente em float32
# para toda coordenada terrestre usual, enquanto a baixa ganha resolucao
# submicrometrica. No zoom geral, a diferenca entre parcelas altas pode perder
# centimetros, mas isso permanece muito abaixo de um pixel nessa escala.
COORD_SPLIT = 16.0
BUILD_SLICE_MS = 9.0
UPLOAD_SLICE_MS = 7.0
MAX_MARKERS = 3_000
MAX_PLACEHOLDERS = 2_000
MAX_HATCH_LINES = 5_000
DECIMATE_MIN_VERTS = 8

_MARKER_TYPES = POINT_LIKE | DIMENSION_TYPES | {"ATTRIB"}


@dataclass(frozen=True)
class GpuBatch:
    """Um VBO logico: uma regiao espacial, camada e estilo."""

    vertices: np.ndarray
    coarse_vertices: np.ndarray
    bbox: BBox
    layer: str
    aci: int
    alpha: int
    max_size: float

    @property
    def vertex_count(self) -> int:
        return int(self.vertices.shape[0])


@dataclass(frozen=True)
class GpuMarker:
    entity: object
    bbox: BBox
    layer: str


@dataclass(frozen=True)
class GpuFill:
    path: QPainterPath
    origin: tuple[float, float]
    bbox: BBox
    layer: str
    aci: int
    alpha: int


@dataclass(frozen=True)
class GpuPlaceholder:
    """Marcador para uma proxy que nao possui geometria decodificavel."""

    bbox: BBox
    layer: str
    aci: int
    alpha: int


@dataclass(frozen=True)
class GpuSnapshot:
    revision: int
    batches: tuple[GpuBatch, ...]
    markers: tuple[GpuMarker, ...]
    fills: tuple[GpuFill, ...]
    placeholders: tuple[GpuPlaceholder, ...]
    entities: int
    vertices: int
    build_ms: float


@dataclass
class _Group:
    layer: str
    aci: int
    alpha: int
    coords: array = field(default_factory=lambda: array("d"))
    coarse: array = field(default_factory=lambda: array("d"))
    minx: float = math.inf
    miny: float = math.inf
    maxx: float = -math.inf
    maxy: float = -math.inf
    max_size: float = 0.0

    def include_bbox(self, box: BBox) -> None:
        if box.is_empty:
            return
        self.minx = min(self.minx, box.minx)
        self.miny = min(self.miny, box.miny)
        self.maxx = max(self.maxx, box.maxx)
        self.maxy = max(self.maxy, box.maxy)
        self.max_size = max(self.max_size, box.width, box.height)

    def add_coarse(self, box: BBox) -> None:
        """Representacao de dois vertices para entidades sub-pixel."""
        if box.is_empty:
            return
        if box.width >= box.height:
            cy = (box.miny + box.maxy) * 0.5
            self.coarse.extend((box.minx, cy, box.maxx, cy))
        else:
            cx = (box.minx + box.maxx) * 0.5
            self.coarse.extend((cx, box.miny, cx, box.maxy))

    def add_polyline(self, poly) -> None:
        if len(poly) < 2:
            return
        last = poly[0]
        for point in poly[1:]:
            self.coords.extend((last[0], last[1], point[0], point[1]))
            last = point


def split_coordinates(points: np.ndarray) -> np.ndarray:
    """Codifica Nx2 float64 como ``high.xy, low.xy`` float32."""
    xy = np.asarray(points, dtype=np.float64)
    high = np.floor(xy / COORD_SPLIT) * COORD_SPLIT
    out = np.empty((len(xy), 4), dtype=np.float32)
    out[:, :2] = high
    out[:, 2:] = xy - high
    return out


def split_scalar(value: float) -> tuple[float, float]:
    high = math.floor(value / COORD_SPLIT) * COORD_SPLIT
    return float(high), float(value - high)


class GpuGeometry:
    """Constroi snapshots de geometria em fatias curtas do event loop."""

    def __init__(self, doc):
        self.doc = doc
        self.snapshot: GpuSnapshot | None = None
        self._revision = -1
        self._source = None
        self._groups: dict[tuple, _Group] = {}
        self._group_source = None
        self._packed: list[GpuBatch] = []
        self._markers: list[GpuMarker] = []
        self._fills: list[GpuFill] = []
        self._placeholders: list[GpuPlaceholder] = []
        self._entities = 0
        self._vertices = 0
        self._started = 0.0
        self._tile = 1.0
        self._sagitta = 0.01
        self._layer_props: dict[str, tuple[int, int]] = {}
        self.start(doc)

    @property
    def building(self) -> bool:
        return self._source is not None or self._group_source is not None

    @property
    def target_revision(self) -> int:
        return self._revision

    def start(self, doc=None) -> None:
        replaced = doc is not None and doc is not self.doc
        if doc is not None:
            self.doc = doc
        if replaced:
            self.snapshot = None
        self._revision = self.doc.geometry_revision
        self._source = iter(self.doc.entities())
        self._groups = {}
        self._group_source = None
        self._packed = []
        self._markers = []
        self._fills = []
        self._placeholders = []
        self._entities = 0
        self._vertices = 0
        self._started = time.perf_counter()
        self._layer_props = {}

        ext = self.doc.extents()
        span = max(ext.width, ext.height, 1.0) if not ext.is_empty else 1.0
        # Aproximadamente 512 entidades por tile num desenho bidimensional. Em
        # corredores lineares o teto de 128 ainda impede VBOs gigantes.
        axis = min(128.0, max(8.0, math.sqrt(max(len(self.doc), 1) / 512.0)))
        self._tile = max(span / axis, 1e-6)
        # Erro de tessellacao abaixo de ~0,2 px na vista geral, limitado para
        # preservar detalhe em desenhos pequenos e conter curvas astronomicas.
        self._sagitta = min(0.10, max(0.001, span / 5_000_000.0))

    def ensure_current(self) -> bool:
        if self.doc.geometry_revision == self._revision:
            return False
        self.start(self.doc)
        return True

    def advance(self, budget_ms: float = BUILD_SLICE_MS) -> bool:
        """Avanca a varredura ou empacotamento. Retorna True quando pronto."""
        self.ensure_current()
        if not self.building:
            return True
        deadline = time.perf_counter() + max(0.0, budget_ms) / 1000.0

        if self._source is not None:
            while time.perf_counter() < deadline:
                try:
                    entity = next(self._source)
                except StopIteration:
                    self._source = None
                    self._group_source = iter(self._groups.values())
                    break
                self._add_entity(entity)
            if self._source is not None:
                return False

        while self._group_source is not None and time.perf_counter() < deadline:
            try:
                group = next(self._group_source)
            except StopIteration:
                self._finish()
                return True
            batch = self._pack_group(group)
            if batch is not None:
                self._vertices += batch.vertex_count
                self._packed.append(batch)
        return self._group_source is None

    def sync(self) -> GpuSnapshot:
        while not self.advance(1_000_000.0):
            pass
        return self.snapshot

    def _layer_style(self, layer: str) -> tuple[int, int]:
        hit = self._layer_props.get(layer)
        if hit is not None:
            return hit
        props = self.doc.layer_manager.properties(layer)
        alpha = int(round(255 * (1.0 - props.transparency / 100.0)))
        if props.locked:
            alpha = min(alpha, 105)
        hit = (int(props.color), max(0, min(255, alpha)))
        self._layer_props[layer] = hit
        return hit

    def _style(self, entity) -> tuple[str, int, int]:
        layer = str(entity.dxf.get("layer", "0"))
        layer_aci, alpha = self._layer_style(layer)
        color = int(entity.dxf.get("color", 256) or 256)
        aci = layer_aci if color in (0, 256) else color
        return layer, aci, alpha

    def _group(self, box: BBox, layer: str, aci: int, alpha: int) -> _Group:
        cx = (box.minx + box.maxx) * 0.5 if not box.is_empty else 0.0
        cy = (box.miny + box.maxy) * 0.5 if not box.is_empty else 0.0
        key = (
            math.floor(cx / self._tile),
            math.floor(cy / self._tile),
            layer,
            aci,
            alpha,
        )
        group = self._groups.get(key)
        if group is None:
            group = self._groups[key] = _Group(layer, aci, alpha)
        group.include_bbox(box)
        return group

    def _add_entity(self, entity) -> None:
        if not entity.is_alive:
            return
        handle = entity.dxf.get("handle")
        box = self.doc.index._boxes.get(handle)
        if box is None or box.is_empty:
            return
        self._entities += 1
        layer, aci, alpha = self._style(entity)
        kind = entity.dxftype()

        if kind in _MARKER_TYPES:
            if kind != "INSERT" or insert_has_text(entity):
                self._markers.append(GpuMarker(entity, box, layer))

        if kind == "HATCH":
            self._add_hatch(entity, box, layer, aci, alpha)
            return
        if kind in POINT_LIKE and kind != "INSERT":
            return

        if (
            kind == "ACAD_PROXY_ENTITY"
            and len(entity.proxy_graphic or b"") > MAX_INTERACTIVE_PROXY_BYTES
            and _proxy_points(entity) is None
        ):
            # A antiga marca tinha o tamanho da bbox. Em proxies geograficas
            # com extents imprecisos isso produzia cruzes de centenas de px.
            self._placeholders.append(GpuPlaceholder(box, layer, aci, alpha))
            return

        group = self._group(box, layer, aci, alpha)
        group.add_coarse(box)
        for poly in entity_point_lists(entity, self._sagitta, expand_blocks=True):
            if len(poly) > DECIMATE_MIN_VERTS:
                poly = decimate(poly, self._sagitta)
            group.add_polyline(poly)

    def _add_hatch(self, hatch, box: BBox, layer: str, aci: int, opacity: int) -> None:
        try:
            alpha = int(round(255 * (1.0 - float(hatch.transparency))))
        except (TypeError, ValueError):
            alpha = 255
        alpha = max(25, min(alpha, opacity))
        if bool(hatch.dxf.get("solid_fill", 0)):
            origin = (
                math.floor(box.minx / 1000.0) * 1000.0,
                math.floor(box.miny / 1000.0) * 1000.0,
            )
            path = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
            for poly in entity_point_lists(hatch, self._sagitta):
                if len(poly) < 3:
                    continue
                if len(poly) > DECIMATE_MIN_VERTS:
                    poly = decimate(poly, self._sagitta)
                path.moveTo(poly[0][0] - origin[0], poly[0][1] - origin[1])
                for x, y in poly[1:]:
                    path.lineTo(x - origin[0], y - origin[1])
                path.closeSubpath()
            if not path.isEmpty():
                self._fills.append(GpuFill(path, origin, box, layer, aci, alpha))
            return

        group = self._group(box, layer, aci, alpha)
        group.add_coarse(box)
        try:
            for index, line in enumerate(hatch.render_pattern_lines()):
                if index >= MAX_HATCH_LINES:
                    break
                start, end = line
                group.add_polyline(((start.x, start.y), (end.x, end.y)))
        except (ValueError, ZeroDivisionError, AttributeError):
            return

    @staticmethod
    def _pack_group(group: _Group) -> GpuBatch | None:
        if not group.coords:
            return None
        points = np.frombuffer(group.coords, dtype=np.float64).reshape((-1, 2))
        vertices = split_coordinates(points)
        coarse_points = np.frombuffer(group.coarse, dtype=np.float64).reshape((-1, 2))
        coarse_vertices = split_coordinates(coarse_points)
        box = BBox(group.minx, group.miny, group.maxx, group.maxy)
        group.coords = array("d")
        group.coarse = array("d")
        return GpuBatch(
            vertices,
            coarse_vertices,
            box,
            group.layer,
            group.aci,
            group.alpha,
            group.max_size,
        )

    def _finish(self) -> None:
        elapsed = (time.perf_counter() - self._started) * 1000.0
        self.snapshot = GpuSnapshot(
            self._revision,
            tuple(self._packed),
            tuple(self._markers),
            tuple(self._fills),
            tuple(self._placeholders),
            self._entities,
            self._vertices,
            elapsed,
        )
        self._groups = {}
        self._group_source = None

    def visible_markers(self, snapshot: GpuSnapshot, viewport) -> list:
        vis = viewport.visible_bbox()
        result = []
        for marker in snapshot.markers:
            if self.doc.layer_is_visible(marker.layer) and marker.bbox.intersects(vis):
                result.append(marker.entity)
                if len(result) > MAX_MARKERS:
                    return []
        return result

    def paint_fills(self, painter, snapshot: GpuSnapshot, viewport, dark: bool) -> None:
        vis = viewport.visible_bbox()
        painter.save()
        painter.setPen(Qt.NoPen)
        for fill in snapshot.fills:
            if not self.doc.layer_is_visible(fill.layer) or not fill.bbox.intersects(vis):
                continue
            color = QColor(aci_to_qcolor(fill.aci, dark))
            color.setAlpha(fill.alpha)
            painter.setBrush(QBrush(color))
            ox, oy = fill.origin
            scale = viewport.scale
            painter.setWorldTransform(
                QTransform(
                    scale,
                    0.0,
                    0.0,
                    -scale,
                    (ox - viewport.center.x) * scale + viewport.width * 0.5,
                    viewport.height * 0.5 + (viewport.center.y - oy) * scale,
                )
            )
            painter.drawPath(fill.path)
        painter.restore()

    def paint_placeholders(
        self, painter, snapshot: GpuSnapshot, viewport, dark: bool
    ) -> None:
        """Desenha proxies desconhecidas como quadrados discretos de 5 px."""
        vis = viewport.visible_bbox()
        visible: dict[str, bool] = {}
        painter.save()
        painter.setBrush(Qt.NoBrush)
        drawn = 0
        style = None
        for marker in snapshot.placeholders:
            layer_on = visible.get(marker.layer)
            if layer_on is None:
                layer_on = visible[marker.layer] = self.doc.layer_is_visible(marker.layer)
            if not layer_on or not marker.bbox.intersects(vis):
                continue
            marker_style = (marker.aci, marker.alpha)
            if marker_style != style:
                color = QColor(aci_to_qcolor(marker.aci, dark))
                color.setAlpha(marker.alpha)
                pen = QPen(color, 1.0)
                pen.setCosmetic(True)
                painter.setPen(pen)
                style = marker_style
            x, y = viewport.world_to_screen(marker.bbox.center)
            painter.drawRect(QRectF(x - 2.5, y - 2.5, 5.0, 5.0))
            drawn += 1
            if drawn >= MAX_PLACEHOLDERS:
                break
        painter.restore()


@dataclass
class _GpuBuffer:
    bbox: BBox
    layer: str
    aci: int
    alpha: int
    first: int
    count: int
    coarse_first: int
    coarse_count: int
    max_size: float


@dataclass
class _BufferSet:
    """Dois VBOs consolidados e os intervalos logicos dentro deles."""

    buffer: QOpenGLBuffer
    coarse_buffer: QOpenGLBuffer
    items: list[_GpuBuffer]


class OpenGLRenderer:
    """Recursos OpenGL que so podem existir com o contexto corrente."""

    VERTEX_SHADER = """
        attribute vec4 a_coord;
        uniform vec2 u_center_high;
        uniform vec2 u_center_low;
        uniform vec2 u_ndc_scale;
        void main() {
            vec2 relative = (a_coord.xy - u_center_high)
                          + (a_coord.zw - u_center_low);
            gl_Position = vec4(relative * u_ndc_scale, 0.0, 1.0);
        }
    """
    FRAGMENT_SHADER = """
        uniform vec4 u_color;
        void main() { gl_FragColor = u_color; }
    """

    def __init__(self, context):
        self.context = context
        self.functions = context.functions()
        self.functions.initializeOpenGLFunctions()
        # Qt 6.9 ainda nao fornece a classe versionada 4.6 em todas as builds,
        # embora o driver anuncie 4.6. MultiDraw pertence ao desktop GL 1.4,
        # entao pedimos exatamente essa interface e caimos no loop portavel em
        # OpenGL ES ou drivers antigos.
        profile = QOpenGLVersionProfile()
        profile.setVersion(1, 4)
        try:
            version_functions = QOpenGLVersionFunctionsFactory.get(profile, context)
        except RuntimeError:
            version_functions = None
        if version_functions is not None:
            version_functions.initializeOpenGLFunctions()
        self.version_functions = (
            version_functions
            if version_functions is not None
            and hasattr(version_functions, "glMultiDrawArrays")
            else None
        )
        self.program = QOpenGLShaderProgram()
        if not self.program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Vertex, self.VERTEX_SHADER
        ):
            raise RuntimeError(self.program.log())
        if not self.program.addShaderFromSourceCode(
            QOpenGLShader.ShaderTypeBit.Fragment, self.FRAGMENT_SHADER
        ):
            raise RuntimeError(self.program.log())
        self.program.bindAttributeLocation("a_coord", 0)
        if not self.program.link():
            raise RuntimeError(self.program.log())

        self.active_snapshot: GpuSnapshot | None = None
        self._active: _BufferSet | None = None
        self._upload_snapshot: GpuSnapshot | None = None
        self._upload_at = 0
        self._upload: _BufferSet | None = None
        self._upload_vertex_at = 0
        self._upload_coarse_at = 0

    @property
    def uploading(self) -> bool:
        return self._upload_snapshot is not None

    def request(self, snapshot: GpuSnapshot | None) -> None:
        if snapshot is None:
            return
        if self.active_snapshot is snapshot or self._upload_snapshot is snapshot:
            return
        self._destroy(self._upload)
        self._upload = None
        self._upload_at = 0
        self._upload_vertex_at = 0
        self._upload_coarse_at = 0
        self._upload_snapshot = snapshot

    def upload_step(self, budget_ms: float = UPLOAD_SLICE_MS) -> bool:
        snapshot = self._upload_snapshot
        if snapshot is None:
            return True
        if self._upload is None:
            vertex_bytes = sum(batch.vertices.nbytes for batch in snapshot.batches)
            coarse_bytes = sum(batch.coarse_vertices.nbytes for batch in snapshot.batches)
            try:
                detailed = self._allocate_buffer(vertex_bytes)
                coarse = self._allocate_buffer(coarse_bytes)
            except RuntimeError:
                if "detailed" in locals() and detailed.isCreated():
                    detailed.destroy()
                raise
            self._upload = _BufferSet(detailed, coarse, [])
        deadline = time.perf_counter() + max(0.0, budget_ms) / 1000.0
        while self._upload_at < len(snapshot.batches) and time.perf_counter() < deadline:
            batch = snapshot.batches[self._upload_at]
            upload = self._upload
            self._write_buffer(upload.buffer, self._upload_vertex_at, batch.vertices)
            self._write_buffer(
                upload.coarse_buffer, self._upload_coarse_at, batch.coarse_vertices
            )
            upload.items.append(
                _GpuBuffer(
                    batch.bbox,
                    batch.layer,
                    batch.aci,
                    batch.alpha,
                    self._upload_vertex_at,
                    batch.vertex_count,
                    self._upload_coarse_at,
                    int(batch.coarse_vertices.shape[0]),
                    batch.max_size,
                )
            )
            self._upload_vertex_at += batch.vertex_count
            self._upload_coarse_at += int(batch.coarse_vertices.shape[0])
            self._upload_at += 1

        if self._upload_at < len(snapshot.batches):
            return False
        self._destroy(self._active)
        self._active = self._upload
        # Linhas sao independentes. Ordenar apenas a pequena tabela de ranges
        # reduz trocas de uniform de cor sem mover novamente a geometria.
        self._active.items.sort(key=lambda item: (item.aci, item.alpha))
        self._upload = None
        self.active_snapshot = snapshot
        self._upload_snapshot = None
        return True

    @staticmethod
    def _allocate_buffer(size: int) -> QOpenGLBuffer:
        vbo = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
        if not vbo.create() or not vbo.bind():
            raise RuntimeError("Nao foi possivel criar um VBO OpenGL")
        vbo.setUsagePattern(QOpenGLBuffer.UsagePattern.StaticDraw)
        # Alguns drivers recusam um buffer de zero bytes. O slot minimo nunca
        # sera desenhado, mas mantem o caminho de snapshot vazio uniforme.
        allocated = max(16, size)
        vbo.allocate(allocated)
        if vbo.size() != allocated:
            vbo.destroy()
            raise RuntimeError("Memoria de video insuficiente para a geometria")
        vbo.release()
        return vbo

    @staticmethod
    def _write_buffer(buffer: QOpenGLBuffer, first: int, vertices: np.ndarray) -> None:
        if vertices.size == 0:
            return
        payload = vertices.tobytes(order="C")
        if not buffer.bind():
            raise RuntimeError("VBO OpenGL deixou de estar acessivel")
        buffer.write(first * 16, payload, len(payload))
        buffer.release()

    def draw(self, viewport, doc, dark: bool, dpr: float = 1.0) -> None:
        active = self._active
        if self.active_snapshot is None or active is None:
            return
        center_high_x, center_low_x = split_scalar(viewport.center.x)
        center_high_y, center_low_y = split_scalar(viewport.center.y)
        sx = 2.0 * viewport.scale / max(float(viewport.width), 1.0)
        sy = 2.0 * viewport.scale / max(float(viewport.height), 1.0)
        vis = viewport.visible_bbox()
        visible: dict[str, bool] = {}

        funcs = self.functions
        funcs.glViewport(
            0,
            0,
            max(1, int(round(viewport.width * dpr))),
            max(1, int(round(viewport.height * dpr))),
        )
        funcs.glDisable(GL_DEPTH_TEST)
        funcs.glDisable(GL_CULL_FACE)
        funcs.glEnable(GL_BLEND)
        funcs.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        funcs.glLineWidth(max(1.0, float(dpr)))

        program = self.program
        program.bind()
        program.setUniformValue("u_center_high", QVector2D(center_high_x, center_high_y))
        program.setUniformValue("u_center_low", QVector2D(center_low_x, center_low_y))
        program.setUniformValue("u_ndc_scale", QVector2D(sx, sy))
        program.enableAttributeArray(0)

        detailed = []
        coarse = []
        for item in active.items:
            layer_on = visible.get(item.layer)
            if layer_on is None:
                layer_on = visible[item.layer] = doc.layer_is_visible(item.layer)
            if not layer_on or not item.bbox.intersects(vis):
                continue
            use_coarse = item.max_size * viewport.scale < 4.0 and item.coarse_count > 0
            (coarse if use_coarse else detailed).append(item)

        self._draw_ranges(active.buffer, detailed, False, dark)
        self._draw_ranges(active.coarse_buffer, coarse, True, dark)
        QOpenGLBuffer.release(QOpenGLBuffer.Type.VertexBuffer)
        program.disableAttributeArray(0)
        program.release()
        funcs.glDisable(GL_BLEND)

    def _draw_ranges(
        self,
        buffer: QOpenGLBuffer,
        items: list[_GpuBuffer],
        coarse: bool,
        dark: bool,
    ) -> None:
        if not items:
            return
        buffer.bind()
        self.program.setAttributeBuffer(0, GL_FLOAT, 0, 4, 16)
        at = 0
        while at < len(items):
            first_item = items[at]
            style = (first_item.aci, first_item.alpha)
            end = at + 1
            while end < len(items) and (items[end].aci, items[end].alpha) == style:
                end += 1
            color = aci_to_qcolor(first_item.aci, dark)
            self.program.setUniformValue(
                "u_color",
                QVector4D(
                    color.redF(), color.greenF(), color.blueF(), first_item.alpha / 255.0
                ),
            )
            group = items[at:end]
            firsts = [item.coarse_first if coarse else item.first for item in group]
            counts = [item.coarse_count if coarse else item.count for item in group]
            if self.version_functions is not None and len(group) > 1:
                self.version_functions.glMultiDrawArrays(
                    GL_LINES, firsts, counts, len(group)
                )
            else:
                for first, count in zip(firsts, counts, strict=True):
                    self.functions.glDrawArrays(GL_LINES, first, count)
            at = end

    def clear(self, color: QColor, viewport, dpr: float = 1.0) -> None:
        funcs = self.functions
        funcs.glViewport(
            0,
            0,
            max(1, int(round(viewport.width * dpr))),
            max(1, int(round(viewport.height * dpr))),
        )
        funcs.glClearColor(color.redF(), color.greenF(), color.blueF(), 1.0)
        funcs.glClear(GL_COLOR_BUFFER_BIT)

    def destroy(self) -> None:
        self._destroy(self._upload)
        self._destroy(self._active)
        self._upload = None
        self._active = None
        self._upload_snapshot = None
        self.active_snapshot = None

    @staticmethod
    def _destroy(buffers: _BufferSet | None) -> None:
        if buffers is None:
            return
        if buffers.buffer.isCreated():
            buffers.buffer.destroy()
        if buffers.coarse_buffer.isCreated():
            buffers.coarse_buffer.destroy()
