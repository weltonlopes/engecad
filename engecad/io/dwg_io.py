"""Abrir/exportar DWG via ODA File Converter (conversao 100% local, sem rede).

Por que isso existe
--------------------
DWG e formato proprietario da Autodesk. O EngeCAD nao le/escreve DWG sozinho
-- ninguem faz isso sem licenciar codigo da Autodesk ou da Open Design
Alliance. A saida pratica e gratuita e o ODA File Converter: um executavel
distribuido de graca pela ODA que converte DWG<->DXF localmente, sem precisar
de conta nem internet depois de instalado.

O `ezdxf` (ja usado pelo resto do io/) traz um addon que chama esse
executavel via subprocess (`ezdxf.addons.odafc`) -- e o mesmo padrao de
raster_import.py: a ferramenta externa roda em processo separado, nunca
importada, e cada import/export DWG passa por um DXF temporario.

Fluxo:
  - Abrir .dwg: converte para DXF temporario (odafc.readfile) e monta um
    Document igual a um DXF de terceiro sem sidecar (mantem o CRS corrente
    e avisa) -- exatamente a politica de dxf_io.open_document.
  - Exportar .dwg: serializa o Document corrente para DXF temporario e
    converte para DWG (odafc.export_dwg). Nao mexe no caminho/estado do
    documento nativo (.dxf) -- e so uma via de saida para interoperar com
    AutoCAD e afins.
"""

from __future__ import annotations

import os
from pathlib import Path

import ezdxf
from ezdxf.addons import odafc

from .project import load_sidecar

INSTALL_HINT = (
    "Importar/exportar DWG exige o ODA File Converter instalado (gratuito):\n\n"
    "  1. Baixe em https://www.opendesign.com/guestfiles/oda_file_converter\n"
    "  2. Instale normalmente (fica em C:\\Program Files\\ODA File Converter <versao>)\n"
    "  3. Reabra o EngeCAD (ele procura o executavel automaticamente)\n\n"
    "Alternativa: aponte a variavel de ambiente ENGECAD_ODA_BIN direto para\n"
    "o ODAFileConverter.exe."
)


class DwgError(Exception):
    pass


# ---------------- deteccao ----------------


def find_oda_converter() -> Path | None:
    """Caminho do ODAFileConverter, se instalado nesta maquina."""
    env = os.environ.get("ENGECAD_ODA_BIN")
    if env:
        p = Path(env)
        if p.is_file():
            return p

    if os.name == "nt":
        for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)")):
            if not base.exists():
                continue
            try:
                candidates = sorted(
                    base.glob("ODA File Converter*/ODAFileConverter.exe"), reverse=True
                )
            except OSError:
                candidates = []
            if candidates:
                return candidates[0]
        return None

    from shutil import which

    path = which("ODAFileConverter")
    return Path(path) if path else None


def is_available() -> bool:
    return find_oda_converter() is not None


def _configure_odafc() -> Path:
    """Aponta o addon do ezdxf para o executavel encontrado, ou explica como instalar."""
    exe = find_oda_converter()
    if exe is None:
        raise DwgError(INSTALL_HINT)
    key = "win_exec_path" if os.name == "nt" else "unix_exec_path"
    ezdxf.options.set("odafc-addon", key, str(exe))
    return exe


# ---------------- abrir ----------------


def open_document(ctx, path: str | Path):
    """Converte um .dwg para DXF (em memoria) e o instala como documento corrente.

    Mesma politica de dxf_io.open_document para um DXF de terceiro: sem
    sidecar .emap.json, mantem o CRS atual do projeto e avisa o usuario.
    """
    from ..core.document import Document

    p = Path(path)
    if not p.exists():
        raise DwgError(f"Arquivo nao encontrado: {p}")

    _configure_odafc()
    try:
        drawing = odafc.readfile(p)
    except odafc.ODAFCNotInstalledError as exc:
        raise DwgError(INSTALL_HINT) from exc
    except odafc.ODAFCError as exc:
        raise DwgError(f"Nao foi possivel converter {p.name}: {exc}") from exc
    except ezdxf.DXFStructureError as exc:
        raise DwgError(f"{p.name} nao e um DWG valido: {exc}") from exc

    doc = Document(drawing, path=p)

    for layer in ctx.rasters:
        layer.close()
    ctx.rasters.clear()

    ctx.set_document(doc)
    data = load_sidecar(ctx, p)
    if data is None:
        ctx.message(
            f"{p.name} aberto sem sidecar .emap.json - o CRS ficou como "
            f"{doc.crs.srid}. Confira em Projeto > Sistema de coordenadas."
        )
    for layer in ctx.rasters:
        layer.set_project_crs(doc.crs)

    doc.mark_saved()
    if data is None or "view" not in (data or {}):
        ctx.zoom_extents()
    else:
        ctx.view_changed()
    return doc


# ---------------- exportar ----------------


def export_document(ctx, path: str | Path, version: str | None = None) -> Path:
    """Exporta o documento corrente como .dwg, sem alterar o .dxf nativo.

    version: versao de saida do DWG (ex.: "R2018", "ACAD2013"). Default:
    mesma versao do DXF do documento.
    """
    _configure_odafc()
    target = Path(path)
    if target.suffix.lower() != ".dwg":
        target = target.with_suffix(".dwg")

    try:
        odafc.export_dwg(ctx.doc.drawing, target, version=version, replace=True)
    except odafc.ODAFCNotInstalledError as exc:
        raise DwgError(INSTALL_HINT) from exc
    except odafc.ODAFCError as exc:
        raise DwgError(f"Falha ao exportar {target.name}: {exc}") from exc
    except OSError as exc:
        raise DwgError(f"Falha ao exportar {target.name}: {exc}") from exc
    return target


# ---------------- diagnostico ----------------


def diagnose() -> str:
    """Texto de diagnostico para o menu Ajuda -- responde 'por que DWG nao abre'."""
    exe = find_oda_converter()
    lines = ["Suporte a DWG no EngeCAD", "=" * 34, ""]
    if exe is None:
        lines.append("ODA File Converter: NAO encontrado")
        lines.append("")
        lines.append(INSTALL_HINT)
    else:
        lines.append(f"ODA File Converter: encontrado em\n  {exe}")
    return "\n".join(lines)
