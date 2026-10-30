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

from models import (
    Base,
    Empresa,
    EstadoFactura,
    Factura,
    InvoiceModel,
    ItemFactura,
    Tercero,
    TipoComprobante,
    TipoTercero,
)

logger = logging.getLogger(__name__)

# Rutas ancladas a la raíz del proyecto (independientes del cwd de ejecución).
_RAIZ_PROYECTO: Final[Path] = Path(__file__).resolve().parent

RUTA_BD: Final[Path] = _RAIZ_PROYECTO / "facturas.db"
RUTA_EXCEL: Final[Path] = _RAIZ_PROYECTO / "reporte_facturas.xlsx"

DEFAULT_TENANT_NIT: Final[str] = "900.123.456-7"
DEFAULT_TENANT_NAME: Final[str] = "Empresa Demo S.A.S."

HOJA_FACTURAS: Final[str] = "Facturas"
HOJA_ITEMS: Final[str] = "Items"

COLUMNAS_FACTURAS: Final[list[str]] = [
    "id",
    "tenant",
    "tenant_nit",
    "proveedor",
    "identificacion_fiscal",
    "tipo_comprobante",
    "estado",
    "fecha",
    "numero",
    "moneda",
    "subtotal",
    "impuestos",
    "total",
    "metodo",
    "archivo",
    "creado_en",
]
COLUMNAS_ITEMS: Final[list[str]] = [
    "factura_id",
    "proveedor",
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
    """Crea el schema relacional 3NF si aún no existe y garantiza el Tenant base."""
    Base.metadata.create_all(_motor)
    with Session(_motor, expire_on_commit=False) as sesion, sesion.begin():
        obtener_o_crear_empresa_demo(sesion)
    logger.info("Base de datos relacional lista en %s con Tenant demo activo", RUTA_BD)


def obtener_o_crear_empresa_demo(
    sesion: Session,
    nit: str = DEFAULT_TENANT_NIT,
    razon_social: str = DEFAULT_TENANT_NAME,
) -> Empresa:
    """Recupera o inserta (Get-or-Create) la Empresa base para soporte Multi-Tenancy."""
    empresa = sesion.execute(select(Empresa).where(Empresa.nit == nit)).scalar_one_or_none()
    if empresa is None:
        empresa = Empresa(nit=nit, razon_social=razon_social)
        sesion.add(empresa)
        sesion.flush()
        logger.info("Tenant base creado: %s (NIT: %s)", razon_social, nit)
    return empresa


def obtener_o_crear_tercero(
    sesion: Session,
    empresa_id: int,
    nombre: str,
    identificacion_fiscal: str | None = None,
    tipo: TipoTercero = TipoTercero.PROVEEDOR,
) -> Tercero:
    """Recupera o inserta (Get-or-Create) un Tercero verificando por identificación o nombre."""
    nombre_limpio = nombre.strip() if nombre else "Proveedor Desconocido"
    id_fiscal = identificacion_fiscal.strip() if identificacion_fiscal else None

    tercero: Tercero | None = None
    if id_fiscal:
        tercero = sesion.execute(
            select(Tercero).where(
                Tercero.empresa_id == empresa_id,
                Tercero.identificacion_fiscal == id_fiscal,
            )
        ).scalar_one_or_none()

    if tercero is None:
        tercero = sesion.execute(
            select(Tercero).where(
                Tercero.empresa_id == empresa_id,
                Tercero.nombre == nombre_limpio,
            )
        ).scalar_one_or_none()

    if tercero is None:
        tercero = Tercero(
            empresa_id=empresa_id,
            tipo=tipo.value,
            nombre=nombre_limpio,
            identificacion_fiscal=id_fiscal,
        )
        sesion.add(tercero)
        sesion.flush()
        logger.info(
            "Tercero registrado: '%s' (NIT: %s, tipo=%s)",
            nombre_limpio,
            id_fiscal or "s/d",
            tipo.value,
        )

    return tercero


def insertar_factura(
    factura: InvoiceModel,
    archivo_origen: str,
    metodo: str,
    tipo_comprobante: TipoComprobante = TipoComprobante.RECIBIDA,
) -> int:
    """Inserta una factura en el esquema relacional normalizado 3NF; devuelve el id (PK)."""
    with Session(_motor, expire_on_commit=False) as sesion, sesion.begin():
        empresa = obtener_o_crear_empresa_demo(sesion)
        tercero = obtener_o_crear_tercero(
            sesion=sesion,
            empresa_id=empresa.id,
            nombre=factura.proveedor,
            identificacion_fiscal=factura.identificacion_fiscal,
            tipo=(
                TipoTercero.PROVEEDOR if tipo_comprobante == TipoComprobante.RECIBIDA else TipoTercero.CLIENTE
            ),
        )

        subtotal = (
            factura.subtotal
            if factura.subtotal is not None
            else (
                factura.total - (factura.impuestos or 0.0)
                if factura.total is not None and factura.impuestos is not None
                else factura.total
            )
        )
        impuestos = (
            factura.impuestos
            if factura.impuestos is not None
            else (
                round(factura.total - subtotal, 2)
                if factura.total is not None and subtotal is not None and factura.total >= subtotal
                else 0.0
            )
        )

        registro = Factura(
            empresa_id=empresa.id,
            tercero_id=tercero.id,
            tipo_comprobante=tipo_comprobante.value,
            fecha=factura.fecha,
            numero_factura=factura.numero_factura,
            moneda=factura.moneda,
            subtotal=subtotal,
            impuestos=impuestos,
            total=factura.total,
            estado=EstadoFactura.PENDIENTE.value,
            archivo_origen=archivo_origen,
            metodo_extraccion=metodo,
            items=[
                ItemFactura(
                    descripcion=item.descripcion,
                    cantidad=item.cantidad,
                    precio_unitario=item.precio_unitario,
                    subtotal=item.subtotal,
                )
                for item in factura.items
            ],
        )
        sesion.add(registro)
        sesion.flush()
        identificador = int(registro.id)

    logger.info(
        "Insertada factura relacional #%d (%s, tercero='%s', total=%.2f)",
        identificador,
        registro.tipo_comprobante,
        tercero.nombre,
        registro.total or 0.0,
    )
    return identificador


def exportar_excel(ruta: Path | None = None) -> Path:
    """Regenera el Excel con dos hojas: `Facturas` e `Items`, enriquecidas mediante JOINs.

    `ruta=None` resuelve `RUTA_EXCEL` en tiempo de llamada (facilita
    aislamiento en tests mediante monkeypatch).
    """
    destino = ruta if ruta is not None else RUTA_EXCEL
    df_facturas, df_items = _dataframes_de_bd()
    with pd.ExcelWriter(destino, engine="openpyxl") as escritor:
        _escribir_hoja(escritor, HOJA_FACTURAS, df_facturas)
        _escribir_hoja(escritor, HOJA_ITEMS, df_items)
    logger.info(
        "Excel relacional actualizado: %s (%d facturas, %d items)",
        destino,
        len(df_facturas),
        len(df_items),
    )
    return destino


# ── Internos ──────────────────────────────────────────────────────────────────


def _dataframes_de_bd() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Carga el contenido de SQLite mediante JOIN entre Factura, Tercero y Empresa."""
    with Session(_motor) as sesion:
        sentencia = (
            select(Factura, Tercero, Empresa)
            .join(Tercero, Factura.tercero_id == Tercero.id)
            .join(Empresa, Factura.empresa_id == Empresa.id)
            .order_by(Factura.id)
        )
        resultados = sesion.execute(sentencia).all()

        filas_facturas: list[dict[str, Any]] = []
        filas_items: list[dict[str, Any]] = []
        for factura, tercero, empresa in resultados:
            filas_facturas.append(
                {
                    "id": factura.id,
                    "tenant": empresa.razon_social,
                    "tenant_nit": empresa.nit,
                    "proveedor": tercero.nombre,
                    "identificacion_fiscal": tercero.identificacion_fiscal or "",
                    "tipo_comprobante": factura.tipo_comprobante,
                    "estado": factura.estado,
                    "fecha": factura.fecha or "",
                    "numero": factura.numero_factura or "",
                    "moneda": factura.moneda or "",
                    "subtotal": factura.subtotal,
                    "impuestos": factura.impuestos,
                    "total": factura.total,
                    "metodo": factura.metodo_extraccion,
                    "archivo": factura.archivo_origen,
                    "creado_en": factura.creado_en.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            for item in factura.items:
                filas_items.append(
                    {
                        "factura_id": factura.id,
                        "proveedor": tercero.nombre,
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
