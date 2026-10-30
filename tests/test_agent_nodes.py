"""Pruebas unitarias para los nodos individuales y compilación del grafo (agent.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent
from models import InvoiceModel
from ocr_engine import ResultadoOCR


def test_nodo_ingesta_exito(tmp_path: Path) -> None:
    """Verifica que el nodo de ingesta procese correctamente una ruta válida."""
    mock_motor = MagicMock()
    img_path = tmp_path / "factura.png"
    mock_motor.preparar_entradas.return_value = [img_path]

    estado: agent.EstadoAgente = {"archivo_entrada": str(img_path)}
    resultado = agent._nodo_ingesta(estado, motor=mock_motor)

    assert resultado["error"] is None
    assert resultado["rutas_imagenes"] == [img_path]
    assert resultado["imagen_fallback"] == str(img_path)


def test_nodo_ingesta_error() -> None:
    """Verifica que el nodo de ingesta capture excepciones y retorne error."""
    mock_motor = MagicMock()
    mock_motor.preparar_entradas.side_effect = ValueError("Archivo corrupto")

    estado: agent.EstadoAgente = {"archivo_entrada": "invalido.xyz"}
    resultado = agent._nodo_ingesta(estado, motor=mock_motor)

    assert resultado["factura"] is None
    assert "ingesta: Archivo corrupto" in resultado["error"]


def test_nodo_ocr_vacio_y_con_texto(tmp_path: Path) -> None:
    """Evalúa agregación de texto y cálculo de métricas en el nodo OCR."""
    mock_motor = MagicMock()
    # Sin imágenes
    assert agent._nodo_ocr({}, motor=mock_motor)["texto_ocr"] == ""

    # Con imágenes
    img1 = tmp_path / "p1.png"
    mock_motor.extraer_texto.return_value = ResultadoOCR(
        texto="FACTURA ACME S.A. DE C.V. FACTURA NUMERO 001 TOTAL $120.00 CON DETALLE",
        confianza_media=0.92,
        lineas_detectadas=2,
    )
    res = agent._nodo_ocr({"rutas_imagenes": [img1]}, motor=mock_motor)
    assert "FACTURA ACME" in res["texto_ocr"]
    assert res["confianza_ocr"] == 0.92
    assert res["ocr_util"] is True


def test_nodo_estructurar_exito() -> None:
    """Valida que el parser de structured output genere el InvoiceModel esperado."""
    mock_llm = MagicMock()
    mock_parser = MagicMock()
    factura_esperada = InvoiceModel(proveedor="Empresa Mock", total=100.0)
    mock_parser.invoke.return_value = factura_esperada
    mock_llm.with_structured_output.return_value = mock_parser

    estado: agent.EstadoAgente = {"texto_ocr": "Factura Empresa Mock Total 100.0"}
    res = agent._nodo_estructurar(estado, llm=mock_llm)

    assert res["error"] is None
    assert res["factura"] == factura_esperada
    assert res["metodo_extraccion"] == agent.METODO_OCR_LLM


def test_nodo_estructurar_fallo() -> None:
    """Simula una excepción en el LLM para forzar la ruta de fallback."""
    mock_llm = MagicMock()
    mock_parser = MagicMock()
    mock_parser.invoke.side_effect = RuntimeError("API Rate Limit")
    mock_llm.with_structured_output.return_value = mock_parser

    res = agent._nodo_estructurar({"texto_ocr": "algo"}, llm=mock_llm)
    assert res["factura"] is None
    assert "API Rate Limit" in res["error"]


def test_nodo_vision_fallback_sin_imagen() -> None:
    """Valida que el nodo vision_fallback reporte error si no hay imagen física."""
    mock_llm = MagicMock()
    res = agent._nodo_vision_fallback({}, llm=mock_llm)
    assert res["factura"] is None
    assert "no hay imagen disponible" in res["error"]


def test_nodo_vision_fallback_exito(tmp_path: Path) -> None:
    """Valida que el nodo vision_fallback invoque el modelo multimodal."""
    mock_llm = MagicMock()
    mock_parser = MagicMock()
    factura_esperada = InvoiceModel(proveedor="Vision OCR", total=250.0)
    mock_parser.invoke.return_value = factura_esperada
    mock_llm.with_structured_output.return_value = mock_parser

    img = tmp_path / "factura.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    res = agent._nodo_vision_fallback({"imagen_fallback": str(img)}, llm=mock_llm)
    assert res["error"] is None
    assert res["factura"] == factura_esperada
    assert res["metodo_extraccion"] == agent.METODO_VISION_FALLBACK


def test_nodo_persistir_y_exportar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Valida la ejecución de persistencia y exportación en el grafo."""
    factura = InvoiceModel(proveedor="Persistente", total=10.0)
    mock_insertar = MagicMock(return_value=42)
    mock_exportar = MagicMock(return_value=tmp_path / "reporte.xlsx")

    monkeypatch.setattr(agent, "insertar_factura", mock_insertar)
    monkeypatch.setattr(agent, "exportar_excel", mock_exportar)

    res_persistir = agent._nodo_persistir({"factura": factura, "archivo_entrada": "doc.pdf"})
    assert res_persistir["registro_id"] == 42
    mock_insertar.assert_called_once()

    res_exportar = agent._nodo_exportar({})
    assert res_exportar == {}
    mock_exportar.assert_called_once()


def test_construir_grafo_compilacion() -> None:
    """Valida que construir_grafo compile el StateGraph sin errores de tipado."""
    mock_motor = MagicMock()
    mock_llm = MagicMock()
    grafo = agent.construir_grafo(motor_ocr=mock_motor, llm=mock_llm)
    assert grafo is not None
    assert hasattr(grafo, "invoke")
