"""Punto de entrada del Agente de Facturas (LangGraph).

Uso básico:

    1. Copia `.env.example` → `.env` y pon ahí tu GEMINI_API_KEY.
    2. Coloca una factura de prueba (PDF o imagen) en `./data`
       (o pásala como argumento). Puedes generar una de demostración con:
       `uv run python scripts/crear_factura_demo.py`
    3. Ejecuta:

        uv run python main.py                  # procesa todo lo de ./data
        uv run python main.py ruta/factura.png # procesa un archivo concreto
        uv run python main.py --debug          # logs detallados

Salida: `facturas.db` (SQLite) y `reporte_facturas.xlsx`.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Final

from agent import EstadoAgente, construir_grafo, ejecutar_archivo
from db_handler import inicializar_bd
from ocr_engine import IMAGENES_SOPORTADAS, EXTENSION_PDF

logger = logging.getLogger("main")

DIRECTORIO_DATOS_DEFAULT: Final[str] = "data"
RUTA_DOTENV: Final[Path] = Path(".env")

_EXTENSIONES_SOPORTADAS: Final[frozenset[str]] = IMAGENES_SOPORTADAS | {EXTENSION_PDF}


def _cargar_dotenv(ruta: Path = RUTA_DOTENV) -> None:
    """Carga un archivo .env (KEY=VALUE) sin dependencias externas.

    No sobrescribe las variables que ya existan en el entorno.Ignora
    comentarios (`#`) y líneas vacías; admite comillas en los valores.
    """
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea_limpia = linea.strip()
        if not linea_limpia or linea_limpia.startswith("#") or "=" not in linea_limpia:
            continue
        clave, _, valor = linea_limpia.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


def _interpretar_argumentos() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agente-facturas",
        description="Agente autónomo de facturas: OCR local + Gemini + SQLite + Excel.",
    )
    parser.add_argument(
        "archivo",
        nargs="?",
        default=None,
        metavar="PDF_O_IMAGEN",
        help="Archivo único a procesar. Si se omite, se procesa --directorio.",
    )
    parser.add_argument(
        "--directorio",
        default=DIRECTORIO_DATOS_DEFAULT,
        help=f"Directorio a escanear si no hay 'archivo' (por defecto: {DIRECTORIO_DATOS_DEFAULT}).",
    )
    parser.add_argument("--debug", action="store_true", help="Logs a nivel DEBUG.")
    return parser.parse_args()


def _resolver_entradas(args: argparse.Namespace) -> list[Path]:
    """Lista de archivos a procesar, filtrando los formatos soportados."""
    if args.archivo:
        candidatos = [Path(args.archivo)]
    else:
        directorio = Path(args.directorio)
        candidatos = sorted(directorio.iterdir()) if directorio.is_dir() else []

    return [
        candidato
        for candidato in candidatos
        if candidato.is_file() and candidato.suffix.lower() in _EXTENSIONES_SOPORTADAS
    ]


def _imprimir_resumen(estado: EstadoAgente) -> bool:
    """Imprime el desenlace de un archivo; devuelve True si hubo éxito."""
    factura = estado.get("factura")
    registro_id = estado.get("registro_id")
    nombre = Path(estado.get("archivo_entrada", "?")).name

    if factura is None or registro_id is None:
        print(f"  [ERROR]   {nombre}: {estado.get('error') or 'sin resultado'}")
        return False

    total = f"{factura.total:.2f}" if factura.total is not None else "s/dato"
    print(
        f"  [OK]      {nombre} → id={registro_id} | {factura.proveedor} | "
        f"{factura.fecha or 'sin fecha'} | total={total} | "
        f"{len(factura.items)} ítems | método={estado.get('metodo_extraccion')}"
    )
    return True


def main() -> int:
    """Orquesta: configurar entorno → compilar grafo → procesar las entradas."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    _cargar_dotenv()
    args = _interpretar_argumentos()
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    entradas = _resolver_entradas(args)
    if not entradas:
        logger.error(
            "Nada que procesar: pasa un archivo como argumento "
            "(soportados: %s) o colócalo en ./%s. Puedes generar una factura "
            "de prueba con `uv run python scripts/crear_factura_demo.py`.",
            ", ".join(sorted(_EXTENSIONES_SOPORTADAS)),
            DIRECTORIO_DATOS_DEFAULT,
        )
        return 1

    try:
        grafo = construir_grafo()
    except RuntimeError as exc:  # p. ej.: falta la GEMINI_API_KEY
        logger.error("%s", exc)
        return 2

    inicializar_bd()

    exitos = 0
    fallos = 0
    for ruta in entradas:
        estado = ejecutar_archivo(grafo, ruta)
        if _imprimir_resumen(estado):
            exitos += 1
        else:
            fallos += 1

    print(
        f"\nProceso completado: {exitos} OK / {fallos} con error → "
        "facturas.db y reporte_facturas.xlsx actualizados."
    )
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
