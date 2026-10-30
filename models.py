"""Esquemas Pydantic y configuración de persistencia (SQLAlchemy + SQLite).

Este módulo es el **contrato de datos** del proyecto:

- `InvoiceItem` / `InvoiceModel`: estructura canónica que el LLM debe producir,
  tanto en el flujo principal (texto OCR → Gemini) como en el fallback
  (imagen → Gemini Vision), vía `with_structured_output`.
- `FacturaRecord` / `ItemFacturaRecord`: mapeo ORM (SQLAlchemy 2.0) a SQLite.

Los validadores son tolerantes con el "ruido" típico del OCR: importes con
separadores de miles europeos/estadounidenses, símbolos de moneda, fechas en
formatos varios y valores nulos tipo "N/A".
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

logger = logging.getLogger(__name__)

# ── Enums de Dominio y Negocio (Modelo Dual AP/AR y Multi-Tenancy) ───────────


class TipoTercero(StrEnum):
    """Clasificación del tercero contable en el modelo dual."""

    CLIENTE = "CLIENTE"
    PROVEEDOR = "PROVEEDOR"


class TipoComprobante(StrEnum):
    """Tipo de factura en cuentas por cobrar / pagar."""

    EMITIDA = "EMITIDA"
    RECIBIDA = "RECIBIDA"


class EstadoFactura(StrEnum):
    """Estado del ciclo de vida contable de la factura."""

    PENDIENTE = "PENDIENTE"
    PAGADA = "PAGADA"


# ── Constantes ────────────────────────────────────────────────────────────────

METODO_OCR_LLM: Final[str] = "ocr+llm"  # flujo principal: RapidOCR → Gemini (texto)
METODO_VISION_FALLBACK: Final[str] = "vision-fallback"  # contingencia: imagen → Gemini Vision

_VALORES_NULOS: Final[frozenset[str]] = frozenset(
    {"", "n/a", "na", "null", "none", "sin dato", "s/d", "-", "--"}
)

_FORMATOS_FECHA: Final[tuple[str, ...]] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%y",
    "%d-%m-%y",
)

# ── Normalizadores (públicos para poder testearlos) ──────────────────────────


def _normalizar_separadores_decimales(texto: str) -> str:
    """Normaliza comas y puntos a representación decimal canónica ('1234.56')."""
    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):
            return texto.replace(".", "").replace(",", ".")
        return texto.replace(",", "")
    if "," in texto:
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", texto):
            return texto.replace(",", "")
        return texto.replace(",", ".")
    if "." in texto and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
        return texto.replace(".", "")
    return texto


def a_flotante(valor: Any) -> float | None:
    """Convierte los formatos numéricos frecuentes en OCR a `float`.

    Soporta: "1.234,56" (ES), "1,234.56" (US), "1.56", "1,56", "$ 1.234,56",
    "12.50 EUR", "N/A" → None, etc. La heurística asume que el separador
    decimal es el que aparece **más a la derecha**.
    """
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()
    if texto.lower() in _VALORES_NULOS:
        return None
    # Quita todo lo que no sea dígito, coma, punto o signo (moneda, letras…).
    texto = re.sub(r"[^\d,.\-]", "", texto)
    if not texto or texto in {"-", ".-"}:
        return None

    texto = _normalizar_separadores_decimales(texto)
    try:
        return float(texto)
    except ValueError:
        logger.debug("No pude convertir %r a float", valor)
        return None


def fecha_a_iso(valor: Any) -> str | None:
    """Normaliza la fecha a ISO-8601 (YYYY-MM-DD) si reconoce el formato.

    Si no logra normalizarla, devuelve el texto tal cual (el dato no se pierde).
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, date):
        return valor.strftime("%Y-%m-%d")

    texto = str(valor).strip()
    if texto.lower() in _VALORES_NULOS:
        return None
    for formato in _FORMATOS_FECHA:
        try:
            return datetime.strptime(texto, formato).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return texto


# ── Esquemas Pydantic (contrato con el LLM) ──────────────────────────────────


class InvoiceItem(BaseModel):
    """Línea de detalle de una factura (una fila de la tabla de conceptos)."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    descripcion: str = ""
    cantidad: float | None = None
    precio_unitario: float | None = None
    subtotal: float | None = None

    @field_validator("cantidad", "precio_unitario", "subtotal", mode="before")
    @classmethod
    def _coaccionar_numero(cls, valor: Any) -> Any:
        return a_flotante(valor)


class InvoiceModel(BaseModel):
    """Factura estructurada con desglose de Tercero e importes (Pydantic V2).

    Es el objeto canónico al que el LLM está obligado mediante
    `with_structured_output(InvoiceModel)`.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    proveedor: str = Field(description="Razón social o nombre del tercero emisor de la factura")
    identificacion_fiscal: str | None = Field(
        default=None,
        description="NIT, CIF, RFC, NIF o identificación tributaria del tercero emisor",
    )
    fecha: str | None = Field(
        default=None,
        description="Fecha de emisión en formato ISO-8601 (YYYY-MM-DD)",
    )
    numero_factura: str | None = Field(default=None, description="Número o código de la factura")
    moneda: str | None = Field(default=None, description="Código de moneda, p. ej. EUR, USD, COP, ARS")
    subtotal: float | None = Field(
        default=None,
        description="Base imponible o subtotal antes de impuestos",
    )
    impuestos: float | None = Field(
        default=None,
        description="Importe total de impuestos o IVA desglosados",
    )
    total: float | None = Field(default=None, description="Importe total de la factura")
    items: list[InvoiceItem] = Field(default_factory=list, description="Líneas de detalle")

    @field_validator("proveedor", mode="before")
    @classmethod
    def _coaccionar_proveedor(cls, valor: Any) -> str:
        texto = str(valor).strip() if valor is not None else ""
        return texto or "Desconocido"

    @field_validator("fecha", mode="before")
    @classmethod
    def _coaccionar_fecha(cls, valor: Any) -> Any:
        return fecha_a_iso(valor)

    @field_validator("identificacion_fiscal", "numero_factura", "moneda", mode="before")
    @classmethod
    def _coaccionar_texto_opcional(cls, valor: Any) -> str | None:
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    @field_validator("subtotal", "impuestos", "total", mode="before")
    @classmethod
    def _coaccionar_montos(cls, valor: Any) -> Any:
        return a_flotante(valor)

    @field_validator("items", mode="before")
    @classmethod
    def _coaccionar_items(cls, valor: Any) -> Any:
        return valor or []

    @property
    def es_util(self) -> bool:
        """Umbral de calidad para persistir: requiere importe total registrado."""
        return self.total is not None


# ── Persistencia SQLite Normalizada en 3NF (SQLAlchemy 2.0 Declarativo) ──────


class Base(DeclarativeBase):
    """Base declarativa común para todos los modelos ORM."""


class Empresa(Base):
    """Tenant empresarial en arquitectura Multi-Tenancy."""

    __tablename__ = "empresas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    razon_social: Mapped[str] = mapped_column(String(256), nullable=False)
    nit: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    terceros: Mapped[list[Tercero]] = relationship(
        back_populates="empresa",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    facturas: Mapped[list[Factura]] = relationship(
        back_populates="empresa",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Tercero(Base):
    """Tercero contable (Cliente o Proveedor) asociado a un Tenant específico."""

    __tablename__ = "terceros"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    tipo: Mapped[str] = mapped_column(String(32), default=TipoTercero.PROVEEDOR.value, nullable=False)
    nombre: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    identificacion_fiscal: Mapped[str | None] = mapped_column(String(64), index=True)

    empresa: Mapped[Empresa] = relationship(back_populates="terceros")
    facturas: Mapped[list[Factura]] = relationship(
        back_populates="tercero",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class Factura(Base):
    """Cabecera de Factura en 3NF con soporte Multi-Tenancy y modelo dual AP/AR."""

    __tablename__ = "facturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    empresa_id: Mapped[int] = mapped_column(ForeignKey("empresas.id"), nullable=False, index=True)
    tercero_id: Mapped[int] = mapped_column(ForeignKey("terceros.id"), nullable=False, index=True)
    tipo_comprobante: Mapped[str] = mapped_column(
        String(32), default=TipoComprobante.RECIBIDA.value, nullable=False
    )
    fecha: Mapped[str | None] = mapped_column(String(32))
    numero_factura: Mapped[str | None] = mapped_column(String(64))
    moneda: Mapped[str | None] = mapped_column(String(8))
    subtotal: Mapped[float | None] = mapped_column(Float)
    impuestos: Mapped[float | None] = mapped_column(Float)
    total: Mapped[float | None] = mapped_column(Float)
    estado: Mapped[str] = mapped_column(String(32), default=EstadoFactura.PENDIENTE.value, nullable=False)

    # Metadatos del pipeline de procesamiento
    archivo_origen: Mapped[str] = mapped_column(String(512), default="", nullable=False)
    metodo_extraccion: Mapped[str] = mapped_column(String(32), default=METODO_OCR_LLM, nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    empresa: Mapped[Empresa] = relationship(back_populates="facturas", lazy="selectin")
    tercero: Mapped[Tercero] = relationship(back_populates="facturas", lazy="selectin")
    items: Mapped[list[ItemFactura]] = relationship(
        back_populates="factura",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def proveedor(self) -> str:
        """Propiedad retrocompatible para compatibilidad con código existente."""
        return self.tercero.nombre if self.tercero else ""

    @property
    def fecha_emision(self) -> str | None:
        """Propiedad retrocompatible con el campo original fecha_emision."""
        return self.fecha


class ItemFactura(Base):
    """Detalle de factura (línea de concepto o artículo)."""

    __tablename__ = "items_factura"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id"), nullable=False, index=True)

    descripcion: Mapped[str] = mapped_column(String(512), default="")
    cantidad: Mapped[float | None] = mapped_column(Float)
    precio_unitario: Mapped[float | None] = mapped_column(Float)
    subtotal: Mapped[float | None] = mapped_column(Float)

    factura: Mapped[Factura] = relationship(back_populates="items")


# Aliases para retrocompatibilidad
FacturaRecord = Factura
ItemFacturaRecord = ItemFactura
