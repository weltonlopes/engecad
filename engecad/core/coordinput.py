"""Entrada de coordenada por teclado -- o que separa desenhar 'no olho' de
desenhar com precisao topografica.

Formatos aceitos:

    500123.45,7412987.12   absoluto no CRS do projeto
    @10,0                  relativo ao ultimo ponto (delta X, delta Y)
    @10<90                 polar relativo: 10 m no angulo CAD (anti-horario a partir do leste)
    @10<<45                polar relativo por AZIMUTE (horario a partir do norte)
    @10<<45d30'20"         azimute em grau/minuto/segundo
    10<90                  polar absoluto a partir da origem

Separador pode ser virgula ou ponto e virgula. Espacos sao ignorados.
"""

from __future__ import annotations

import math
import re

from .geometry import Vec2

_DMS_RE = re.compile(
    r"""^\s*(?P<sign>[+-])?\s*
        (?P<deg>\d+(?:\.\d+)?)\s*(?:[d°:]\s*)?
        (?:(?P<min>\d+(?:\.\d+)?)\s*(?:['m′:]\s*)?)?
        (?:(?P<sec>\d+(?:\.\d+)?)\s*(?:["s″]\s*)?)?
        \s*$""",
    re.VERBOSE | re.IGNORECASE,
)


def parse_angle(text: str) -> float | None:
    """Angulo em graus decimais. Aceita '45', '45.5', \"45d30'20\", '45°30'20\"'."""
    t = text.strip()
    if not t:
        return None
    m = _DMS_RE.match(t)
    if not m:
        return None
    try:
        deg = float(m.group("deg"))
    except (TypeError, ValueError):
        return None
    deg += float(m.group("min") or 0) / 60.0
    deg += float(m.group("sec") or 0) / 3600.0
    return -deg if m.group("sign") == "-" else deg


def _split_pair(body: str) -> tuple[str, str] | None:
    for sep in (",", ";"):
        if sep in body:
            a, _, b = body.partition(sep)
            return a.strip(), b.strip()
    return None


def parse_coordinate(text: str, last: Vec2 | None = None) -> Vec2 | None:
    """Interpreta uma entrada de coordenada. None se nao reconhecer."""
    t = text.strip().replace(" ", "")
    if not t:
        return None

    relative = t.startswith("@")
    if relative:
        t = t[1:]
        if last is None:
            last = Vec2(0.0, 0.0)

    # polar por azimute: dist<<az   (checar antes de '<' simples)
    if "<<" in t:
        d_txt, _, a_txt = t.partition("<<")
        dist = _to_float(d_txt)
        ang = parse_angle(a_txt)
        if dist is None or ang is None:
            return None
        # azimute: horario a partir do norte -> angulo matematico
        rad = math.radians(90.0 - ang)
        base = last if relative else Vec2(0.0, 0.0)
        return Vec2.polar(base, rad, dist)

    # polar CAD: dist<angulo
    if "<" in t:
        d_txt, _, a_txt = t.partition("<")
        dist = _to_float(d_txt)
        ang = parse_angle(a_txt)
        if dist is None or ang is None:
            return None
        base = last if relative else Vec2(0.0, 0.0)
        return Vec2.polar(base, math.radians(ang), dist)

    # cartesiano
    pair = _split_pair(t)
    if pair is None:
        return None
    x, y = _to_float(pair[0]), _to_float(pair[1])
    if x is None or y is None:
        return None
    if relative:
        return Vec2(last.x + x, last.y + y)
    return Vec2(x, y)


def _to_float(s: str) -> float | None:
    try:
        return float(s.strip())
    except (ValueError, AttributeError):
        return None


def format_coordinate(p: Vec2, decimals: int = 3) -> str:
    return f"{p.x:.{decimals}f}, {p.y:.{decimals}f}"
