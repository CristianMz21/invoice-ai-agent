"""Interfaz web (Streamlit) para el Agente Autónomo de Facturas.

Consume los módulos existentes sin duplicar lógica:

- `construir_grafo(motor_ocr, llm)` / `construir_llm()` → inyección de
  dependencias, idéntica a la de `main.py`.
- `EstadoAgente` → estado tipado del grafo (factura, error, registro_id, …).
- `db_handler.RUTA_EXCEL` → descarga del reporte generado.

Ejecutar:

    uv run streamlit run app.py        # abre http://localhost:8501

El motor OCR (modelos ONNX), el LLM y el grafo compilado se instancian UNA
única vez vía `@st.cache_resource`: Streamlit re-ejecuta el script en cada
interacción, pero el pipeline pesado solo se paga en el primer arranque.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import pandas as pd
import streamlit as st
from langgraph.graph.state import CompiledStateGraph
from streamlit.runtime.uploaded_file_manager import UploadedFile

from agent import CONFIANZA_MINIMA, EstadoAgente, construir_grafo, construir_llm
from db_handler import RUTA_EXCEL, inicializar_bd
from logging_config import configurar_logging
from main import _cargar_dotenv
from models import InvoiceModel
from ocr_engine import MotorOCR

# ── Configuración de página y constantes ─────────────────────────────────────

st.set_page_config(
    page_title="GTC Agent · Validador de Facturas",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

configurar_logging()  # logs del agente visibles en la consola del servidor

DIRECTORIO_DATOS: Final[Path] = Path("data")
RUTA_DEMO: Final[Path] = DIRECTORIO_DATOS / "factura_demo.png"
EXTENSIONES_SUBIDA: Final[tuple[str, ...]] = ("pdf", "png", "jpg", "jpeg")
MAX_TAMANO_MB: Final[int] = 25

# Etiqueta legible de cada nodo del grafo LangGraph → progreso en vivo.
_TITULOS_NODOS: Final[dict[str, dict[str, str]]] = {
    "ingesta": {
        "label": "📥 Ingestando documento…",
        "detalle": "PDF convertido a imágenes / imagen validada.",
    },
    "ocr": {
        "label": "👁️ Ejecutando OCR local (RapidOCR)…",
        "detalle": "Motor ONNX corriendo en tu máquina, sin servicios externos.",
    },
    "estructurar": {
        "label": "🧠 Validando y estructurando con el LLM…",
        "detalle": "Texto OCR → esquema Pydantic (with_structured_output).",
    },
    "tools": {
        "label": "🛠️ Consultando herramientas dinámicas y MCP…",
        "detalle": "Validación contable en ERP / tipos de cambio.",
    },
    "vision_fallback": {
        "label": "🚑 OCR insuficiente → fallback: Gemini Vision sobre la imagen…",
        "detalle": "Plan de contingencia: la imagen va directamente al LLM.",
    },
    "persistir": {
        "label": "💾 Persistiendo en SQLite…",
        "detalle": "INSERT en facturas.db (cabecera + items).",
    },
    "exportar": {
        "label": "📊 Exportando reporte Excel…",
        "detalle": "Se regenera reporte_facturas.xlsx desde la BD.",
    },
}

# Causas de error documentadas por el grafo → mensajes accionables.
_CAUSAS_FALLO: Final[dict[str, str]] = {
    "ingesta": "El archivo no existe o no es un PDF/imagen soportado.",
    "estructurar": ("El LLM no logró estructurar el texto OCR: posible documento vacío o ilegible."),
    "vision_fallback": (
        "El fallback de visión tampoco pudo leer la factura: probablemente la imagen "
        "está borrosa, recortada o sin importes detectables."
    ),
    "persistir": "No había una factura validada que insertar.",
}


# ── Infraestructura (cacheada por proceso del servidor) ─────────────────────


@st.cache_resource(show_spinner="Inicializando pipeline: modelo OCR + LLM + grafo LangGraph…")
def _construir_pipeline() -> CompiledStateGraph:
    """Compila el grafo UNA vez. Misma inyección de dependencias que `main.py`;
    además garantiza el schema de SQLite antes de que `persistir` inserte."""
    motor_ocr = MotorOCR()
    llm = construir_llm()
    inicializar_bd()
    return construir_grafo(motor_ocr=motor_ocr, llm=llm)


def _guardar_upload(archivo_subido: UploadedFile) -> Path:
    """Guarda la factura subida en `data/` para que OCR y pdf2image la lean.

    Si ya existe un archivo con ese nombre, añade un sufijo corto para no
    pisar la corrida anterior (cada corrida es un registro distinto).
    """
    DIRECTORIO_DATOS.mkdir(exist_ok=True)
    destino = DIRECTORIO_DATOS / Path(archivo_subido.name).name
    if destino.exists():
        destino = destino.with_name(f"{destino.stem}_{uuid4().hex[:8]}{destino.suffix}")
    destino.write_bytes(archivo_subido.getvalue())
    return destino


# ── Ejecución del grafo con progreso en vivo ────────────────────────────────


def _ejecutar_con_status(grafo: CompiledStateGraph, ruta: Path) -> EstadoAgente:
    """Ejecuta el grafo con `stream_mode="updates"` para narrar cada nodo.

    LangGraph emite el delta de cada nodo al terminar; la UI lo convierte en
    mensajes EN VIVO y fusiona los deltas (último valor gana, igual que hace
    el propio grafo) para reconstruir el estado final.
    """
    estados: dict[str, Any] = {}
    with st.status("Ejecutando agente…", expanded=True) as status:
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
                        st.write(titulos["detalle"])
                    detalle = _detalle_de_delta(nodo, delta)
                    if detalle:
                        st.write(detalle)
            exito = estados.get("factura") is not None
            status.update(
                label="Pipeline completado",
                state="complete" if exito else "error",
                expanded=False,
            )
        except Exception as exc:  # noqa: BLE001 — toda excepción se reporta abajo
            estados.setdefault("error", f"runtime: {exc}")
            status.update(label="El pipeline terminó inesperadamente", state="error")
            st.write(f"`{exc}`")

    return estados  # type: ignore[return-value]


def _detalle_de_delta(nodo: str, delta: dict[str, Any]) -> str | None:
    """Traduce los deltas del estado a mensajes útiles dentro del status."""
    if nodo == "ocr" and "confianza_ocr" in delta:
        confianza = float(delta["confianza_ocr"])
        if bool(delta.get("ocr_util")):
            return f"Confianza de lectura: **{confianza:.0%}** → texto útil, flujo principal."
        return f"Confianza de lectura: **{confianza:.0%}** → texto insuficiente."
    if nodo == "estructurar" and delta.get("factura") is not None:
        return f"Estructura reconocida: **{delta['factura'].proveedor}**."
    if nodo == "persistir" and "registro_id" in delta:
        return f"Registro guardado con id **#{delta['registro_id']}**."
    return None


# ── Renderizado de resultados ───────────────────────────────────────────────


def _mostrar_resultados(estados: EstadoAgente) -> None:
    """Primer nivel del render: éxito con métricas, o error con causa."""
    factura = estados.get("factura")
    registro_id = estados.get("registro_id")

    if factura is not None and registro_id is not None:
        _mostrar_exito(factura, estados)
    else:
        _mostrar_error(estados)


def _mostrar_exito(factura: InvoiceModel, estados: EstadoAgente) -> None:
    registro_id = estados.get("registro_id", "?")
    st.success(f"✅ Factura **#{registro_id}** procesada, validada y persistida")
    st.caption(f"Método de extracción: `{estados.get('metodo_extraccion', '—')}`")

    col_total, col_proveedor, col_fecha, col_numero = st.columns(4)
    col_total.metric(
        label="💰 Total",
        value=f"{factura.total:,.2f}" if factura.total is not None else "s/dato",
        help=f"Moneda declarada: {factura.moneda or 'no detectada'}",
    )
    col_proveedor.metric(label="🏢 Proveedor", value=_acortar(factura.proveedor, 24))
    col_fecha.metric(label="📅 Fecha", value=factura.fecha or "—")
    col_numero.metric(label="🧾 Nº factura", value=factura.numero_factura or "—")

    st.subheader("Items de la factura")
    if factura.items:
        df_items = pd.DataFrame([item.model_dump() for item in factura.items])
        st.dataframe(df_items, hide_index=True)
    else:
        st.info("El LLM no detectó líneas de detalle en esta factura.")

    st.subheader("Reporte consolidado")
    _mostrar_descarga_excel()
    _mostrar_diagnostico(estados)


def _mostrar_descarga_excel() -> None:
    """Ofrece el Excel siempre que db_handler lo haya exportado al disco."""
    if not RUTA_EXCEL.exists():
        st.warning("El reporte Excel aún no existe en el disco.")
        return
    st.download_button(
        label="📥 Descargar reporte_facturas.xlsx",
        data=RUTA_EXCEL.read_bytes(),
        file_name="reporte_facturas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary",
    )


def _mostrar_diagnostico(estados: EstadoAgente) -> None:
    """Detalle técnico (ideal para demo): texto crudo del OCR y confianza."""
    texto_ocr = estados.get("texto_ocr", "")
    if not texto_ocr.strip():
        return
    with st.expander("🔬 Diagnóstico: texto crudo del OCR"):
        st.caption(
            f"Confianza media del OCR: {estados.get('confianza_ocr', 0) * 100:.1f}% · "
            f"método: {estados.get('metodo_extraccion', '—')}"
        )
        st.code(texto_ocr, language="text")


def _mostrar_error(estados: EstadoAgente) -> None:
    """Presenta el error documentado por el grafo con una causa accionable."""
    error = estados.get("error") or "El agente terminó sin producir una factura."
    causa_key = error.split(":")[0].split()[0]
    causa = _CAUSAS_FALLO.get(causa_key, "El pipeline no pudo obtener datos suficientes de la factura.")
    st.error(
        f"**La factura no pudo procesarse.** {causa}\n\n"
        "Sugerencia: sube el documento con mejor resolución o usa un PDF "
        "generado digitalmente (no escaneado).",
        icon="🚫",
    )
    with st.expander("Detalle técnico"):
        st.code(error, language="text")
        if estados.get("texto_ocr"):
            st.write("Texto que alcanzó a ver el OCR:")
            st.code(estados["texto_ocr"], language="text")


def _acortar(texto: str, maximo: int) -> str:
    return texto if len(texto) <= maximo else texto[: maximo - 1] + "…"


# ── UI principal ─────────────────────────────────────────────────────────────

st.title("🧾 GTC Agent · Validador Autónomo de Facturas")
st.caption(
    "Agente LangGraph · OCR local (RapidOCR/ONNX) · estructuración con Gemini · SQLite · reporte en Excel"
)

_cargar_dotenv()  # idempotente: no sobrescribe variables ya presentes en el entorno

with st.sidebar:
    st.header("Arquitectura")
    st.markdown(
        """
        **Pipeline (LangGraph):**
        `ingesta → OCR → estructurar → persistir → exportar`

        - 🧠 **Flujo principal:** texto OCR → Gemini → `InvoiceModel`
        - 🚑 **Fallback:** OCR nulo/incoherente → imagen directa a Gemini Vision
        - 💾 **Fuente de verdad:** `facturas.db`; el Excel se regenera de ella
        - 🧪 **Tests:** `uv run pytest` (33 pruebas)
        """
    )
    st.subheader("Configuración detectada")
    api_detectada = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    st.write(f"- API key presente: {'✅' if api_detectada else '❌'}")
    st.write(f"- Modelo: `{os.getenv('GEMINI_MODEL', 'gemini-1.5-flash')}`")
    st.write(f"- Umbral de confianza OCR: `{CONFIANZA_MINIMA}`")
    if not api_detectada:
        st.info("Copia `.env.example` → `.env` con tu `GEMINI_API_KEY`.", icon="🔑")

archivo_subido = st.file_uploader(
    "Sube una factura (PDF o imagen)",
    type=list(EXTENSIONES_SUBIDA),
    help=f"Formatos aceptados: {', '.join(EXTENSIONES_SUBIDA)} · máx. {MAX_TAMANO_MB} MB",
)

if archivo_subido is not None and archivo_subido.size > MAX_TAMANO_MB * 1024 * 1024:
    st.error(f"El archivo supera el límite de {MAX_TAMANO_MB} MB.", icon="🚫")
    st.stop()

col_procesar, col_demo, _col_relleno = st.columns([1.5, 1.5, 2])
boton_procesar = col_procesar.button(
    "⚡ Procesar Factura",
    type="primary",
    disabled=archivo_subido is None,
    use_container_width=True,
)
boton_demo = col_demo.button(
    "🧪 Probar con factura demo",
    help="Usa data/factura_demo.png (generada por scripts/crear_factura_demo.py)",
    disabled=not RUTA_DEMO.exists(),
    use_container_width=True,
)


def _ruta_a_procesar() -> Path:
    """Elige la entrada: el archivo subido o la factura demo empaquetada."""
    if archivo_subido is not None:
        return _guardar_upload(archivo_subido)
    return RUTA_DEMO


if boton_procesar or boton_demo:
    try:
        grafo = _construir_pipeline()
    except RuntimeError as exc:  # p. ej.: falta la GEMINI_API_KEY
        st.error(f"**Pipeline no inicializable.** {exc}", icon="🔑")
        st.stop()

    st.divider()
    estatus = _ejecutar_con_status(grafo, _ruta_a_procesar())
    st.session_state["ultima_corrida"] = estatus  # persiste entre reruns
    st.divider()
    _mostrar_resultados(estatus)

elif "ultima_corrida" in st.session_state:
    # Reruns por interacciones (sidebar, etc.): conservamos el último resultado.
    st.divider()
    _mostrar_resultados(st.session_state["ultima_corrida"])

if not archivo_subido and not boton_demo and not boton_procesar:
    st.info(
        "Sube un PDF/imagen o pulsa **Probar con factura demo**. También puedes "
        "generar archivos de prueba con `uv run python scripts/crear_factura_demo.py`.",
        icon="👆",
    )
