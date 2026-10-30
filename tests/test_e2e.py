"""Test de integración End-to-End: orquesta el grafo completo SIN red real.

Garantía: valida la transición de TODOS los nodos (ingesta → OCR →
estructurar → persistir → exportar, incluidos los desvíos del fallback)
sin consumir saldo de API:

- **RapidOCR** se parchea con `unittest.mock.patch` (responde texto simulado).
- **Gemini** se sustituye por un LLM mock cuyo `with_structured_output`
  devuelve respuestas canned; se inyecta parcheando `agent.construir_llm`.

El aislamiento de artefactos (SQLite + Excel) se logra parcheando el engine
y la ruta de exportación de `db_handler` hacia `tmp_path`; los archivos
reales del proyecto quedan intactos y cada test arranca con BD limpia.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import db_handler
from agent import construir_grafo
from models import (
    METODO_OCR_LLM,
    METODO_VISION_FALLBACK,
    FacturaRecord,
    InvoiceItem,
    InvoiceModel,
)

# ── Fábricas de datos simulados ──────────────────────────────────────────────


def _factura_estructurada() -> InvoiceModel:
    """La factura que el "LLM" devolverá en los tests felices."""
    return InvoiceModel(
        proveedor="TECNOLOGIA ANDINA S.A.",
        fecha="2026-03-12",
        numero_factura="F-2026-0042",
        moneda="ARS",
        total=8059.21,
        items=[
            InvoiceItem(
                descripcion="Licencia software anual",
                cantidad=2.0,
                precio_unitario=1250.0,
                subtotal=2500.0,
            )
        ],
    )


def _lineas_ocr() -> list[list[Any]]:
    """Filas estilo RapidOCR: [caja, texto, confianza]."""
    return [
        [None, "TECNOLOGIA ANDINA S.A.", 0.98],
        [None, "TOTAL $ 8.059,21", 0.99],
        [None, "Fecha de emision: 12/03/2026", 0.97],
    ]


def _llm_mock(respuestas: list[InvoiceModel | None]) -> MagicMock:
    """LLM falso: su parser devuelve las respuestas en orden."""
    parser = MagicMock()
    parser.invoke.side_effect = respuestas
    llm = MagicMock()
    llm.with_structured_output.return_value = parser
    return llm


def _clase_rapidocr_mock() -> MagicMock:
    """Patchea `ocr_engine.RapidOCR`: instancia que responde (lineas, None)."""
    clase = MagicMock()
    clase.return_value.return_value = (_lineas_ocr(), None)
    return clase


@pytest.fixture()
def e2e(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """Aísla BD y Excel en tmp_path y crea una imagen de entrada falsa."""
    motor = create_engine(f"sqlite:///{tmp_path / 'bd_test.sqlite'}", future=True)
    monkeypatch.setattr(db_handler, "_motor", motor)
    monkeypatch.setattr(db_handler, "RUTA_EXCEL", tmp_path / "reporte_test.xlsx")
    db_handler.inicializar_bd()

    imagen = tmp_path / "factura_e2e.png"
    imagen.write_bytes(b"\x89PNG\r\ncontenido-falso")  # el OCR real no la lee
    return imagen


def _invocar(entrada) -> dict:  # type: ignore[no-untyped-def]
    grafo = construir_grafo()
    return grafo.invoke({"archivo_entrada": str(entrada)})  # type: ignore[no-any-return]


def _conteo_facturas() -> int:
    with Session(db_handler._motor) as sesion:
        return len(sesion.execute(select(FacturaRecord)).scalars().all())  # type: ignore[no-any-return]


# ── Tests ────────────────────────────────────────────────────────────────────


def test_e2e_flujo_principal_ingesta_ocr_llm_bd_excel(e2e):  # type: ignore[no-untyped-def]
    """Camino feliz: los 5 nodos transicionan y se persiste + exporta."""
    factura = _factura_estructurada()
    with (
        patch("ocr_engine.RapidOCR", return_value=_clase_rapidocr_mock().return_value),
        patch("agent.construir_llm", return_value=_llm_mock([factura])),
    ):
        estado = _invocar(e2e)

    assert estado["error"] is None
    assert estado["factura"] == factura
    assert estado["metodo_extraccion"] == METODO_OCR_LLM
    assert "TOTAL" in estado["texto_ocr"]
    assert estado["registro_id"] == 1  # BD limpia → primer insert

    assert _conteo_facturas() == 1
    with Session(db_handler._motor) as sesion:
        registro = sesion.execute(select(FacturaRecord)).scalars().one()
        assert registro.total == pytest.approx(8059.21)
        assert registro.proveedor == "TECNOLOGIA ANDINA S.A."
        assert len(registro.items) == 1
        assert registro.items[0].descripcion == "Licencia software anual"

    assert db_handler.RUTA_EXCEL.exists()
    df = pd.read_excel(db_handler.RUTA_EXCEL, sheet_name="Facturas")
    assert len(df) == 1
    assert df.loc[0, "total"] == pytest.approx(8059.21)


def test_e2e_fallback_vision_cuando_texto_falla(e2e):  # type: ignore[no-untyped-def]
    """OCR va bien pero el parseo por texto falla → fallback visión recupera."""
    factura = _factura_estructurada()
    llm = _llm_mock([None, factura])  # 1ª llamada (texto)=None, 2ª (visión)=OK
    with (
        patch("ocr_engine.RapidOCR", return_value=_clase_rapidocr_mock().return_value),
        patch("agent.construir_llm", return_value=llm),
    ):
        estado = _invocar(e2e)

    assert estado["metodo_extraccion"] == METODO_VISION_FALLBACK
    assert estado["factura"] == factura
    assert estado["registro_id"] == 1

    parser = llm.with_structured_output.return_value  # el "parser" compartido
    assert parser.invoke.call_count == 2  # estructurar + vision_fallback
    assert _conteo_facturas() == 1


def test_e2e_error_documentado_sin_insertar_nada(e2e):  # type: ignore[no-untyped-def]
    """Ambos flujos fallan → el grafo termina en error y NO persiste ni exporta."""
    with (
        patch("ocr_engine.RapidOCR", return_value=_clase_rapidocr_mock().return_value),
        patch("agent.construir_llm", return_value=_llm_mock([None, None])),
    ):
        estado = _invocar(e2e)

    assert estado["factura"] is None
    assert "vision_fallback" in (estado["error"] or "")
    assert _conteo_facturas() == 0
    assert not db_handler.RUTA_EXCEL.exists()
