"""Agente autónomo LangGraph: facturas → datos estructurados → SQLite → Excel.

Arquitectura (StateGraph con enrutado condicional):

    START
      │
      ▼
   ┌─────────┐
   │ ingesta │  PDF → páginas PNG (pdf2image); imagen → tal cual
   └────┬────┘        │ fallo grave → END (error)
        ▼
   ┌─────────┐
   │   OCR   │  RapidOCR local (ONNX), texto + confianza por línea
   └────┬────┘
        │ ¿texto útil? ──── no (nulo/incoherente) ──────────┐
        │ sí                                               │
        ▼                                                  │
   ┌─────────────┐                                          │
   │ estructurar │  Gemini (solo texto) → InvoiceModel      │
   └────┬────────┘                                          │
        │ ¿factura válida? ──── no ──┐                      │
        │ sí                        ▼                      ▼
        │                    ┌─────────────────────────────────────┐
        │                    │ vision_fallback                     │
        │                    │ Gemini Vision (imagen) → InvoiceModel│
        │                    └──────────────┬──────────────────────┘
        │                                   │ ¿válida? ── no ─▶ END (error)
        │ sí                                │ sí
        ▼                                   ▼
   ┌────────────┐                     ┌────────────┐
   │ persistir  │  INSERT en SQLite   │ persistir  │
   └─────┬──────┘                     └─────┬──────┘
         ▼
   ┌───────────┐
   │ exportar  │  refresca reporte_facturas.xlsx (Pandas/openpyxl)
   └─────┬─────┘
         ▼
        END

Cada nodo es una función sobre el estado compartido (`EstadoAgente`); las
dependencias (motor OCR, LLM) se inyectan al compilar el grafo, lo que facilita
tests y experimentos (p. ej. cambiar de modelo sin tocar el resto).
"""

from __future__ import annotations

import base64
import functools
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Final, TypedDict

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from db_handler import exportar_excel, insertar_factura
from models import InvoiceModel, METODO_OCR_LLM, METODO_VISION_FALLBACK
from ocr_engine import MotorOCR

logger = logging.getLogger(__name__)

MODELO_GEMINI_DEFAULT: Final[str] = "gemini-1.5-flash"

# Umbrales del nodo 2/3 para decidir si el texto del OCR es aprovechable.
CONFIANZA_MINIMA: Final[float] = 0.5
LONGITUD_MINIMA_TEXTO: Final[int] = 40  # caracteres

# ── Prompts ───────────────────────────────────────────────────────────────────

_PROMPT_ESTRUCTURAR: Final[str] = (
    "Actúa como un parser experto de facturas, muy tolerante con el ruido del "
    "OCR (saltos de línea mal puestos, columnas mezcladas, caracteres confundidos).\n\n"
    "--- TEXTO OCR ---\n"
    "{texto_ocr}\n"
    "--- FIN TEXTO OCR ---\n\n"
    "Devuelve el objeto estructurado según el esquema:\n"
    "- proveedor: razón social del emisor, tal como aparezca.\n"
    "- fecha: normalizada a ISO-8601 (YYYY-MM-DD) si es posible.\n"
    "- numero_factura y moneda, si aparecen.\n"
    "- total: importe TOTAL de la factura como número (decimal con punto).\n"
    "- items: líneas de detalle (descripcion, cantidad, precio_unitario, subtotal).\n\n"
    "Reglas: NO inventes datos que no estén en el texto; si un dato no aparece o "
    "es ilegible, usa null o lista vacía. Convierte importes con formato español "
    "(1.234,56) a 1234.56."
)

_PROMPT_VISION: Final[str] = (
    "Actúa como un parser experto de facturas. Te adjunto la imagen de una "
    "factura: léela y devuelve el objeto estructurado según el esquema "
    "(proveedor, fecha ISO-8601, numero_factura, moneda, total numérico e items "
    "con descripcion/cantidad/precio_unitario/subtotal).\n"
    "NO inventes datos: lo que no leas con claridad devuélvelo como null o "
    "lista vacía. Convierte importes con formato español a decimal con punto."
)

# ── Estado compartido del grafo ──────────────────────────────────────────────


class EstadoAgente(TypedDict, total=False):
    """Estado compartido entre los nodos.

    Sin reducers declarados → semántica "último valor gana", que es la que
    este pipeline en árbol necesita.
    """

    archivo_entrada: str  # ruta original (PDF o imagen)
    rutas_imagenes: list[Path]  # imágenes normalizadas tras la ingesta
    imagen_fallback: str  # primera imagen, reservada para el fallback de visión
    texto_ocr: str  # texto bruto concatenado (puede ser multipágina)
    ocr_util: bool  # heurística de calidad del texto OCR
    confianza_ocr: float  # confianza media del OCR
    factura: InvoiceModel | None  # salida estructurada del LLM
    metodo_extraccion: str  # `METODO_OCR_LLM` | `METODO_VISION_FALLBACK`
    registro_id: int  # PK generada en SQLite
    error: str | None  # explicación si el pipeline terminó en error


# ── Evaluación (heurísticas puras, testeables sin LLM) ──────────────────────


def texto_util(texto: str, confianza: float) -> bool:
    """Heurística del nodo 3: ¿el texto del OCR sirve para estructurar?

    Reglas simples y defensables: suficiente longitud, confianza media
    aceptable y presencia de dígitos (una factura siempre tiene números).
    """
    return (
        len(texto) >= LONGITUD_MINIMA_TEXTO
        and confianza >= CONFIANZA_MINIMA
        and any(caracter.isdigit() for caracter in texto)
    )


def factura_util(factura: InvoiceModel | None) -> bool:
    """Un registro solo merece persistirse si el LLM produjo algo con total."""
    return factura is not None and factura.es_util


# ── Construcción de dependencias ─────────────────────────────────────────────


def construir_llm(nombre_modelo: str | None = None) -> ChatGoogleGenerativeAI:
    """Instancia el LLM leyendo credenciales del entorno (.env cargado antes).

    El modelo por defecto es `gemini-1.5-flash` (según el enunciado) y puede
    sobregrabarse con la variable `GEMINI_MODEL` (p. ej. `gemini-2.5-flash`
    si el proyecto no tiene acceso a 1.5).
    """
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Falta la API key: crea un archivo .env (ver .env.example) con "
            "GEMINI_API_KEY=tu_clave_de_google_ai_studio"
        )
    modelo = nombre_modelo or os.getenv("GEMINI_MODEL", MODELO_GEMINI_DEFAULT)
    return ChatGoogleGenerativeAI(
        model=modelo,
        google_api_key=api_key,
        temperature=0,  # extracción estructurada determinista
    )


# ── Nodos ────────────────────────────────────────────────────────────────────


def _nodo_ingesta(estado: EstadoAgente, *, motor: MotorOCR) -> dict[str, Any]:
    """Nodo 1: valida el archivo y lo normaliza a imágenes."""
    ruta = Path(estado["archivo_entrada"])
    logger.info("Nodo 'ingesta': analizando %s", ruta)
    try:
        rutas_imagenes = motor.preparar_entradas(ruta)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        logger.error("La ingesta falló: %s", exc)
        return {"error": f"ingesta: {exc}", "factura": None, "rutas_imagenes": []}
    logger.info("  Ingesta OK: %d página(s)/imagen(es)", len(rutas_imagenes))
    return {
        "rutas_imagenes": rutas_imagenes,
        "imagen_fallback": str(rutas_imagenes[0]),
        "error": None,
    }


def _nodo_ocr(estado: EstadoAgente, *, motor: MotorOCR) -> dict[str, Any]:
    """Nodo 2: OCR local con RapidOCR sobre cada imagen y agregación."""
    rutas = estado.get("rutas_imagenes", [])
    if not rutas:
        return {"texto_ocr": "", "ocr_util": False, "confianza_ocr": 0.0}

    textos: list[str] = []
    confianzas: list[float] = []
    total_lineas = 0
    for ruta in rutas:
        resultado = motor.extraer_texto(ruta)
        if not resultado.exito:
            logger.warning("  OCR sin texto en %s", ruta.name)
            continue
        logger.info(
            "  OCR %s → %d líneas (confianza %.2f)",
            ruta.name,
            resultado.lineas_detectadas,
            resultado.confianza_media,
        )
        textos.append(resultado.texto)
        confianzas.append(resultado.confianza_media)
        total_lineas += resultado.lineas_detectadas

    texto = "\n".join(textos)
    confianza = sum(confianzas) / len(confianzas) if confianzas else 0.0
    util = texto_util(texto, confianza)
    logger.info(
        "Nodo 'ocr': %d líneas totales, confianza media %.2f → %s",
        total_lineas,
        confianza,
        "texto útil" if util else "TEXTO INSUFICIENTE",
    )
    return {"texto_ocr": texto, "ocr_util": util, "confianza_ocr": round(confianza, 4)}


def _nodo_estructurar(estado: EstadoAgente, *, llm: ChatGoogleGenerativeAI) -> dict[str, Any]:
    """Nodo 3 (flujo principal): texto OCR → Gemini → InvoiceModel."""
    texto = estado.get("texto_ocr", "")
    logger.info("Nodo 'estructurar': parseando el texto OCR con el LLM…")
    parser = llm.with_structured_output(InvoiceModel)
    try:
        factura = parser.invoke(_PROMPT_ESTRUCTURAR.format(texto_ocr=texto))
    except Exception as exc:  # cualquier fallo del LLM activa el fallback
        logger.warning("La estructuración por texto falló (%s) → fallback de visión", exc)
        return {"factura": None, "error": f"estructurar (texto): {exc}"}
    if factura is None or not isinstance(factura, InvoiceModel):
        return {"factura": None, "error": "estructurar (texto): respuesta vacía del LLM"}
    logger.info(
        "  Factura estructurada por texto: '%s' (total=%.2f)",
        factura.proveedor,
        factura.total or 0.0,
    )
    return {"factura": factura, "metodo_extraccion": METODO_OCR_LLM, "error": None}


def _nodo_vision_fallback(estado: EstadoAgente, *, llm: ChatGoogleGenerativeAI) -> dict[str, Any]:
    """Nodo 3B (plan de contingencia): imagen directa → Gemini Vision."""
    ruta = estado.get("imagen_fallback", "")
    if not ruta or not Path(ruta).exists():
        logger.error("El fallback de visión no tiene imagen disponible")
        return {"factura": None, "error": "vision_fallback: no hay imagen disponible"}

    logger.info("Nodo 'vision_fallback': enviando %s directamente al LLM…", Path(ruta).name)
    mime = mimetypes.types_map.get(Path(ruta).suffix.lower(), "image/png")
    datos = base64.b64encode(Path(ruta).read_bytes()).decode("ascii")
    mensaje = HumanMessage(
        content=[
            {"type": "text", "text": _PROMPT_VISION},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{datos}"}},
        ]
    )
    parser = llm.with_structured_output(InvoiceModel)
    try:
        factura = parser.invoke([mensaje])
    except Exception as exc:  # último eslabón: si falla, el pipeline termina en error
        logger.error("El fallback de visión también falló: %s", exc)
        return {"factura": None, "error": f"vision_fallback: {exc}"}
    if factura is None or not isinstance(factura, InvoiceModel):
        return {"factura": None, "error": "vision_fallback: respuesta vacía del LLM"}
    logger.info(
        "  Factura estructurada por visión: '%s' (total=%.2f)",
        factura.proveedor,
        factura.total or 0.0,
    )
    return {"factura": factura, "metodo_extraccion": METODO_VISION_FALLBACK, "error": None}


def _nodo_persistir(estado: EstadoAgente) -> dict[str, Any]:
    """Nodo 4: inserta la factura validada en SQLite."""
    factura = estado.get("factura")
    if factura is None:  # defensa: el enrutado jamás debería permitirlo
        return {"error": "persistir: no hay factura que insertar"}
    registro_id = insertar_factura(
        factura=factura,
        archivo_origen=estado.get("archivo_entrada", "?"),
        metodo=estado.get("metodo_extraccion", "?"),
    )
    logger.info("Nodo 'persistir': fila #%d en facturas.db", registro_id)
    return {"registro_id": registro_id}


def _nodo_exportar(estado: EstadoAgente) -> dict[str, Any]:
    """Nodo 5: refresca el reporte Excel con todo lo que hay en SQLite."""
    ruta = exportar_excel()
    logger.info("Nodo 'exportar': reporte actualizado → %s", ruta.name)
    return {}


# ── Routers (aristas condicionales, públicos y testeables) ──────────────────


def enrutar_ingesta(estado: EstadoAgente) -> str:
    """Tras la ingesta: continuar con el OCR o abortar si falló todo."""
    return "ocr" if estado.get("rutas_imagenes") else END


def enrutar_post_ocr(estado: EstadoAgente) -> str:
    """Tras el OCR: texto útil → flujo principal; nulo/incoherente → fallback."""
    if estado.get("ocr_util", False):
        return "estructurar"
    logger.warning("Texto OCR insuficiente o nulo → activando fallback de visión")
    return "vision_fallback"


def enrutar_post_estructurar(estado: EstadoAgente) -> str:
    """Tras el flujo principal: si la factura no es validable, salta al fallback."""
    if factura_util(estado.get("factura")):
        return "persistir"
    logger.warning("La estructuración por texto no produjo una factura utilizable → fallback")
    return "vision_fallback"


def enrutar_post_fallback(estado: EstadoAgente) -> str:
    """Tras el fallback: persistir si hubo éxito; si no, terminar con error."""
    if factura_util(estado.get("factura")):
        return "persistir"
    logger.error("El pipeline no pudo estructurar la factura: %s", estado.get("error"))
    return END


# ── Compilación del grafo ────────────────────────────────────────────────────


def construir_grafo(
    motor_ocr: MotorOCR | None = None,
    llm: ChatGoogleGenerativeAI | None = None,
) -> CompiledStateGraph:
    """Compila el StateGraph inyectando las dependencias por cierre."""
    motor = motor_ocr or MotorOCR()
    modelo = llm or construir_llm()

    grafo = StateGraph(EstadoAgente)

    grafo.add_node("ingesta", functools.partial(_nodo_ingesta, motor=motor))
    grafo.add_node("ocr", functools.partial(_nodo_ocr, motor=motor))
    grafo.add_node("estructurar", functools.partial(_nodo_estructurar, llm=modelo))
    grafo.add_node("vision_fallback", functools.partial(_nodo_vision_fallback, llm=modelo))
    grafo.add_node("persistir", _nodo_persistir)
    grafo.add_node("exportar", _nodo_exportar)

    grafo.add_edge(START, "ingesta")
    grafo.add_conditional_edges("ingesta", enrutar_ingesta, {"ocr": "ocr", END: END})
    grafo.add_conditional_edges(
        "ocr", enrutar_post_ocr, {"estructurar": "estructurar", "vision_fallback": "vision_fallback"}
    )
    grafo.add_conditional_edges(
        "estructurar",
        enrutar_post_estructurar,
        {"persistir": "persistir", "vision_fallback": "vision_fallback"},
    )
    grafo.add_conditional_edges(
        "vision_fallback", enrutar_post_fallback, {"persistir": "persistir", END: END}
    )
    grafo.add_edge("persistir", "exportar")
    grafo.add_edge("exportar", END)

    logger.debug("Grafo LangGraph compilado (6 nodos, 4 routers condicionales)")
    return grafo.compile()


def ejecutar_archivo(grafo: CompiledStateGraph, ruta: Path) -> EstadoAgente:
    """Conveniencia: ejecuta el pipeline completo sobre un archivo."""
    logger.info("════ Iniciando pipeline para: %s ════", ruta)
    estado_final = grafo.invoke({"archivo_entrada": str(ruta)})
    return estado_final
