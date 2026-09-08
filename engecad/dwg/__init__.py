"""Leitor nativo do formato binário DWG.

O pacote não depende de conversores DXF/ODA.  A camada pública expõe o
contêiner DWG e os objetos geométricos decodificados; a adaptação para o
modelo interno do EngeCAD fica em :mod:`engecad.io.dwg_io`.
"""

from .objects import ObjectIndex, ObjectRecord, ObjectRef, build_object_index
from .reader import DwgDocument, DwgEntity, DwgError, DwgReader, read

__all__ = [
    "DwgDocument",
    "DwgEntity",
    "DwgError",
    "DwgReader",
    "ObjectIndex",
    "ObjectRecord",
    "ObjectRef",
    "build_object_index",
    "read",
]
