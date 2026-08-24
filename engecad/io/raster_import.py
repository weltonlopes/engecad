"""Importacao de raster, com a cadeia de contorno do ECW.

Por que isso existe
-------------------
ECW e formato proprietario da Hexagon. A leitura e gratuita no desktop, mas a
biblioteca NAO e redistribuivel -- e por isso que nem o QGIS a embute, e por
isso que o GDAL que vem nos wheels do rasterio nao traz o driver.

Consequencia pratica: com um `pip install` puro nao da para ler NEM converter
ECW. Precisa existir, em algum lugar da maquina, um GDAL com o driver -- na
pratica o pacote `gdal-ecw` do OSGeo4W, ou o GDAL que acompanha o QGIS.

A cadeia:
  1. rasterio abre direto?              -> usa
  2. existe GDAL externo com ECW?       -> converte para COG uma unica vez
  3. nenhum dos dois                    -> instrui a instalar o gdal-ecw

O GDAL externo e chamado por SUBPROCESS, jamais importado. Carregar duas
copias de GDAL/PROJ no mesmo processo Python e causa classica de crash
silencioso no Windows.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import rasterio

# Formatos que abrem sem nenhuma dependencia externa.
NATIVE_SUFFIXES = {".tif", ".tiff", ".gtiff", ".img", ".jpg", ".jpeg", ".png", ".vrt", ".jp2"}
PROPRIETARY_SUFFIXES = {".ecw", ".sid"}

INSTALL_HINT = (
    "Este arquivo precisa de um GDAL com o driver ECW, que nao pode ser\n"
    "distribuido junto com o EngeCAD (licenca da Hexagon).\n\n"
    "Como resolver:\n"
    "  1. Instale o OSGeo4W:  https://trac.osgeo.org/osgeo4w/\n"
    "  2. No instalador, selecione o pacote  gdal-ecw\n"
    "  3. Reabra o EngeCAD (ele procura em C:\\OSGeo4W\\bin automaticamente)\n\n"
    "Alternativa: aponte a variavel de ambiente ENGECAD_GDAL_BIN para a pasta\n"
    "bin de um GDAL que ja tenha o driver."
)

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class RasterImportError(Exception):
    pass


# ---------------- deteccao ----------------


def rasterio_can_open(path: str | Path) -> bool:
    """O GDAL embutido consegue abrir? (para ECW, quase sempre nao)"""
    try:
        with rasterio.open(str(path)):
            return True
    except Exception:
        return False


def candidate_gdal_dirs() -> list[Path]:
    """Pastas onde um GDAL externo costuma estar, em ordem de preferencia."""
    out: list[Path] = []
    env = os.environ.get("ENGECAD_GDAL_BIN")
    if env:
        out.append(Path(env))
    out.append(Path(r"C:\OSGeo4W\bin"))
    out.append(Path(r"C:\OSGeo4W64\bin"))
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
        if base.exists():
            try:
                out.extend(sorted(base.glob("QGIS*/bin"), reverse=True))
            except OSError:
                pass
    return [p for p in out if p.exists()]


def _run(cmd: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=_CREATE_NO_WINDOW,
    )


def gdal_dir_supports(bin_dir: Path, driver: str = "ECW") -> bool:
    exe = bin_dir / ("gdalinfo.exe" if os.name == "nt" else "gdalinfo")
    if not exe.exists():
        return False
    try:
        res = _run([str(exe), "--formats"])
    except (OSError, subprocess.SubprocessError):
        return False
    return driver.upper() in res.stdout.upper()


def find_external_gdal(driver: str = "ECW") -> Path | None:
    """Primeira pasta bin de GDAL externo que tenha o driver pedido."""
    for d in candidate_gdal_dirs():
        if gdal_dir_supports(d, driver):
            return d
    return None


def driver_for(path: str | Path) -> str:
    return Path(path).suffix.lower().lstrip(".").upper().replace("SID", "MrSID")


# ---------------- conversao ----------------


def cog_target_for(src: str | Path, out_dir: Path | None = None) -> Path:
    src = Path(src)
    base = out_dir or src.parent
    return base / (src.stem + "_cog.tif")


def convert_to_cog(
    src: str | Path,
    dst: str | Path,
    gdal_bin: Path,
    progress: Callable[[str], None] | None = None,
    timeout: int = 3600,
) -> Path:
    """Converte para Cloud Optimized GeoTIFF usando o GDAL externo.

    COG ja traz overviews internos e blocos, que e exatamente o que o canvas
    precisa para navegar rapido em qualquer zoom.
    """
    src, dst = Path(src), Path(dst)
    exe = gdal_bin / ("gdal_translate.exe" if os.name == "nt" else "gdal_translate")
    if not exe.exists():
        raise RasterImportError(f"gdal_translate nao encontrado em {gdal_bin}")
    cmd = [
        str(exe),
        "-of", "COG",
        "-co", "COMPRESS=DEFLATE",
        "-co", "PREDICTOR=YES",
        "-co", "NUM_THREADS=ALL_CPUS",
        "-co", "BIGTIFF=IF_SAFER",
        str(src),
        str(dst),
    ]
    if progress:
        progress(f"Convertendo {src.name} para COG (uma unica vez)...")
    try:
        res = _run(cmd, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RasterImportError(f"conversao excedeu {timeout}s") from exc
    if res.returncode != 0 or not dst.exists():
        raise RasterImportError(f"gdal_translate falhou:\n{res.stderr.strip()[:800]}")
    if progress:
        progress(f"Convertido: {dst.name}")
    return dst


# ---------------- orquestracao ----------------


class ImportPlan:
    """O que fazer com um arquivo, decidido antes de mexer no disco."""

    def __init__(self, source: Path, action: str, target: Path | None, gdal_bin: Path | None,
                 message: str = ""):
        self.source = source
        self.action = action  # "direct" | "convert" | "blocked"
        self.target = target
        self.gdal_bin = gdal_bin
        self.message = message

    @property
    def needs_conversion(self) -> bool:
        return self.action == "convert"

    @property
    def blocked(self) -> bool:
        return self.action == "blocked"


def plan_import(path: str | Path, reuse_existing_cog: bool = True) -> ImportPlan:
    """Decide a rota sem executar nada -- assim a interface pode perguntar antes."""
    src = Path(path)
    if not src.exists():
        return ImportPlan(src, "blocked", None, None, f"Arquivo nao encontrado: {src}")

    if rasterio_can_open(src):
        return ImportPlan(src, "direct", src, None, f"Aberto direto ({src.suffix.lower()})")

    cog = cog_target_for(src)
    if reuse_existing_cog and cog.exists() and rasterio_can_open(cog):
        return ImportPlan(src, "direct", cog, None, f"Usando conversao existente: {cog.name}")

    driver = driver_for(src)
    gdal_bin = find_external_gdal(driver)
    if gdal_bin is None:
        return ImportPlan(src, "blocked", None, None, INSTALL_HINT)
    return ImportPlan(
        src,
        "convert",
        cog,
        gdal_bin,
        f"O driver {driver} nao esta no GDAL embutido, mas foi encontrado em\n"
        f"{gdal_bin}.\n\nConverter para COG agora? (feito uma unica vez; "
        f"depois a navegacao fica mais rapida)",
    )


def execute_plan(plan: ImportPlan, progress: Callable[[str], None] | None = None) -> Path:
    """Executa o plano e devolve o caminho pronto para o RasterLayer."""
    if plan.blocked:
        raise RasterImportError(plan.message)
    if plan.action == "direct":
        return plan.target
    return convert_to_cog(plan.source, plan.target, plan.gdal_bin, progress)


def diagnose() -> str:
    """Texto de diagnostico para o menu Ajuda -- responde 'por que meu ECW nao abre'."""
    lines = ["Suporte a raster no EngeCAD", "=" * 34, ""]
    lines.append(f"GDAL do rasterio: {rasterio.__gdal_version__}")
    try:
        with rasterio.Env() as env:
            drivers = set(env.drivers())
    except Exception:
        drivers = set()
    for drv in ("GTiff", "COG", "ECW", "JP2OpenJPEG", "MrSID"):
        mark = "sim" if drv in drivers else "NAO"
        lines.append(f"  driver {drv:<12} {mark}")
    lines.append("")
    dirs = candidate_gdal_dirs()
    if not dirs:
        lines.append("Nenhum GDAL externo encontrado nos locais conhecidos.")
    else:
        lines.append("GDAL externo encontrado em:")
        for d in dirs:
            ecw = "com ECW" if gdal_dir_supports(d, "ECW") else "sem ECW"
            lines.append(f"  {d}  ({ecw})")
    if find_external_gdal("ECW") is None and "ECW" not in drivers:
        lines.append("")
        lines.append(INSTALL_HINT)
    return "\n".join(lines)
