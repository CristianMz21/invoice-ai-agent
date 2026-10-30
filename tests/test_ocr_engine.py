"""Pruebas unitarias para el motor OCR y rasterizador (ocr_engine)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ocr_engine import MotorOCR, ResultadoOCR, es_imagen, es_pdf


def test_es_pdf_y_es_imagen() -> None:
    """Valida la detección correcta de extensiones soportadas."""
    assert es_pdf(Path("doc.pdf"))
    assert es_pdf(Path("DOC.PDF"))
    assert not es_pdf(Path("doc.png"))

    assert es_imagen(Path("foto.png"))
    assert es_imagen(Path("foto.jpg"))
    assert es_imagen(Path("foto.jpeg"))
    assert not es_imagen(Path("doc.txt"))


def test_resultado_ocr_exito() -> None:
    """Valida la propiedad booleana exito según contenido de texto."""
    assert not ResultadoOCR(texto="", confianza_media=0.0, lineas_detectadas=0).exito
    assert not ResultadoOCR(texto="   ", confianza_media=0.9, lineas_detectadas=0).exito
    assert ResultadoOCR(texto="FACTURA F-001", confianza_media=0.85, lineas_detectadas=1).exito


def test_preparar_entradas_imagen(tmp_path: Path) -> None:
    """Verifica que imágenes existentes se devuelvan en lista."""
    motor = MotorOCR()
    img_path = tmp_path / "factura.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    resultado = motor.preparar_entradas(img_path)
    assert resultado == [img_path]


def test_preparar_entradas_errores(tmp_path: Path) -> None:
    """Valida excepciones ante formatos no soportados o archivos inexistentes."""
    motor = MotorOCR()
    invalido = tmp_path / "archivo.docx"
    with pytest.raises(ValueError, match="Formato no soportado"):
        motor.preparar_entradas(invalido)

    inexistente_img = tmp_path / "inexistente.png"
    with pytest.raises(FileNotFoundError):
        motor.preparar_entradas(inexistente_img)

    inexistente_pdf = tmp_path / "inexistente.pdf"
    with pytest.raises(FileNotFoundError):
        motor.preparar_entradas(inexistente_pdf)


def test_extraer_texto_con_mock(tmp_path: Path) -> None:
    """Ejecuta extraer_texto con un mock del motor subyacente de RapidOCR."""
    motor = MotorOCR()
    img_path = tmp_path / "factura.png"
    img_path.write_text("dummy")

    mock_rapid = MagicMock()
    # Simula salida de RapidOCR: [[box, texto, score]]
    mock_rapid.return_value = (
        [
            ([[0, 0], [10, 0], [10, 10], [0, 10]], "EMPRESA S.A.", 0.95),
            ([[0, 20], [10, 20], [10, 30], [0, 30]], "TOTAL: $100.00", 0.85),
        ],
        0.02,
    )
    motor._motor = mock_rapid

    resultado = motor.extraer_texto(img_path)
    assert resultado.exito
    assert "EMPRESA S.A." in resultado.texto
    assert "TOTAL: $100.00" in resultado.texto
    assert resultado.lineas_detectadas == 2
    assert resultado.confianza_media == 0.9
