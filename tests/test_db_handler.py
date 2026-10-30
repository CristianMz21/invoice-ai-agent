"""Pruebas unitarias para la capa de persistencia y exportación (db_handler)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import Engine, create_engine, inspect

import db_handler
from models import InvoiceItem, InvoiceModel


@pytest.fixture
def motor_bd_aislado(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Engine, Path]:
    """Crea una base de datos SQLite aislada para cada test."""
    ruta_bd = tmp_path / "test_facturas.db"
    ruta_excel = tmp_path / "test_reporte.xlsx"
    motor = create_engine(f"sqlite:///{ruta_bd}", future=True)

    monkeypatch.setattr(db_handler, "_motor", motor)
    monkeypatch.setattr(db_handler, "RUTA_BD", ruta_bd)
    monkeypatch.setattr(db_handler, "RUTA_EXCEL", ruta_excel)

    db_handler.inicializar_bd()
    return motor, ruta_excel


def test_inicializar_bd_idempotente(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Verifica que inicializar_bd crea tablas y es idempotente."""
    motor, _ = motor_bd_aislado
    db_handler.inicializar_bd()
    inspector = inspect(motor)
    assert inspector.has_table("facturas")
    assert inspector.has_table("items_factura")


def test_insertar_y_consultar_factura(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Inserta una factura con items y valida su persistencia y retorno de PK."""
    _, _ = motor_bd_aislado
    factura = InvoiceModel(
        proveedor="ACME Corp",
        fecha="2026-10-30",
        numero_factura="FAC-001",
        moneda="USD",
        total=150.50,
        items=[
            InvoiceItem(descripcion="Servicio Cloud", cantidad=1.0, precio_unitario=150.50, subtotal=150.50)
        ],
    )

    pk = db_handler.insertar_factura(factura, archivo_origen="test.pdf", metodo="ocr+llm")
    assert pk >= 1

    df_facturas, df_items = db_handler._dataframes_de_bd()
    assert len(df_facturas) == 1
    assert df_facturas.iloc[0]["proveedor"] == "ACME Corp"
    assert df_facturas.iloc[0]["total"] == 150.50
    assert len(df_items) == 1
    assert df_items.iloc[0]["descripcion"] == "Servicio Cloud"


def test_exportar_excel(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Exporta reporte a Excel y verifica la existencia de hojas y columnas."""
    _, ruta_excel = motor_bd_aislado
    factura = InvoiceModel(
        proveedor="Beta S.A.",
        total=50.0,
        items=[InvoiceItem(descripcion="Consultoría", total=50.0)],
    )
    db_handler.insertar_factura(factura, archivo_origen="factura.png", metodo="vision")

    salida = db_handler.exportar_excel()
    assert salida.exists()

    wb = openpyxl.load_workbook(salida)
    assert db_handler.HOJA_FACTURAS in wb.sheetnames
    assert db_handler.HOJA_ITEMS in wb.sheetnames


def test_exportar_excel_sin_datos(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Valida que un export sin datos genere un Excel válido con encabezados."""
    _, ruta_excel = motor_bd_aislado
    salida = db_handler.exportar_excel(ruta_excel)
    assert salida.exists()

    wb = openpyxl.load_workbook(salida)
    assert wb[db_handler.HOJA_FACTURAS].max_row >= 1
    assert wb[db_handler.HOJA_ITEMS].max_row >= 1
