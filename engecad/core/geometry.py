"""Geometria 2D em float64. Sem dependencia de Qt -- testavel sem GUI."""

from __future__ import annotations

import math
from dataclasses import dataclass

TAU = math.tau
EPS = 1e-12


@dataclass(frozen=True, slots=True)
class Vec2:
    """Ponto/vetor no plano, em unidades do mundo (metros no CRS do projeto)."""

    x: float = 0.0
    y: float = 0.0

    def __add__(self, o: Vec2) -> Vec2:
        return Vec2(self.x + o.x, self.y + o.y)

    def __sub__(self, o: Vec2) -> Vec2:
        return Vec2(self.x - o.x, self.y - o.y)

    def __mul__(self, k: float) -> Vec2:
        return Vec2(self.x * k, self.y * k)

    __rmul__ = __mul__

    def __truediv__(self, k: float) -> Vec2:
        return Vec2(self.x / k, self.y / k)

    def __neg__(self) -> Vec2:
        return Vec2(-self.x, -self.y)

    def __iter__(self):
        yield self.x
        yield self.y

    def dot(self, o: Vec2) -> float:
        return self.x * o.x + self.y * o.y

    def cross(self, o: Vec2) -> float:
        return self.x * o.y - self.y * o.x

    @property
    def length(self) -> float:
        return math.hypot(self.x, self.y)

    @property
    def length_sq(self) -> float:
        return self.x * self.x + self.y * self.y

    def normalized(self) -> Vec2:
        n = self.length
        return Vec2(self.x / n, self.y / n) if n > EPS else Vec2()

    def distance_to(self, o: Vec2) -> float:
        return math.hypot(self.x - o.x, self.y - o.y)

    @property
    def angle(self) -> float:
        """Angulo em radianos, anti-horario a partir do eixo +X."""
        return math.atan2(self.y, self.x)

    def rotated(self, rad: float, origin: Vec2 | None = None) -> Vec2:
        o = origin or Vec2()
        c, s = math.cos(rad), math.sin(rad)
        d = self - o
        return Vec2(o.x + d.x * c - d.y * s, o.y + d.x * s + d.y * c)

    def rounded(self, nd: int = 4) -> Vec2:
        return Vec2(round(self.x, nd), round(self.y, nd))

    @staticmethod
    def polar(origin: Vec2, rad: float, dist: float) -> Vec2:
        return Vec2(origin.x + dist * math.cos(rad), origin.y + dist * math.sin(rad))

    @staticmethod
    def of(p) -> Vec2:
        """Aceita Vec2, tupla, lista ou objeto com .x/.y."""
        if isinstance(p, Vec2):
            return p
        if hasattr(p, "x") and hasattr(p, "y"):
            return Vec2(float(p.x), float(p.y))
        return Vec2(float(p[0]), float(p[1]))


@dataclass(frozen=True, slots=True)
class BBox:
    """Retangulo alinhado aos eixos. minx>maxx sinaliza bbox vazia."""

    minx: float = math.inf
    miny: float = math.inf
    maxx: float = -math.inf
    maxy: float = -math.inf

    @property
    def is_empty(self) -> bool:
        return self.minx > self.maxx or self.miny > self.maxy

    @property
    def width(self) -> float:
        return max(0.0, self.maxx - self.minx)

    @property
    def height(self) -> float:
        return max(0.0, self.maxy - self.miny)

    @property
    def center(self) -> Vec2:
        return Vec2((self.minx + self.maxx) / 2, (self.miny + self.maxy) / 2)

    def union(self, o: BBox) -> BBox:
        if self.is_empty:
            return o
        if o.is_empty:
            return self
        return BBox(
            min(self.minx, o.minx),
            min(self.miny, o.miny),
            max(self.maxx, o.maxx),
            max(self.maxy, o.maxy),
        )

    def expanded(self, d: float) -> BBox:
        if self.is_empty:
            return self
        return BBox(self.minx - d, self.miny - d, self.maxx + d, self.maxy + d)

    def intersects(self, o: BBox) -> bool:
        # Chamado centenas de vezes por movimento do mouse, pelo indice espacial.
        # A bbox vazia tem minx=+inf e maxx=-inf, entao as comparacoes abaixo ja
        # a rejeitam: nao vale pagar duas chamadas de propriedade para checar.
        return not (
            o.minx > self.maxx or o.maxx < self.minx or o.miny > self.maxy or o.maxy < self.miny
        )

    def contains(self, p: Vec2) -> bool:
        return self.minx <= p.x <= self.maxx and self.miny <= p.y <= self.maxy

    @staticmethod
    def of_points(pts) -> BBox:
        b = BBox()
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        if not xs:
            return b
        return BBox(min(xs), min(ys), max(xs), max(ys))


def closest_point_on_segment(p: Vec2, a: Vec2, b: Vec2) -> Vec2:
    """Projecao de p sobre o segmento ab, grampeada aos extremos."""
    ab = b - a
    ll = ab.length_sq
    if ll < EPS:
        return a
    t = max(0.0, min(1.0, (p - a).dot(ab) / ll))
    return a + ab * t


def distance_to_segment(p: Vec2, a: Vec2, b: Vec2) -> float:
    return p.distance_to(closest_point_on_segment(p, a, b))


def line_intersection(a1: Vec2, a2: Vec2, b1: Vec2, b2: Vec2, as_segments: bool = True):
    """Interseccao de duas retas/segmentos. None se paralelos ou fora do segmento."""
    d1 = a2 - a1
    d2 = b2 - b1
    den = d1.cross(d2)
    if abs(den) < EPS:
        return None
    diff = b1 - a1
    t = diff.cross(d2) / den
    u = diff.cross(d1) / den
    if as_segments and not (-EPS <= t <= 1 + EPS and -EPS <= u <= 1 + EPS):
        return None
    return a1 + d1 * t


def line_circle_intersection(a: Vec2, b: Vec2, center: Vec2, radius: float,
                             as_segment: bool = True) -> list[Vec2]:
    """Intersecoes exatas de uma reta/segmento com um circulo.

    Usado no aparar: achatar o circulo em segmentos deixaria a linha aparada
    alguns milimetros fora do circulo, o que num CAD cadastral aparece.
    """
    d = b - a
    f = a - center
    aa = d.length_sq
    if aa < EPS:
        return []
    bb = 2 * f.dot(d)
    cc = f.length_sq - radius * radius
    disc = bb * bb - 4 * aa * cc
    if disc < 0:
        return []
    disc = math.sqrt(disc)
    out = []
    for t in ((-bb - disc) / (2 * aa), (-bb + disc) / (2 * aa)):
        if as_segment and not (-EPS <= t <= 1 + EPS):
            continue
        out.append(a + d * t)
    # tangente: as duas raizes coincidem
    if len(out) == 2 and out[0].distance_to(out[1]) < EPS:
        out.pop()
    return out


def circle_from_3points(a: Vec2, b: Vec2, c: Vec2):
    """Circulo que passa por tres pontos: (centro, raio). None se colineares."""
    d = 2 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y))
    if abs(d) < EPS:
        return None
    a2, b2, c2 = a.length_sq, b.length_sq, c.length_sq
    ux = (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d
    uy = (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d
    center = Vec2(ux, uy)
    return center, center.distance_to(a)


def circle_circle_intersection(c1: Vec2, r1: float, c2: Vec2, r2: float) -> list[Vec2]:
    """Intersecoes exatas de dois circulos."""
    d = c1.distance_to(c2)
    if d < EPS or d > r1 + r2 + EPS or d < abs(r1 - r2) - EPS:
        return []
    a = (r1 * r1 - r2 * r2 + d * d) / (2 * d)
    h_sq = r1 * r1 - a * a
    if h_sq < 0:
        h_sq = 0.0
    h = math.sqrt(h_sq)
    base = c1 + (c2 - c1) * (a / d)
    if h < EPS:
        return [base]
    off = Vec2(-(c2.y - c1.y) * h / d, (c2.x - c1.x) * h / d)
    return [base + off, base - off]


def angle_in_range(angle: float, start: float, end: float) -> bool:
    """O angulo (graus) esta no arco que vai de start a end no sentido anti-horario?"""
    a = (angle - start) % 360.0
    span = (end - start) % 360.0
    if span < EPS:
        span = 360.0
    return a <= span + 1e-9


def normal_left(d: Vec2) -> Vec2:
    """Normal unitaria a esquerda da direcao d."""
    n = d.normalized()
    return Vec2(-n.y, n.x)


def point_side(p: Vec2, a: Vec2, b: Vec2) -> float:
    """Sinal do lado de p em relacao a reta ab: +1 esquerda, -1 direita."""
    c = (b - a).cross(p - a)
    return 1.0 if c > 0 else (-1.0 if c < 0 else 0.0)


def polyline_length(pts, closed: bool = False) -> float:
    if len(pts) < 2:
        return 0.0
    total = sum(pts[i].distance_to(pts[i + 1]) for i in range(len(pts) - 1))
    if closed:
        total += pts[-1].distance_to(pts[0])
    return total


def polygon_area(pts) -> float:
    """Area por formula do sapateiro. Retorna valor absoluto."""
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        j = (i + 1) % n
        s += pts[i].x * pts[j].y - pts[j].x * pts[i].y
    return abs(s) / 2.0


def azimuth(a: Vec2, b: Vec2) -> float:
    """Azimute topografico de a para b: graus, horario, 0 = Norte."""
    d = b - a
    deg = math.degrees(math.atan2(d.x, d.y))
    return deg + 360.0 if deg < 0 else deg


def format_dms(deg: float) -> str:
    """Graus decimais -> 123deg45'56.7\" no padrao de memorial descritivo."""
    sign = "-" if deg < 0 else ""
    deg = abs(deg)
    d = int(deg)
    m_full = (deg - d) * 60
    m = int(m_full)
    s = (m_full - m) * 60
    if round(s, 1) >= 60.0:
        s = 0.0
        m += 1
    if m >= 60:
        m = 0
        d += 1
    return f"{sign}{d}°{m:02d}'{s:04.1f}\""
