"""Persistencia en SQLite + exportación del reporte Excel.

Un único módulo concentra el "cómo se guardan y se exportan" los datos, de
modo que `agent.py` solo llama a `insertar_factura()` y `exportar_excel()`.

Decisiones de diseño:
- SQLite vía SQLAlchemy 2.0 (ORM declarativo definido en `models.py`).
- El Excel se **regenera desde la BD** en cada exportación: SQLite es la
  fuente de verdad y el archivo siempre refleja su estado completo. Así el
  resultado es idempotente (jamás duplica filas por re-append a ciegas) y al
  mismo tiempo "añade" los registros nuevos en cada corrida.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

import pandas as pd
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models import Base, FacturaRecord, InvoiceModel, ItemFacturaRecord

logger = logging.getLogger(__name__)

# Rutas ancladas a la raíz del proyecto (independientes del cwd de ejecución).
_RAIZ_PROYECTO: Final[Path] = Path(__file__).resolve().parent

RUTA_BD: Final[Path] = _RAIZ_PROYECTO / "facturas.db"
RUTA_EXCEL: Final[Path] = _RAIZ_PROYECTO / "reporte_facturas.xlsx"

HOJA_FACTURAS: Final[str] = "Facturas"
HOJA_ITEMS: Final[str] = "Items"

COLUMNAS_FACTURAS: Final[list[str]] = [
    "id",
    "proveedor",
    "fecha",
    "numero",
    "moneda",
    "total",
    "metodo",
    "archivo",
    "creado_en",
]
COLUMNAS_ITEMS: Final[list[str]] = [
    "factura_id",
    "descripcion",
    "cantidad",
    "precio_unitario",
    "subtotal",
]
_COLUMNAS_MINIMAS: Final[dict[str, list[str]]] = {
    HOJA_FACTURAS: COLUMNAS_FACTURAS,
    HOJA_ITEMS: COLUMNAS_ITEMS,
}

_motor = create_engine(f"sqlite:///{RUTA_BD}", future=True)


def inicializar_bd() -> None:
    """Crea el schema de SQLite si aún no existe (idempotente)."""
    Base.metadata.create_all(_motor)
    logger.info("Base de datos lista en %s", RUTA_BD)


def insertar_factura(factura: InvoiceModel, archivo_origen: str, metodo: str) -> int:
    """Inserta una factura (validada) junto a sus items; devuelve el id (PK)."""
    registro = FacturaRecord(
        archivo_origen=archivo_origen,
        proveedor=factura.proveedor,
        fecha_emision=factura.fecha,
        numero_factura=factura.numero_factura,
        moneda=factura.moneda,
        total=factura.total,
        metodo_extraccion=metodo,
        items=[
            ItemFacturaRecord(
                descripcion=item.descripcion,
                cantidad=item.cantidad,
                precio_unitario=item.precio_unitario,
                subtotal=item.subtotal,
            )
            for item in factura.items
        ],
    )
    with Session(_motor, expire_on_commit=False) as sesion, sesion.begin():
        sesion.add(registro)
        sesion.flush()  # asigna el id en la transacción, aún sin confirmar
        identificador = int(registro.id)
    logger.info(
        "Insertada factura #%d (%s, total=%.2f)", identificador, registro.proveedor, registro.total or 0.0
    )
    return identificador


def exportar_excel(ruta: Path | None = None) -> Path:
    """Regenera el Excel con dos hojas: `Facturas` e `Items`.

    `ruta=None` resuelve `RUTA_EXCEL` en tiempo de llamada (facilita
    aislamiento en tests mediante monkeypatch).
    """
    destino = ruta if ruta is not None else RUTA_EXCEL
    df_facturas, df_items = _dataframes_de_bd()
    with pd.ExcelWriter(destino, engine="openpyxl") as escritor:
        _escribir_hoja(escritor, HOJA_FACTURAS, df_facturas)
        _escribir_hoja(escritor, HOJA_ITEMS, df_items)
    logger.info(
        "Excel actualizado: %s (%d facturas, %d items)",
        destino,
        len(df_facturas),
        len(df_items),
    )
    return destino


# ── Internos ──────────────────────────────────────────────────────────────────


def _dataframes_de_bd() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carga todo el contenido de SQLite como dos DataFrames listos para Excel."""
    with Session(_motor) as sesion:
        registros = sesion.execute(select(FacturaRecord).order_by(FacturaRecord.id)).scalars().all()

    filas_facturas: list[dict[str, Any]] = []
    filas_items: list[dict[str, Any]] = []
    for registro in registros:
        filas_facturas.append(
            {
                "id": registro.id,
                "proveedor": registro.proveedor,
                "fecha": registro.fecha_emision or "",
                "numero": registro.numero_factura or "",
                "moneda": registro.moneda or "",
                "total": registro.total,
                "metodo": registro.metodo_extraccion,
                "archivo": registro.archivo_origen,
                "creado_en": registro.creado_en.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        for item in registro.items:
            filas_items.append(
                {
                    "factura_id": registro.id,
                    "descripcion": item.descripcion,
                    "cantidad": item.cantidad,
                    "precio_unitario": item.precio_unitario,
                    "subtotal": item.subtotal,
                }
            )

    return (
        pd.DataFrame(filas_facturas, columns=COLUMNAS_FACTURAS),
        pd.DataFrame(filas_items, columns=COLUMNAS_ITEMS),
    )


def _escribir_hoja(escritor: pd.ExcelWriter, hoja: str, marco: pd.DataFrame) -> None:
    """Escribe una hoja; si no hay datos aún, deja su encabezado mínimo."""
    df_salida = (
        marco if not marco.empty else pd.DataFrame(columns=_COLUMNAS_MINIMAS[hoja])  # noqa: PD901
    )
    df_salida.to_excel(escritor, sheet_name=hoja, index=False)
