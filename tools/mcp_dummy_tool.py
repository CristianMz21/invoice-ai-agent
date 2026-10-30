"""Simulated MCP (Model Context Protocol) enterprise tools for demonstration.

Demonstrates how external ERP tools and third-party APIs can be exposed to the agent
without static import clutter in the core state machine.
"""

from __future__ import annotations

import logging
from typing import Any

from tools.registry import register_tool

logger = logging.getLogger(__name__)

# Simulated enterprise ERP database (SAP / Oracle NetSuite master record)
_MOCK_ERP_VENDORS: dict[str, dict[str, Any]] = {
    "tecnologia andina": {
        "vendor_id": "VEND-00777",
        "legal_name": "Tecnologia Andina S.A.",
        "tax_id": "ES-A11223344",
        "status": "APPROVED",
        "payment_terms": "NET_30",
        "currency": "EUR",
        "open_purchase_orders": ["PO-2024-901"],
    },
    "acme": {
        "vendor_id": "VEND-00109",
        "legal_name": "ACME Supplies Corp",
        "tax_id": "ES-B12345678",
        "status": "APPROVED",
        "payment_terms": "NET_30",
        "currency": "EUR",
        "open_purchase_orders": ["PO-2024-001", "PO-2024-089"],
    },
    "techcorp": {
        "vendor_id": "VEND-00842",
        "legal_name": "TechCorp Solutions SL",
        "tax_id": "ES-B87654321",
        "status": "APPROVED",
        "payment_terms": "NET_60",
        "currency": "EUR",
        "open_purchase_orders": ["PO-2024-550"],
    },
    "papeleria": {
        "vendor_id": "VEND-00331",
        "legal_name": "Papelería & Suministros Martínez",
        "tax_id": "ES-A99887766",
        "status": "REVIEW_REQUIRED",
        "payment_terms": "DUE_ON_RECEIPT",
        "currency": "EUR",
        "open_purchase_orders": [],
    },
}


@register_tool(
    name="consultar_erp",
    description=(
        "Consulta el sistema ERP empresarial (SAP/NetSuite) para validar si el proveedor está registrado "
        "y activo, su NIF/CIF fiscal, términos de pago y órdenes de compra asociadas."
    ),
    tags=("erp", "mcp", "compliance"),
    source="mcp",
)
def consultar_erp(proveedor: str, numero_factura: str | None = None) -> dict[str, Any]:
    """Query enterprise ERP to verify vendor status, tax compliance, and open purchase orders."""
    logger.info(
        "Executing MCP Tool 'consultar_erp' for proveedor='%s', factura='%s'",
        proveedor,
        numero_factura,
    )

    normalized_key = proveedor.lower().strip()
    match = None
    for key, data in _MOCK_ERP_VENDORS.items():
        if key in normalized_key or normalized_key in key:
            match = data
            break

    if match:
        return {
            "encontrado": True,
            "proveedor_consultado": proveedor,
            "numero_factura": numero_factura,
            "datos_erp": match,
            "mensaje": f"Proveedor '{match['legal_name']}' encontrado y activo en el ERP.",
        }

    return {
        "encontrado": False,
        "proveedor_consultado": proveedor,
        "numero_factura": numero_factura,
        "datos_erp": None,
        "mensaje": (
            f"El proveedor '{proveedor}' no figura en el maestro de proveedores del ERP. "
            "Requiere revisión de compras."
        ),
    }


@register_tool(
    name="consultar_tipo_cambio",
    description=(
        "Obtiene el tipo de cambio oficial del Banco Central para conversión de monedas "
        "(ej. USD a EUR, o EUR a USD) en la fecha de emisión de la factura."
    ),
    tags=("finance", "fx", "mcp"),
    source="mcp",
)
def consultar_tipo_cambio(moneda_origen: str, moneda_destino: str = "EUR") -> dict[str, Any]:
    """Query central bank FX rate for currency normalization."""
    logger.info("Executing MCP Tool 'consultar_tipo_cambio': %s -> %s", moneda_origen, moneda_destino)
    orig = moneda_origen.upper().strip()
    dest = moneda_destino.upper().strip()

    rates = {
        ("USD", "EUR"): 0.92,
        ("EUR", "USD"): 1.09,
        ("GBP", "EUR"): 1.18,
        ("EUR", "GBP"): 0.85,
    }

    if orig == dest:
        return {
            "tasa": 1.0,
            "moneda_origen": orig,
            "moneda_destino": dest,
            "estado": "CONSULTA_EXITOSA",
        }

    tasa = rates.get((orig, dest), 1.0)
    return {
        "tasa": tasa,
        "moneda_origen": orig,
        "moneda_destino": dest,
        "estado": "CONSULTA_EXITOSA",
        "fuente": "Central Bank FX Rate (Simulated MCP)",
    }
