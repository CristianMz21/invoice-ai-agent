"""Tests del agente: heurísticas de evaluación y routers condicionales.

Se testea la lógica de decisión del grafo SIN invocar el LLM ni el OCR
(los nodos reales se ejercitan en la demo con la API key configurada).
"""

from __future__ import annotations

from models import InvoiceModel
from agent import (
    enrutar_ingesta,
    enrutar_post_estructurar,
    enrutar_post_fallback,
    enrutar_post_ocr,
    factura_util,
    texto_util,
)


# ── Heurística de texto útil ─────────────────────────────────────────────────


def test_texto_vacio_no_es_util() -> None:
    assert not texto_util("", 0.0)


def test_texto_corto_o_sin_digitos_no_es_util() -> None:
    assert not texto_util("hola", 0.9)
    assert not texto_util("no hay ningun numero por aqui en el texto del ocr", 0.9)


def test_texto_con_baja_confianza_no_es_util() -> None:
    assert not texto_util("TOTAL 1234,56", 0.2)


def test_texto_ocr_de_factura_si_es_util() -> None:
    texto = "TECNOLOGIA ANDINA S.A. FACTURA F-2026-0042 TOTAL $ 8.059,21 cant 2 x 1.250,00"
    assert texto_util(texto, 0.92)


# ── Umbral de factura utilizable ─────────────────────────────────────────────


def test_factura_sin_total_no_es_util() -> None:
    assert not factura_util(None)
    assert not factura_util(InvoiceModel(proveedor="X", total=None))
    assert factura_util(InvoiceModel(proveedor="X", total=10.0))


# ── Routers (el enrutado condicional del grafo) ─────────────────────────────


def test_router_ingesta_con_imagenes_continua() -> None:
    estado = {"rutas_imagenes": ["x.png"]}
    assert enrutar_ingesta(estado) == "ocr"  # type: ignore[arg-type]


def test_router_ingesta_sin_imagenes_aborta() -> None:
    assert enrutar_ingesta({}) == "__end__"  # type: ignore[arg-type]


def test_router_post_ocr_bifurca_segun_calidad() -> None:
    assert enrutar_post_ocr({"ocr_util": True}) == "estructurar"  # type: ignore[arg-type]
    assert enrutar_post_ocr({"ocr_util": False}) == "vision_fallback"  # type: ignore[arg-type]


def test_router_post_estructurar_va_al_fallback_si_no_hay_total() -> None:
    assert enrutar_post_estructurar({"factura": InvoiceModel(proveedor="X", total=5.0)}) == "persistir"  # type: ignore[arg-type]
    assert enrutar_post_estructurar({"factura": InvoiceModel(proveedor="X")}) == "vision_fallback"  # type: ignore[arg-type]
    assert enrutar_post_estructurar({"factura": None}) == "vision_fallback"  # type: ignore[arg-type]


def test_router_post_fallback_termina_o_persiste() -> None:
    assert enrutar_post_fallback({"factura": InvoiceModel(proveedor="X", total=5.0)}) == "persistir"  # type: ignore[arg-type]
    assert enrutar_post_fallback({"factura": None}) == "__end__"  # type: ignore[arg-type]
