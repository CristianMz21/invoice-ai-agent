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
from typing import Any, Final, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

logger = logging.getLogger(__name__)

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


def a_flotante(valor: Any) -> Optional[float]:
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
    if not texto or texto in {"-", ".-", ".-"}:
        return None

    if "," in texto and "." in texto:
        if texto.rfind(",") > texto.rfind("."):  # formato ES: 1.234,56
            texto = texto.replace(".", "").replace(",", ".")
        else:  # formato US: 1,234.56
            texto = texto.replace(",", "")
    elif "," in texto:
        # "1,234" → miles (US); "1,23" → decimal.
        if re.fullmatch(r"-?\d{1,3}(,\d{3})+", texto):
            texto = texto.replace(",", "")
        else:
            texto = texto.replace(",", ".")
    elif "." in texto:
        # "1.234" → miles (ES); "1.23" → decimal.
        if re.fullmatch(r"-?\d{1,3}(\.\d{3})+", texto):
            texto = texto.replace(".", "")

    try:
        return float(texto)
    except ValueError:
        logger.debug("No pude convertir %r a float", valor)
        return None


def fecha_a_iso(valor: Any) -> Optional[str]:
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
    cantidad: Optional[float] = None
    precio_unitario: Optional[float] = None
    subtotal: Optional[float] = None

    @field_validator("cantidad", "precio_unitario", "subtotal", mode="before")
    @classmethod
    def _coaccionar_numero(cls, valor: Any) -> Any:
        return a_flotante(valor)


class InvoiceModel(BaseModel):
    """Factura estructurada.

    Es el objeto al que el nodo de estructuración (flujo principal y fallback
    de visión) está **obligado** mediante `with_structured_output(InvoiceModel)`.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    proveedor: str = Field(description="Razón social del emisor de la factura")
    fecha: Optional[str] = Field(
        default=None, description="Fecha de emisión en formato ISO-8601 (YYYY-MM-DD)"
    )
    numero_factura: Optional[str] = Field(default=None, description="Número o código de la factura")
    moneda: Optional[str] = Field(default=None, description="Código de moneda, p. ej. EUR, USD, ARS")
    total: Optional[float] = Field(default=None, description="Importe total de la factura")
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

    @field_validator("numero_factura", "moneda", mode="before")
    @classmethod
    def _coaccionar_texto_opcional(cls, valor: Any) -> Optional[str]:
        if valor is None:
            return None
        texto = str(valor).strip()
        return texto or None

    @field_validator("total", mode="before")
    @classmethod
    def _coaccionar_total(cls, valor: Any) -> Any:
        return a_flotante(valor)

    @field_validator("items", mode="before")
    @classmethod
    def _coaccionar_items(cls, valor: Any) -> Any:
        return valor or []

    @property
    def es_util(self) -> bool:
        """Umbral de calidad para persistir: sin importe total no vale el registro."""
        return self.total is not None


# ── Persistencia SQLite (SQLAlchemy 2.0 declarativo) ─────────────────────────


class Base(DeclarativeBase):
    """Base declarativa común para todos los modelos ORM."""


class FacturaRecord(Base):
    """Fila de la tabla `facturas`: cabecera de la factura procesada."""

    __tablename__ = "facturas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    archivo_origen: Mapped[str] = mapped_column(String(512), nullable=False)
    proveedor: Mapped[str] = mapped_column(String(256), nullable=False)
    fecha_emision: Mapped[Optional[str]] = mapped_column(String(32))
    numero_factura: Mapped[Optional[str]] = mapped_column(String(64))
    moneda: Mapped[Optional[str]] = mapped_column(String(8))
    total: Mapped[Optional[float]] = mapped_column(Float)
    metodo_extraccion: Mapped[str] = mapped_column(String(32), nullable=False)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, nullable=False)

    items: Mapped[list["ItemFacturaRecord"]] = relationship(
        back_populates="factura",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class ItemFacturaRecord(Base):
    """Fila de la tabla `items_factura`: una línea de detalle."""

    __tablename__ = "items_factura"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factura_id: Mapped[int] = mapped_column(ForeignKey("facturas.id"), nullable=False)

    descripcion: Mapped[str] = mapped_column(String(512), default="")
    cantidad: Mapped[Optional[float]] = mapped_column(Float)
    precio_unitario: Mapped[Optional[float]] = mapped_column(Float)
    subtotal: Mapped[Optional[float]] = mapped_column(Float)

    factura: Mapped["FacturaRecord"] = relationship(back_populates="items")
