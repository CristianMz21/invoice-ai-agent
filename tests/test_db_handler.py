"""Pruebas unitarias para la capa de persistencia y exportación (db_handler)."""

from __future__ import annotations

from pathlib import Path

import openpyxl
import pytest
from sqlalchemy import Engine, create_engine, inspect, select
from sqlalchemy.orm import Session

import db_handler
import models
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
    """Verifica que inicializar_bd crea tablas 3NF y es idempotente."""
    motor, _ = motor_bd_aislado
    db_handler.inicializar_bd()
    inspector = inspect(motor)
    assert inspector.has_table("empresas")
    assert inspector.has_table("terceros")
    assert inspector.has_table("facturas")
    assert inspector.has_table("items_factura")


def test_insertar_y_consultar_factura(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Inserta una factura con items y valida su persistencia y retorno de PK."""
    _, _ = motor_bd_aislado
    factura = InvoiceModel(
        proveedor="ACME Corp",
        identificacion_fiscal="NIT-999-1",
        fecha="2026-10-30",
        numero_factura="FAC-001",
        moneda="USD",
        subtotal=126.47,
        impuestos=24.03,
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
    assert df_facturas.iloc[0]["identificacion_fiscal"] == "NIT-999-1"
    assert df_facturas.iloc[0]["tenant"] == db_handler.DEFAULT_TENANT_NAME
    assert df_facturas.iloc[0]["tipo_comprobante"] == "RECIBIDA"
    assert df_facturas.iloc[0]["total"] == 150.50
    assert len(df_items) == 1
    assert df_items.iloc[0]["descripcion"] == "Servicio Cloud"


def test_get_or_create_tercero_reutiliza_existente(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Valida que facturas sucesivas del mismo proveedor reutilicen la misma entidad Tercero."""
    motor, _ = motor_bd_aislado
    factura1 = InvoiceModel(
        proveedor="Distribuidora Central S.A.",
        identificacion_fiscal="NIT 800.555.444-1",
        total=100.0,
    )
    factura2 = InvoiceModel(
        proveedor="Distribuidora Central S.A.",
        identificacion_fiscal="NIT 800.555.444-1",
        total=250.0,
    )

    pk1 = db_handler.insertar_factura(factura1, archivo_origen="f1.pdf", metodo="ocr+llm")
    pk2 = db_handler.insertar_factura(factura2, archivo_origen="f2.pdf", metodo="ocr+llm")
    assert pk1 != pk2

    with Session(motor) as sesion:
        terceros = (
            sesion.execute(
                select(models.Tercero).where(models.Tercero.nombre == "Distribuidora Central S.A.")
            )
            .scalars()
            .all()
        )
        assert len(terceros) == 1
        assert terceros[0].identificacion_fiscal == "NIT 800.555.444-1"

        facturas = sesion.execute(select(models.Factura)).scalars().all()
        assert len(facturas) == 2
        assert facturas[0].tercero_id == terceros[0].id
        assert facturas[1].tercero_id == terceros[0].id


def test_modelo_dual_emitida_y_recibida(motor_bd_aislado: tuple[Engine, Path]) -> None:
    """Valida el soporte de cuentas por cobrar (EMITIDA) y cuentas por pagar (RECIBIDA)."""
    motor, _ = motor_bd_aislado
    factura_ar = InvoiceModel(
        proveedor="Cliente Corporativo Global",
        total=5000.0,
    )
    pk_emitida = db_handler.insertar_factura(
        factura_ar,
        archivo_origen="factura_emitida.pdf",
        metodo="erp",
        tipo_comprobante=models.TipoComprobante.EMITIDA,
    )

    with Session(motor) as sesion:
        factura = sesion.get(models.Factura, pk_emitida)
        assert factura is not None
        assert factura.tipo_comprobante == models.TipoComprobante.EMITIDA
        assert factura.estado == models.EstadoFactura.PENDIENTE
        assert factura.tercero.tipo == models.TipoTercero.CLIENTE


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


def test_migrar_base_de_datos(
    motor_bd_aislado: tuple[Engine, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valida que migrar_base_de_datos inicialice las tablas 3NF y cree el tenant demo."""
    import init_db

    motor, _ = motor_bd_aislado
    monkeypatch.setattr(init_db, "_motor", motor)

    init_db.migrar_base_de_datos()

    with Session(motor) as sesion:
        empresas = sesion.execute(select(models.Empresa)).scalars().all()
        assert len(empresas) == 1
        assert empresas[0].nit == "900.123.456-7"


def test_calcular_subtotal_e_impuestos_casos_borde() -> None:
    """Valida los cálculos derivados de subtotal e impuestos."""
    # Caso 1: subtotal e impuestos ya provistos
    f1 = InvoiceModel(proveedor="A", total=100.0, subtotal=80.0, impuestos=20.0)
    sub, imp = db_handler._calcular_subtotal_e_impuestos(f1)
    assert sub == 80.0
    assert imp == 20.0

    # Caso 2: subtotal ausente, pero total e impuestos presentes
    f2 = InvoiceModel(proveedor="B", total=100.0, impuestos=15.0)
    sub, imp = db_handler._calcular_subtotal_e_impuestos(f2)
    assert sub == 85.0
    assert imp == 15.0

    # Caso 3: ambos ausentes, solo total
    f3 = InvoiceModel(proveedor="C", total=50.0)
    sub, imp = db_handler._calcular_subtotal_e_impuestos(f3)
    assert sub == 50.0
    assert imp == 0.0

