"""Sistema de coordenadas do projeto, sobre PROJ via pyproj."""

from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

WGS84 = "EPSG:4326"
# SIRGAS 2000 / UTM 22S cobre boa parte do sul/sudeste -- ponto de partida sensato.
DEFAULT_CRS = "EPSG:31982"

# SIRGAS 2000 / UTM -- padrao oficial brasileiro desde 2015.
# Hemisferio sul: EPSG = 31960 + zona (17S..25S -> 31977..31985)
# Hemisferio norte: EPSG = 31954 + zona (18N..22N -> 31972..31976)
_UTM_SOUTH_BASE = 31960
_UTM_NORTH_BASE = 31954
_UTM_SOUTH_ZONES = range(17, 26)
_UTM_NORTH_ZONES = range(18, 23)

COMMON_CRS: list[tuple[str, str]] = [
    ("EPSG:31982", "SIRGAS 2000 / UTM 22S  (PR, SC, RS, MS, SP oeste)"),
    ("EPSG:31983", "SIRGAS 2000 / UTM 23S  (SP, RJ, MG, GO, DF)"),
    ("EPSG:31984", "SIRGAS 2000 / UTM 24S  (BA, SE, AL, PE leste)"),
    ("EPSG:31985", "SIRGAS 2000 / UTM 25S  (PB, RN, CE, PE leste)"),
    ("EPSG:31981", "SIRGAS 2000 / UTM 21S  (MT, MS oeste)"),
    ("EPSG:31980", "SIRGAS 2000 / UTM 20S  (MT, RO, AM)"),
    ("EPSG:31979", "SIRGAS 2000 / UTM 19S  (AC, AM oeste)"),
    ("EPSG:31978", "SIRGAS 2000 / UTM 18S  (AC extremo oeste)"),
    ("EPSG:31977", "SIRGAS 2000 / UTM 17S"),
    ("EPSG:4674", "SIRGAS 2000 (geografico, graus)"),
    ("EPSG:4326", "WGS 84 (geografico, graus)"),
    ("EPSG:3857", "Web Mercator (tiles)"),
]


def sirgas_utm_epsg(zone: int, south: bool = True) -> str:
    if south:
        if zone not in _UTM_SOUTH_ZONES:
            raise ValueError(f"zona UTM {zone}S sem definicao SIRGAS 2000")
        return f"EPSG:{_UTM_SOUTH_BASE + zone}"
    if zone not in _UTM_NORTH_ZONES:
        raise ValueError(f"zona UTM {zone}N sem definicao SIRGAS 2000")
    return f"EPSG:{_UTM_NORTH_BASE + zone}"


def utm_zone_from_lon(lon: float) -> int:
    return int((lon + 180) // 6) + 1


def suggest_utm_from_lonlat(lon: float, lat: float) -> str:
    """CRS SIRGAS 2000 / UTM provavel para uma coordenada geografica."""
    try:
        return sirgas_utm_epsg(utm_zone_from_lon(lon), south=lat < 0)
    except ValueError:
        return WGS84


class ProjectCRS:
    """CRS do projeto. Toda coordenada do documento vive neste sistema."""

    def __init__(self, user_input: str | int | CRS | None = DEFAULT_CRS):
        if user_input is None or user_input == "":
            user_input = DEFAULT_CRS
        self.crs = user_input if isinstance(user_input, CRS) else CRS.from_user_input(user_input)

    @staticmethod
    def is_valid(user_input) -> bool:
        try:
            CRS.from_user_input(user_input)
            return True
        except (CRSError, ValueError, TypeError):
            return False

    @property
    def epsg(self) -> int | None:
        return self.crs.to_epsg()

    @property
    def srid(self) -> str:
        e = self.epsg
        return f"EPSG:{e}" if e else "CRS personalizado"

    @property
    def name(self) -> str:
        return self.crs.name

    @property
    def is_projected(self) -> bool:
        return bool(self.crs.is_projected)

    @property
    def unit_name(self) -> str:
        try:
            return self.crs.axis_info[0].unit_name
        except (IndexError, AttributeError):
            return "unknown"

    @property
    def unit_suffix(self) -> str:
        u = self.unit_name.lower()
        if "metre" in u or "meter" in u:
            return "m"
        if "degree" in u:
            return "\N{DEGREE SIGN}"
        return ""

    @property
    def decimals(self) -> int:
        """Casas decimais adequadas para exibir coordenada nesta unidade."""
        return 3 if self.is_projected else 8

    @property
    def display(self) -> str:
        return f"{self.srid} - {self.name}"

    def to_wkt(self) -> str:
        return self.crs.to_wkt()

    def transformer_to(self, other: ProjectCRS | str) -> Transformer:
        target = other.crs if isinstance(other, ProjectCRS) else CRS.from_user_input(other)
        return _cached_transformer(self.crs.to_wkt(), target.to_wkt())

    def to_wgs84(self, x: float, y: float) -> tuple[float, float]:
        """Retorna (lon, lat)."""
        return self.transformer_to(WGS84).transform(x, y)

    def from_wgs84(self, lon: float, lat: float) -> tuple[float, float]:
        tr = _cached_transformer(CRS.from_user_input(WGS84).to_wkt(), self.crs.to_wkt())
        return tr.transform(lon, lat)

    def __eq__(self, other) -> bool:
        return isinstance(other, ProjectCRS) and self.crs == other.crs

    def __hash__(self) -> int:
        return hash(self.crs.to_wkt())

    def __repr__(self) -> str:
        return f"ProjectCRS({self.srid})"


@lru_cache(maxsize=64)
def _cached_transformer(src_wkt: str, dst_wkt: str) -> Transformer:
    # always_xy garante ordem (x, y) / (lon, lat) independente do eixo declarado no EPSG.
    return Transformer.from_crs(CRS.from_wkt(src_wkt), CRS.from_wkt(dst_wkt), always_xy=True)
