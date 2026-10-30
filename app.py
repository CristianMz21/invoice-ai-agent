"""Interfaz web Enterprise (Streamlit) para el Agente Autónomo de Facturas.

Diseño Enterprise Staff Engineer:
- Arquitectura Hexagonal y visualización de herramientas dinámicas MCP.
- Vista dual: Previsualización del documento original e inspección de datos estructurados.
- Conciliación contable ERP en tiempo real (SAP / NetSuite master record).
- Trazabilidad agéntica en vivo (LangGraph StateGraph + deltas de ejecución).
- Historial interactivo de persistencia SQLite y exportación Excel.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import pandas as pd
import streamlit as st
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit.runtime.uploaded_file_manager import UploadedFile

from agent import CONFIANZA_MINIMA, EstadoAgente, construir_grafo, construir_llm
from db_handler import RUTA_BD, RUTA_EXCEL, _motor, exportar_excel, inicializar_bd
from logging_config import configurar_logging
from main import _cargar_dotenv
from models import FacturaRecord, InvoiceModel
from ocr_engine import MotorOCR
from tools.mcp_dummy_tool import consultar_erp
from tools.registry import default_registry

# ── Configuración de página y estilos Enterprise ─────────────────────────────

st.set_page_config(
    page_title="Enterprise Invoice Agent · LangGraph & MCP",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

configurar_logging()

DIRECTORIO_DATOS: Final[Path] = Path("data")
RUTA_DEMO: Final[Path] = DIRECTORIO_DATOS / "factura_demo.png"
EXTENSIONES_SUBIDA: Final[tuple[str, ...]] = ("pdf", "png", "jpg", "jpeg")
MAX_TAMANO_MB: Final[int] = 25

_CUSTOM_CSS: Final[str] = """
<style>
    /* Estilos Enterprise para métricas y tarjetas */
    .hero-container {
        padding: 1.2rem 1.5rem;
        background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%);
        border-radius: 12px;
        color: #f8fafc;
        margin-bottom: 1.5rem;
        border: 1px solid #334155;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    .hero-title {
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 0.3rem;
        color: #ffffff;
    }
    .hero-subtitle {
        font-size: 0.95rem;
        color: #94a3b8;
    }
    .badge-pill {
        display: inline-block;
        padding: 0.25rem 0.65rem;
        font-size: 0.75rem;
        font-weight: 600;
        border-radius: 9999px;
        margin-right: 0.4rem;
        margin-top: 0.5rem;
    }
    .badge-blue { background-color: #1e3a8a; color: #93c5fd; border: 1px solid #3b82f6; }
    .badge-purple { background-color: #3b0764; color: #d8b4fe; border: 1px solid #a855f7; }
    .badge-emerald { background-color: #064e3b; color: #6ee7b7; border: 1px solid #10b981; }

    .erp-card {
        padding: 1rem 1.2rem;
        border-radius: 10px;
        background-color: rgba(30, 41, 59, 0.4);
        border-left: 4px solid #10b981;
        margin: 1rem 0;
    }
    .erp-card-warning {
        padding: 1rem 1.2rem;
        border-radius: 10px;
        background-color: rgba(30, 41, 59, 0.4);
        border-left: 4px solid #f59e0b;
        margin: 1rem 0;
    }
</style>
"""
st.markdown(_CUSTOM_CSS, unsafe_allow_html=True)

_TITULOS_NODOS: Final[dict[str, dict[str, str]]] = {
    "ingesta": {
        "label": "📥 Ingestando documento…",
        "detalle": "Validación de formato y rasterizado de páginas a imágenes.",
    },
    "ocr": {
        "label": "👁️ Ejecutando OCR local (RapidOCR / ONNX)…",
        "detalle": "Inferencia de modelos de visión local sobre CPU (cero llamadas de red).",
    },
    "estructurar": {
        "label": "🧠 Razonamiento agéntico y estructuración…",
        "detalle": "LLM con herramientas MCP enlazadas vía bind_tools() y validación Pydantic.",
    },
    "tools": {
        "label": "🛠️ Invocando servidor MCP empresarial…",
        "detalle": "Conciliación con ERP contable (SAP/NetSuite) y cotización de divisas.",
    },
    "vision_fallback": {
        "label": "🚑 Fallback multimodal activado…",
        "detalle": "Inspección visual directa de la imagen mediante Gemini Multimodal.",
    },
    "persistir": {
        "label": "💾 Persistiendo en SQLite…",
        "detalle": "Transacción ACID en facturas.db (encabezado e ítems).",
    },
    "exportar": {
        "label": "📊 Generando reporte consolidado…",
        "detalle": "Exportación idempotente a reporte_facturas.xlsx.",
    },
}

_CAUSAS_FALLO: Final[dict[str, str]] = {
    "ingesta": "El archivo no existe o no es un PDF/imagen soportado.",
    "estructurar": "El LLM no logró estructurar el texto OCR: posible documento vacío o ilegible.",
    "vision_fallback": "El fallback de visión tampoco pudo interpretar la factura.",
    "persistir": "No había una factura validada para insertar.",
}


# ── Infraestructura y Pipelines ──────────────────────────────────────────────


@st.cache_resource(show_spinner="Compilando infraestructura: RapidOCR + LLM + ToolRegistry...")
def _construir_pipeline() -> CompiledStateGraph:
    """Compila el StateGraph inyectando el ToolRegistry y las dependencias desacopladas."""
    motor_ocr = MotorOCR()
    llm = construir_llm()
    inicializar_bd()
    return construir_grafo(motor_ocr=motor_ocr, llm=llm, registry=default_registry)


def _guardar_upload(archivo_subido: UploadedFile) -> Path:
    """Almacena el archivo recibido garantizando unicidad de nombre."""
    DIRECTORIO_DATOS.mkdir(exist_ok=True)
    destino = DIRECTORIO_DATOS / Path(archivo_subido.name).name
    if destino.exists():
        destino = destino.with_name(f"{destino.stem}_{uuid4().hex[:8]}{destino.suffix}")
    destino.write_bytes(archivo_subido.getvalue())
    return destino


def _obtener_excel_bytes() -> bytes:
    """Retorna los bytes del reporte Excel regenerándolo si no existe o leyéndolo."""
    if not RUTA_EXCEL.exists():
        try:
            exportar_excel()
        except Exception:
            return b""
    if RUTA_EXCEL.exists():
        return RUTA_EXCEL.read_bytes()
    return b""


def _generar_csv_facturas() -> bytes:
    """Genera el contenido CSV consolidado de todas las facturas en la base de datos."""
    try:
        with Session(_motor) as sesion:
            registros = sesion.execute(select(FacturaRecord).order_by(FacturaRecord.id)).scalars().all()
        if not registros:
            return b""
        df = pd.DataFrame(
            [
                {
                    "id": r.id,
                    "tenant": r.empresa.razon_social if r.empresa else "",
                    "proveedor": r.proveedor,
                    "identificacion_fiscal": (
                        r.tercero.identificacion_fiscal
                        if r.tercero and r.tercero.identificacion_fiscal
                        else ""
                    ),
                    "tipo_comprobante": r.tipo_comprobante,
                    "estado": r.estado,
                    "fecha": r.fecha or "",
                    "numero_factura": r.numero_factura or "",
                    "moneda": r.moneda or "",
                    "subtotal": r.subtotal,
                    "impuestos": r.impuestos,
                    "total": r.total,
                    "metodo_extraccion": r.metodo_extraccion,
                    "archivo_origen": r.archivo_origen,
                    "creado_en": r.creado_en,
                }
                for r in registros
            ]
        )
        return df.to_csv(index=False).encode("utf-8")
    except Exception:
        return b""


# ── Ejecución del Grafo con Streaming en Vivo ────────────────────────────────


def _ejecutar_con_status(grafo: CompiledStateGraph, ruta: Path) -> EstadoAgente:
    """Ejecuta el grafo LangGraph transmitiendo el estado paso a paso a la UI."""
    estados: dict[str, Any] = {}
    with st.status("Ejecutando Agente Autónomo LangGraph…", expanded=True) as status:
        try:
            for paso in grafo.stream({"archivo_entrada": str(ruta)}, stream_mode="updates"):
                for nodo, delta in paso.items():
                    if not isinstance(delta, dict):
                        continue
                    estados.update(delta)
                    titulos = _TITULOS_NODOS.get(
                        nodo, {"label": f"⚙️ Ejecutando nodo '{nodo}'…", "detalle": ""}
                    )
                    status.update(label=titulos["label"])
                    if titulos["detalle"]:
                        st.caption(f"• {titulos['detalle']}")
                    detalle = _detalle_de_delta(nodo, delta)
                    if detalle:
                        st.markdown(f"&nbsp;&nbsp;↳ {detalle}")
            exito = estados.get("factura") is not None
            label_status = (
                "✅ Pipeline completado exitosamente"
                if exito
                else "❌ El pipeline no pudo completar la factura"
            )
            status.update(label=label_status, state="complete" if exito else "error", expanded=False)
        except Exception as exc:  # noqa: BLE001
            estados.setdefault("error", f"runtime: {exc}")
            status.update(label="Fallo durante la ejecución del grafo", state="error")
            st.error(f"Excepción capturada: `{exc}`")

    return estados  # type: ignore[return-value]


def _detalle_de_delta(nodo: str, delta: dict[str, Any]) -> str | None:
    """Interpreta los eventos de cada nodo para el feed de progreso."""
    if nodo == "ocr" and "confianza_ocr" in delta:
        confianza = float(delta["confianza_ocr"])
        calidad = "Texto legible" if delta.get("ocr_util") else "Texto insuficiente"
        return f"OCR Local completado: Confianza media **{confianza:.1%}** ({calidad})."
    if nodo == "estructurar" and delta.get("factura") is not None:
        return f"Factura estructurada: **{delta['factura'].proveedor}**."
    if nodo == "tools":
        return "Herramienta MCP ejecutada: Conciliación de datos con servicios corporativos."
    if nodo == "persistir" and "registro_id" in delta:
        return f"Registro persistido en SQLite con ID **#{delta['registro_id']}**."
    return None


# ── Renderizado de Resultados y UX Avanzada ──────────────────────────────────


def _mostrar_resultados(estados: EstadoAgente, ruta_archivo: Path | None) -> None:
    """Renderiza la vista principal con diseño split y tabs de auditoría."""
    factura = estados.get("factura")
    registro_id = estados.get("registro_id")

    if factura is None or registro_id is None:
        _mostrar_error(estados)
        return

    # Layout de 2 columnas: Documento a la izquierda, Inteligencia a la derecha
    col_doc, col_data = st.columns([1, 1.2], gap="large")

    with col_doc:
        st.subheader("📄 Documento Original")
        _render_document_preview(ruta_archivo, estados)

    with col_data:
        st.subheader("📊 Extracción & Conciliación MCP")
        tab_resumen, tab_mcp, tab_bd = st.tabs(
            [
                "📋 Datos & Auditoría",
                "🛠️ Trazabilidad Agéntica",
                "💾 Historial BD",
            ]
        )

        with tab_resumen:
            _render_resumen_tab(factura)

        with tab_mcp:
            _render_mcp_trace_tab(estados)

        with tab_bd:
            _render_db_history_tab()


def _render_document_preview(ruta_archivo: Path | None, estados: EstadoAgente) -> None:
    """Muestra la vista previa del archivo procesado e información de ingesta."""
    if ruta_archivo and ruta_archivo.exists():
        if ruta_archivo.suffix.lower() in [".png", ".jpg", ".jpeg", ".webp"]:
            st.image(str(ruta_archivo), use_container_width=True, caption=f"Origen: {ruta_archivo.name}")
        elif ruta_archivo.suffix.lower() == ".pdf":
            rutas_img = estados.get("rutas_imagenes", [])
            if rutas_img and rutas_img[0].exists():
                caption_pdf = f"PDF rasterizado: {ruta_archivo.name} (Pág. 1)"
                st.image(str(rutas_img[0]), use_container_width=True, caption=caption_pdf)
            else:
                st.info(f"📄 Documento PDF: `{ruta_archivo.name}`")
    else:
        st.info("Sin archivo de vista previa disponible.")

    # Tarjeta de salud de lectura
    confianza = estados.get("confianza_ocr", 0.0)
    metodo = estados.get("metodo_extraccion", "—")
    color_confianza = "#4ade80" if confianza >= 0.8 else "#fbbf24"
    card_html = (
        '<div style="background-color: rgba(30, 41, 59, 0.3); padding: 0.8rem; '
        'border-radius: 8px; border: 1px solid #334155; margin-top: 0.8rem;">\n'
        f'    <div style="font-size: 0.85rem; color: #94a3b8;">'
        f'MÉTODO: <strong style="color: #38bdf8;">{metodo}</strong></div>\n'
        f'    <div style="font-size: 0.85rem; color: #94a3b8;">'
        f'CONFIANZA OCR: <strong style="color: {color_confianza};">{confianza:.1%}</strong></div>\n'
        "</div>"
    )
    st.markdown(card_html, unsafe_allow_html=True)


def _render_resumen_tab(factura: InvoiceModel) -> None:
    """Renderiza KPIs, conciliación en ERP vía MCP y tabla de ítems."""
    # Fila de métricas estilizadas
    m1, m2, m3 = st.columns(3)
    total_val = f"{factura.total:,.2f}" if factura.total is not None else "0.00"
    m1.metric("Total Factura", f"${total_val}", help=f"Moneda: {factura.moneda or 'N/D'}")
    m2.metric("Fecha Emisión", factura.fecha or "No detectada")
    m3.metric("Factura Nº", factura.numero_factura or "S/N")

    st.write(f"**Razón Social / Proveedor:** `{factura.proveedor}`")

    # ── Módulo de Conciliación ERP (Model Context Protocol) ──────────────────
    erp_resultado = consultar_erp(proveedor=factura.proveedor, numero_factura=factura.numero_factura)
    if erp_resultado.get("encontrado"):
        erp_data = erp_resultado["datos_erp"]
        pos = ", ".join(erp_data["open_purchase_orders"]) or "Ninguna"
        erp_html = (
            '<div class="erp-card">\n'
            '    <div style="font-weight: 700; color: #10b981; font-size: 0.95rem;">\n'
            "        🛡️ CONCILIADO CON ERP CORPORATIVO (SAP S/4HANA Master Record)\n"
            "    </div>\n"
            '    <div style="font-size: 0.85rem; color: #cbd5e1; margin-top: 0.3rem;">\n'
            f"        • <strong>Razón Social:</strong> {erp_data['legal_name']}<br/>\n"
            f"        • <strong>NIF/CIF:</strong> {erp_data['tax_id']} | "
            f'<strong>Estatus:</strong> <span style="color: #4ade80;">{erp_data["status"]}</span><br/>\n'
            f"        • <strong>Términos:</strong> {erp_data['payment_terms']} | "
            f"<strong>Órdenes Abiertas:</strong> {pos}\n"
            "    </div>\n"
            "</div>"
        )
        st.markdown(erp_html, unsafe_allow_html=True)
    else:
        warning_html = (
            '<div class="erp-card-warning">\n'
            '    <div style="font-weight: 700; color: #f59e0b; font-size: 0.95rem;">\n'
            "        ⚠️ ATENCIÓN: PROVEEDOR NO REGISTRADO EN EL MAESTRO DE ERP\n"
            "    </div>\n"
            '    <div style="font-size: 0.85rem; color: #cbd5e1; margin-top: 0.3rem;">\n'
            f'        El proveedor "{factura.proveedor}" no figura en el ERP. '
            "Requiere validación de Compras.\n"
            "    </div>\n"
            "</div>"
        )
        st.markdown(warning_html, unsafe_allow_html=True)

    # Tabla de items
    st.write("#### Desglose de Líneas de Ítems")
    if factura.items:
        df_items = pd.DataFrame([item.model_dump() for item in factura.items])
        st.dataframe(
            df_items,
            use_container_width=True,
            hide_index=True,
            column_config={
                "descripcion": st.column_config.TextColumn("Descripción"),
                "cantidad": st.column_config.NumberColumn("Cant.", format="%.1f"),
                "precio_unitario": st.column_config.NumberColumn("Precio Unitario", format="$%.2f"),
                "subtotal": st.column_config.NumberColumn("Subtotal", format="$%.2f"),
            },
        )
    else:
        st.info("No se detectaron líneas de detalle individuales.")

    st.write("#### 📥 Centro de Descargas")
    col_dl1, col_dl2 = st.columns(2)
    excel_bytes = _obtener_excel_bytes()
    with col_dl1:
        if excel_bytes:
            st.download_button(
                label="📊 Descargar Reporte Excel (.xlsx)",
                data=excel_bytes,
                file_name="reporte_facturas.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True,
            )
    with col_dl2:
        nombre_json = f"{factura.numero_factura or 'factura_auditada'}.json"
        st.download_button(
            label="🧾 Descargar JSON Estructurado",
            data=factura.model_dump_json(indent=2),
            file_name=nombre_json,
            mime="application/json",
            use_container_width=True,
        )


def _render_mcp_trace_tab(estados: EstadoAgente) -> None:
    """Muestra la trazabilidad interna del agente y el texto de OCR extraído."""
    st.markdown("##### 🧬 Grafo de Estados & Protocolo MCP")
    tools_disponibles = default_registry.list_tools()
    total_tools = len(tools_disponibles)
    st.caption(f"El agente LangGraph tiene acceso a **{total_tools} herramientas dinámicas**:")
    for t in tools_disponibles:
        st.markdown(f"- `[{t.source.upper()}]` **{t.name}**: {t.description}")

    st.divider()
    texto_ocr = estados.get("texto_ocr", "")
    if texto_ocr.strip():
        st.markdown("##### 🔍 Texto Crudo Extraído (RapidOCR)")
        st.caption(
            f"Total de caracteres: {len(texto_ocr)} | Confianza media: {estados.get('confianza_ocr', 0):.1%}"
        )
        st.text_area("OCR Text Stream", texto_ocr, height=220, disabled=True)


def _render_db_history_tab() -> None:
    """Permite auditar los registros almacenados en facturas.db."""
    st.markdown("##### 💾 Últimas Facturas Persistidas en SQLite")
    try:
        with Session(_motor) as sesion:
            registros = (
                sesion.execute(select(FacturaRecord).order_by(FacturaRecord.id.desc()).limit(10))
                .scalars()
                .all()
            )

        if registros:
            datos = [
                {
                    "ID": r.id,
                    "Tenant": r.empresa.razon_social if r.empresa else "Demo",
                    "Proveedor": r.proveedor,
                    "NIT/Identificación": (
                        r.tercero.identificacion_fiscal
                        if r.tercero and r.tercero.identificacion_fiscal
                        else "—"
                    ),
                    "Tipo": r.tipo_comprobante,
                    "Fecha": r.fecha or "—",
                    "Subtotal": f"${r.subtotal:,.2f}" if r.subtotal is not None else "—",
                    "Impuestos": f"${r.impuestos:,.2f}" if r.impuestos is not None else "—",
                    "Total": f"${r.total:,.2f}" if r.total is not None else "—",
                    "Estado": r.estado,
                    "Método": r.metodo_extraccion,
                    "Archivo": Path(r.archivo_origen).name,
                }
                for r in registros
            ]
            st.dataframe(pd.DataFrame(datos), use_container_width=True, hide_index=True)

            st.write("###### Descargas Disponibles")
            c1, c2, c3 = st.columns(3)
            with c1:
                excel_bytes = _obtener_excel_bytes()
                if excel_bytes:
                    st.download_button(
                        label="📊 Descargar Excel",
                        data=excel_bytes,
                        file_name="reporte_facturas.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
            with c2:
                csv_bytes = _generar_csv_facturas()
                if csv_bytes:
                    st.download_button(
                        label="📄 Descargar CSV",
                        data=csv_bytes,
                        file_name="facturas_consolidadas.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
            with c3:
                if RUTA_BD.exists():
                    st.download_button(
                        label="💾 Descargar BD SQLite",
                        data=RUTA_BD.read_bytes(),
                        file_name="facturas.db",
                        mime="application/x-sqlite3",
                        use_container_width=True,
                    )
        else:
            st.info("La base de datos aún no contiene registros.")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"No se pudo consultar el historial: {exc}")


def _mostrar_error(estados: EstadoAgente) -> None:
    """Presenta el error documentado por el pipeline."""
    error = estados.get("error") or "Fallo de procesamiento desconocido."
    causa_key = error.split(":")[0].split()[0]
    causa = _CAUSAS_FALLO.get(causa_key, "El pipeline no pudo obtener datos suficientes.")
    st.error(f"**Error de procesamiento:** {causa}", icon="🚫")
    with st.expander("Ver Traza Técnica del Error"):
        st.code(error, language="text")


# ── Renderizado de la Cabecera & Controles ───────────────────────────────────

_cargar_dotenv()

st.markdown(
    """
    <div class="hero-container">
        <div class="hero-title">🧾 Autonomous Enterprise Invoice Agent</div>
        <div class="hero-subtitle">
            Procesador Autónomo de Documentos Contables con LangGraph, RapidOCR local y Servidores MCP
        </div>
        <div>
            <span class="badge-pill badge-blue">LangGraph v1.2</span>
            <span class="badge-pill badge-purple">Model Context Protocol (MCP)</span>
            <span class="badge-pill badge-emerald">RapidOCR (ONNX Local)</span>
            <span class="badge-pill badge-blue">SQLAlchemy 2.0 & SQLite</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### 🏢 Arquitectura Hexagonal")
    st.caption("Aislamiento de puertos y adaptadores para escalabilidad empresarial:")
    st.markdown(
        """
        - 🧩 **Core:** LangGraph StateGraph (aislado)
        - 👁️ **Adaptador OCR:** RapidOCR (Local ONNX CPU)
        - 🧠 **Adaptador LLM:** Google Gemini 2.5 Flash
        - 🛠️ **Protocolo MCP:** Registry dinámico de herramientas
        - 💾 **Repositorio:** SQLite + SQLAlchemy 2.0
        """
    )
    st.divider()
    st.markdown("### 🔌 Servidores MCP Conectados")
    for tool_meta in default_registry.list_tools():
        st.markdown(f"• **`{tool_meta.name}`** `[{tool_meta.source}]`")

    st.divider()
    api_detectada = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    st.markdown("### ⚙️ Estatus de Conexión")
    st.write(f"• API Key LLM: {'✅ Conectado' if api_detectada else '❌ Falta .env'}")
    st.write(f"• Modelo: `{os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')}`")
    st.write(f"• Umbral Calidad OCR: `{CONFIANZA_MINIMA}`")

    st.divider()
    st.markdown("### 📥 Centro de Exportación")
    total_reg = 0
    try:
        with Session(_motor) as sesion:
            total_reg = len(sesion.execute(select(FacturaRecord.id)).scalars().all())
    except Exception:
        total_reg = 0
    st.caption(f"Total en repositorio: **{total_reg} facturas**")

    excel_side = _obtener_excel_bytes()
    if excel_side:
        st.download_button(
            label="📊 Descargar Reporte Excel",
            data=excel_side,
            file_name="reporte_facturas.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    csv_side = _generar_csv_facturas()
    if csv_side:
        st.download_button(
            label="📄 Descargar CSV Facturas",
            data=csv_side,
            file_name="facturas_consolidadas.csv",
            mime="text/csv",
            use_container_width=True,
        )
    if RUTA_BD.exists():
        st.download_button(
            label="💾 Descargar BD SQLite",
            data=RUTA_BD.read_bytes(),
            file_name="facturas.db",
            mime="application/x-sqlite3",
            use_container_width=True,
        )

# ── Selector de Documento ────────────────────────────────────────────────────

col_up, col_btn = st.columns([2, 1], gap="medium")

with col_up:
    archivo_subido = st.file_uploader(
        "Carga una factura para procesamiento (PDF o Imagen)",
        type=list(EXTENSIONES_SUBIDA),
        help=f"Soporta {', '.join(EXTENSIONES_SUBIDA)} hasta {MAX_TAMANO_MB} MB",
    )

with col_btn:
    st.write("##### Acciones")
    boton_procesar = st.button(
        "⚡ Procesar Documento",
        type="primary",
        disabled=archivo_subido is None,
        use_container_width=True,
    )
    boton_demo = st.button(
        "🧪 Probar con Factura Demo",
        help="Carga la factura de demostración empaquetada (data/factura_demo.png)",
        disabled=not RUTA_DEMO.exists(),
        use_container_width=True,
    )

if archivo_subido is not None and archivo_subido.size > MAX_TAMANO_MB * 1024 * 1024:
    st.error(f"El archivo supera el límite de {MAX_TAMANO_MB} MB.", icon="🚫")
    st.stop()


def _resolver_ruta_trabajo() -> Path:
    if archivo_subido is not None:
        return _guardar_upload(archivo_subido)
    return RUTA_DEMO


if boton_procesar or boton_demo:
    try:
        grafo = _construir_pipeline()
    except RuntimeError as exc:
        st.error(f"**Pipeline no inicializable:** {exc}", icon="🔑")
        st.stop()

    ruta_activa = _resolver_ruta_trabajo()
    st.divider()
    resultado = _ejecutar_con_status(grafo, ruta_activa)
    st.session_state["ultima_corrida"] = resultado
    st.session_state["ruta_ultima_corrida"] = ruta_activa
    st.divider()
    _mostrar_resultados(resultado, ruta_activa)

elif "ultima_corrida" in st.session_state:
    st.divider()
    _mostrar_resultados(
        st.session_state["ultima_corrida"],
        st.session_state.get("ruta_ultima_corrida"),
    )

elif not archivo_subido and not boton_demo and not boton_procesar:
    st.info(
        "Selecciona una factura o haz clic en **Probar con Factura Demo** para iniciar el análisis agéntico.",
        icon="💡",
    )
