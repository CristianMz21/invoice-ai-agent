"""Configuración central de logging estructurado para todo el proyecto.

Formato objetivo:

    [2026-10-30 08:03:06] [INFO] [agent.py] - Nodo 'ocr': 30 líneas totales…

Un único punto de configuración (`configurar_logging`) evita que cada entry
point (CLI, Streamlit, scripts) redefina formatos distintos.
"""

from __future__ import annotations

import logging
from typing import Final

_FORMATO: Final[str] = "[%(asctime)s] [%(levelname)s] [%(module)s.py] - %(message)s"
_FECHA: Final[str] = "%Y-%m-%d %H:%M:%S"

# Librerías externas ruidosas: se degradan a WARNING para que la salida
# muestre solo el flujo del agente.
_RUIDOSOS: Final[tuple[str, ...]] = (
    "httpx",
    "httpcore",
    "google_genai",
    "google_genai.models",
    "langchain_google_genai",
    "PIL",
    "urllib3",
)


def configurar_logging(
    nivel: int = logging.INFO,
    *,
    silenciar_ruido: bool = True,
) -> None:
    """Configura el root logger con el formato canónico del proyecto.

    Es idempotente: si el root logger ya tiene handlers (p. ej. Streamlit),
    se respeta la configuración previa y solo se ajusta el nivel.
    """
    logging.basicConfig(level=nivel, format=_FORMATO, datefmt=_FECHA)
    logging.getLogger().setLevel(nivel)
    if silenciar_ruido:
        for nombre in _RUIDOSOS:
            logging.getLogger(nombre).setLevel(logging.WARNING)
