"""Superficie estavel exposta aos scripts do usuario.

Deliberadamente pequena. Tudo aqui e contrato publico: scripts de usuario vao
depender destes nomes, entao acrescentar e barato e renomear e caro. O acesso
cru continua disponivel por `doc.drawing` (ezdxf) para quem precisar de mais.
"""

from __future__ import annotations

from ..core.geometry import Vec2, azimuth, polygon_area, polyline_length


class ScriptAPI:
    def __init__(self, ctx):
        self._ctx = ctx

    # ---------------- acesso ----------------

    @property
    def ctx(self):
        return self._ctx

    @property
    def doc(self):
        """Documento corrente (Document). doc.drawing e o ezdxf.Drawing cru."""
        return self._ctx.doc

    @property
    def view(self):
        """Viewport corrente."""
        return self._ctx.viewport

    @property
    def crs(self):
        return self._ctx.doc.crs

    # ---------------- criacao ----------------

    def add_line(self, a, b, layer=None):
        return self.doc.add_line(a, b, layer=layer)

    def add_polyline(self, points, closed=False, layer=None):
        return self.doc.add_lwpolyline(points, closed=closed, layer=layer)

    def add_circle(self, center, radius, layer=None):
        return self.doc.add_circle(center, radius, layer=layer)

    def add_point(self, p, layer=None):
        return self.doc.add_point(p, layer=layer)

    def add_text(self, text, at, height=1.0, layer=None):
        return self.doc.add_text(text, at, height=height, layer=layer)

    def erase(self, entities):
        if not isinstance(entities, (list, tuple, set)):
            entities = [entities]
        self.doc.delete(entities)

    # ---------------- consulta ----------------

    def entities(self, layer=None, dxftype=None) -> list:
        out = []
        for e in self.doc.entities():
            if layer and e.dxf.get("layer", "0") != layer:
                continue
            if dxftype and e.dxftype() != str(dxftype).upper():
                continue
            out.append(e)
        return out

    def count(self, **kw) -> int:
        return len(self.entities(**kw))

    def near(self, point, radius: float) -> list:
        return self.doc.query_point(Vec2.of(point), radius)

    def extents(self):
        return self.doc.extents()

    # ---------------- camadas ----------------

    def layers(self) -> list[str]:
        return self.doc.layer_names()

    def set_layer(self, name: str):
        self.doc.current_layer = name
        self._ctx.documentChanged.emit()

    def new_layer(self, name: str, color: int = 7):
        return self.doc.ensure_layer(name, color)

    def show_layer(self, name: str, visible: bool = True):
        self.doc.set_layer_visible(name, visible)

    # ---------------- comandos ----------------

    def command(self, name: str, *args) -> bool:
        """Aciona um comando do registro -- o mesmo caminho da linha de comando."""
        return self._ctx.run_command(name, *args)

    def commands(self) -> list[str]:
        return self._ctx.registry.names()

    # ---------------- vista ----------------

    def zoom_extents(self):
        self._ctx.zoom_extents()

    def zoom_scale(self, denom: float):
        self.view.set_scale_denominator(denom)
        self._ctx.view_changed()

    def pan(self, x, y=None):
        self.view.center = Vec2.of(x if y is None else (x, y))
        self._ctx.view_changed()

    def refresh(self):
        self._ctx.refresh()

    # ---------------- geodesia e medidas ----------------

    def to_wgs84(self, x, y=None):
        p = Vec2.of(x if y is None else (x, y))
        return self.crs.to_wgs84(p.x, p.y)

    def from_wgs84(self, lon, lat):
        return self.crs.from_wgs84(lon, lat)

    @staticmethod
    def dist(a, b) -> float:
        return Vec2.of(a).distance_to(Vec2.of(b))

    @staticmethod
    def azimuth(a, b) -> float:
        return azimuth(Vec2.of(a), Vec2.of(b))

    @staticmethod
    def area(points) -> float:
        return polygon_area([Vec2.of(p) for p in points])

    @staticmethod
    def length(points, closed=False) -> float:
        return polyline_length([Vec2.of(p) for p in points], closed=closed)

    # ---------------- utilidades ----------------

    def message(self, text: str):
        self._ctx.message(str(text))

    def save(self, path=None):
        from ..io.project import save_sidecar

        p = self.doc.save(path)
        save_sidecar(self._ctx, p)
        return p

    def undo(self):
        self.doc.undo.undo()

    def redo(self):
        self.doc.undo.redo()

    def help(self) -> str:
        names = [n for n in dir(self) if not n.startswith("_")]
        return "API do EngeCAD: " + ", ".join(sorted(names))


def build_namespace(ctx) -> dict:
    """Nomes ja disponiveis no console, sem precisar de import."""
    api = ScriptAPI(ctx)
    ns = {
        "api": api,
        "ctx": ctx,
        "Vec2": Vec2,
        "__name__": "__console__",
    }
    # metodos da api soltos no topo: add_line(...) em vez de api.add_line(...)
    for name in dir(api):
        if name.startswith("_"):
            continue
        ns.setdefault(name, getattr(api, name))
    return ns
