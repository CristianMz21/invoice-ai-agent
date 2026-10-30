"""Tests del contrato de datos: normalizadores y modelo Pydantic."""

from __future__ import annotations

import pytest

from models import InvoiceItem, InvoiceModel, a_flotante, fecha_a_iso


# ── a_flotante ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("1.234,56", 1234.56),  # formato español con miles
        ("$ 1.234,56 EUR", 1234.56),  # con símbolos/letras alrededor
        ("1,234.56", 1234.56),  # formato US
        ("1,56", 1.56),  # coma decimal simple
        ("1.56", 1.56),
        ("1.234", 1234.0),  # punto de miles (ES)
        ("1,234", 1234.0),  # coma de miles (US)
        (2500, 2500.0),  # ya numérico
        ("N/A", None),
        ("", None),
        ("--", None),
        ("importe ilegible", None),
    ],
)
def test_a_flotante(entrada: object, esperado: float | None) -> None:
    assert a_flotante(entrada) == esperado


# ── fecha_a_iso ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("2026-03-12", "2026-03-12"),
        ("12/03/2026", "2026-03-12"),
        ("12-03-26", "2026-03-12"),
        ("12.03.2026", "2026-03-12"),
        ("N/A", None),
        ("ayer por la tarde", "ayer por la tarde"),  # no normalizable → tal cual
    ],
)
def test_fecha_a_iso(entrada: str, esperado: str | None) -> None:
    assert fecha_a_iso(entrada) == esperado


def test_fecha_a_iso_nulo() -> None:
    assert fecha_a_iso(None) is None


# ── InvoiceModel (lo que produce el LLM) ─────────────────────────────────────


def test_modelo_acepta_salida_tolerante_del_llm() -> None:
    """El LLM devuelve strings sucios; el validador los Normaliza."""
    factura = InvoiceModel.model_validate(
        {
            "proveedor": "  TECNOLOGIA ANDINA S.A.  ",
            "fecha": "12/03/2026",
            "numero_factura": "F-2026-0042",
            "moneda": "ARS",
            "total": "$ 8.059,21",
            "items": [
                {"descripcion": "Licencia software anual", "cantidad": "2", "subtotal": "2.500,00"},
                {"descripcion": r"N/A", "cantidad": None},
            ],
        }
    )
    assert factura.proveedor == "TECNOLOGIA ANDINA S.A."
    assert factura.fecha == "2026-03-12"
    assert factura.total == 8059.21
    assert isinstance(factura.items[0], InvoiceItem)
    assert factura.items[0].cantidad == 2.0
    assert factura.items[0].subtotal == 2500.0
    assert factura.items[1].descripcion == "N/A"  # la descripción no se inventa, queda tal cual
    assert factura.es_util


def test_modelo_rechaza_proveedor_ausente() -> None:
    with pytest.raises(ValueError, match="proveedor"):
        InvoiceModel.model_validate({"total": "1.000,00"})


def test_modelo_sin_total_no_es_util() -> None:
    factura = InvoiceModel.model_validate({"proveedor": "X", "total": None})
    assert not factura.es_util


def test_modelo_campo_extra_ignorado() -> None:
    """El LLM a veces regala claves de más; deben ignorarse, no romper."""
    factura = InvoiceModel.model_validate({"proveedor": "X", "campo_imaginario": "y"})
    assert factura.proveedor == "X"
