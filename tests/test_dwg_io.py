"""DWG via ODA File Converter: deteccao, mensagens de erro e round-trip quando disponivel."""

from __future__ import annotations

import pytest

from engecad.core.document import Document
from engecad.io.dwg_io import (
    INSTALL_HINT,
    DwgError,
    diagnose,
    export_document,
    find_oda_converter,
    is_available,
    open_document,
)

E, N = 500000.0, 7400000.0


class _Ctx:
    """Stub minimo: so o que open_document/export_document realmente tocam."""

    def __init__(self, doc):
        self.doc = doc
        self.rasters = []
        self.messages = []

    def message(self, text):
        self.messages.append(text)

    def set_document(self, doc):
        self.doc = doc

    def zoom_extents(self):
        pass

    def view_changed(self):
        pass


def test_env_var_overrides_detection(tmp_path, monkeypatch):
    fake_exe = tmp_path / "ODAFileConverter.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setenv("ENGECAD_ODA_BIN", str(fake_exe))
    assert find_oda_converter() == fake_exe


def test_missing_converter_blocks_open_with_actionable_instructions(tmp_path, monkeypatch):
    """Sem o ODA File Converter, o usuario tem de saber exatamente o que fazer."""
    monkeypatch.setattr("engecad.io.dwg_io.find_oda_converter", lambda: None)
    fake_dwg = tmp_path / "planta.dwg"
    fake_dwg.write_bytes(b"nao e um DWG de verdade")

    with pytest.raises(DwgError) as exc:
        open_document(_Ctx(Document.new("EPSG:31982")), fake_dwg)
    assert INSTALL_HINT in str(exc.value)
    assert "opendesign.com" in str(exc.value)


def test_missing_converter_blocks_export(tmp_path, monkeypatch):
    monkeypatch.setattr("engecad.io.dwg_io.find_oda_converter", lambda: None)
    ctx = _Ctx(Document.new("EPSG:31982"))

    with pytest.raises(DwgError) as exc:
        export_document(ctx, tmp_path / "saida.dwg")
    assert INSTALL_HINT in str(exc.value)


def test_missing_file_is_reported_before_conversion(tmp_path):
    ctx = _Ctx(Document.new("EPSG:31982"))
    with pytest.raises(DwgError, match="nao encontrado"):
        open_document(ctx, tmp_path / "nao_existe.dwg")


def test_diagnose_mentions_install_hint_when_unavailable(monkeypatch):
    monkeypatch.setattr("engecad.io.dwg_io.find_oda_converter", lambda: None)
    text = diagnose()
    assert "NAO encontrado" in text
    assert INSTALL_HINT in text


def test_diagnose_reports_found_path(tmp_path, monkeypatch):
    fake_exe = tmp_path / "ODAFileConverter.exe"
    fake_exe.write_bytes(b"")
    monkeypatch.setattr("engecad.io.dwg_io.find_oda_converter", lambda: fake_exe)
    text = diagnose()
    assert str(fake_exe) in text


@pytest.mark.skipif(not is_available(), reason="ODA File Converter nao instalado nesta maquina")
def test_export_then_reopen_roundtrip(tmp_path):
    """So roda de verdade quando o conversor esta instalado -- exercita a
    cadeia completa: Document -> DWG -> Document."""
    doc = Document.new("EPSG:31982")
    doc.add_line((E, N), (E + 10, N + 10), layer="0")
    ctx = _Ctx(doc)

    dwg_path = export_document(ctx, tmp_path / "roundtrip.dwg")
    assert dwg_path.exists()

    reopened = open_document(_Ctx(Document.new("EPSG:31982")), dwg_path)
    assert len(reopened) == 1
