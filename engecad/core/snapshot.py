"""Instantaneo da geometria de uma entidade, para desfazer edicoes de forma exata.

Por que nao usar a matriz inversa
---------------------------------
Desfazer um MOVE aplicando a matriz inversa parece elegante, mas nao devolve o
valor identico: medido em ezdxf, mover 500010.0 e voltar da 500009.99999999907.
Sao nanometros -- irrelevante para topografia, mas num CAD o desfazer tem de
devolver exatamente o numero que estava la, e a deriva acumula em ciclos
repetidos de desfazer/refazer.

Guardar e restaurar os valores crus resolve isso, e de quebra serve para
edicoes que nao sao transformacoes afins (TRIM, EXTEND, arrastar grip), onde
nao existe matriz inversa nenhuma.
"""

from __future__ import annotations

SUPPORTED = {
    "LINE",
    "LWPOLYLINE",
    "CIRCLE",
    "ARC",
    "ELLIPSE",
    "POINT",
    "TEXT",
    "MTEXT",
    "INSERT",
}


def supports(entity) -> bool:
    return entity.dxftype() in SUPPORTED


def _xy(v) -> tuple[float, float, float]:
    return (float(v.x), float(v.y), float(getattr(v, "z", 0.0)))


def snapshot(entity) -> dict | None:
    """Congela a geometria da entidade. None se o tipo nao for suportado."""
    if not entity.is_alive:
        return None
    t = entity.dxftype()
    dxf = entity.dxf

    if t == "LINE":
        return {"_t": t, "start": _xy(dxf.start), "end": _xy(dxf.end)}

    if t == "LWPOLYLINE":
        return {
            "_t": t,
            "points": [tuple(p) for p in entity.get_points("xyseb")],
            "closed": bool(entity.closed),
            "elevation": float(dxf.get("elevation", 0.0)),
        }

    if t == "CIRCLE":
        return {"_t": t, "center": _xy(dxf.center), "radius": float(dxf.radius)}

    if t == "ARC":
        return {
            "_t": t,
            "center": _xy(dxf.center),
            "radius": float(dxf.radius),
            "start_angle": float(dxf.start_angle),
            "end_angle": float(dxf.end_angle),
        }

    if t == "ELLIPSE":
        return {
            "_t": t,
            "center": _xy(dxf.center),
            "major_axis": _xy(dxf.major_axis),
            "ratio": float(dxf.ratio),
            "start_param": float(dxf.start_param),
            "end_param": float(dxf.end_param),
        }

    if t == "POINT":
        return {"_t": t, "location": _xy(dxf.location)}

    if t == "TEXT":
        snap = {
            "_t": t,
            "insert": _xy(dxf.insert),
            "height": float(dxf.height),
            "rotation": float(dxf.get("rotation", 0.0)),
            "text": str(dxf.text),
        }
        if dxf.hasattr("align_point"):
            snap["align_point"] = _xy(dxf.align_point)
        return snap

    if t == "MTEXT":
        return {
            "_t": t,
            "insert": _xy(dxf.insert),
            "char_height": float(dxf.char_height),
            "rotation": float(dxf.get("rotation", 0.0)),
            "text": str(entity.text),
        }

    if t == "INSERT":
        return {
            "_t": t,
            "insert": _xy(dxf.insert),
            "rotation": float(dxf.get("rotation", 0.0)),
            "xscale": float(dxf.get("xscale", 1.0)),
            "yscale": float(dxf.get("yscale", 1.0)),
        }

    return None


def restore(entity, snap: dict | None) -> bool:
    """Devolve a entidade ao estado do instantaneo. True se conseguiu."""
    if snap is None or not entity.is_alive or entity.dxftype() != snap.get("_t"):
        return False
    t = snap["_t"]
    dxf = entity.dxf

    if t == "LINE":
        dxf.start = snap["start"]
        dxf.end = snap["end"]
        return True

    if t == "LWPOLYLINE":
        entity.set_points(snap["points"], format="xyseb")
        entity.closed = snap["closed"]
        dxf.elevation = snap["elevation"]
        return True

    if t == "CIRCLE":
        dxf.center = snap["center"]
        dxf.radius = snap["radius"]
        return True

    if t == "ARC":
        dxf.center = snap["center"]
        dxf.radius = snap["radius"]
        dxf.start_angle = snap["start_angle"]
        dxf.end_angle = snap["end_angle"]
        return True

    if t == "ELLIPSE":
        dxf.center = snap["center"]
        dxf.major_axis = snap["major_axis"]
        dxf.ratio = snap["ratio"]
        dxf.start_param = snap["start_param"]
        dxf.end_param = snap["end_param"]
        return True

    if t == "POINT":
        dxf.location = snap["location"]
        return True

    if t == "TEXT":
        dxf.insert = snap["insert"]
        dxf.height = snap["height"]
        dxf.rotation = snap["rotation"]
        dxf.text = snap["text"]
        if "align_point" in snap:
            dxf.align_point = snap["align_point"]
        return True

    if t == "MTEXT":
        dxf.insert = snap["insert"]
        dxf.char_height = snap["char_height"]
        dxf.rotation = snap["rotation"]
        entity.text = snap["text"]
        return True

    if t == "INSERT":
        dxf.insert = snap["insert"]
        dxf.rotation = snap["rotation"]
        dxf.xscale = snap["xscale"]
        dxf.yscale = snap["yscale"]
        return True

    return False
