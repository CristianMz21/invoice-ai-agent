"""Encapsula el OCR local ultraligero (RapidOCR sobre ONNXRuntime).

RapidOCR trae embebidos los modelos PaddleOCR convertidos a ONNX (~15 MB en
total): no depende de GPU, ni de servicios externos. Este módulo aísla todo el
detalle técnico del motor para que el resto del proyecto solo vea
`ResultadoOCR` y `MotorOCR.preparar_entradas()`.

Además convierte PDFs a imágenes con `pdf2image`, que requiere poppler en el
sistema (`sudo apt install poppler-utils`).
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pdf2image import convert_from_path
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError
from rapidocr_onnxruntime import RapidOCR

logger = logging.getLogger(__name__)

EXTENSION_PDF: Final[str] = ".pdf"

IMAGENES_SOPORTADAS: Final[frozenset[str]] = frozenset(
    {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
)


@dataclass(slots=True)
class ResultadoOCR:
    """Salida procesada del OCR sobre una imagen."""

    texto: str
    confianza_media: float
    lineas_detectadas: int

    @property
    def exito(self) -> bool:
        """Indica si hay texto aprovechable (no vacío)."""
        return bool(self.texto.strip())


def es_pdf(ruta: Path) -> bool:
    return ruta.suffix.lower() == EXTENSION_PDF


def es_imagen(ruta: Path) -> bool:
    return ruta.suffix.lower() in IMAGENES_SOPORTADAS


class MotorOCR:
    """Wrapper perezoso (lazy) sobre RapidOCR.

    El motor (y sus modelos ONNX) se instancia la primera vez que se usa, así
    el arranque del programa no paga ese coste si no hay nada que procesar. La
    instancia se inyecta al compilar el grafo, por lo que se reutiliza en todos
    los nodos y ejecuciones del proceso.
    """

    def __init__(self, dpi_pdf: int = 200) -> None:
        self._motor: RapidOCR | None = None
        self.dpi_pdf = dpi_pdf

    # ── API pública ────────────────────────────────────────────────────────

    def extraer_texto(self, ruta_imagen: Path) -> ResultadoOCR:
        """Ejecuta el OCR sobre una imagen y agrega el texto por líneas."""
        motor = self._obtener_motor()
        bruto, _latencias = motor(str(ruta_imagen))  # list[[caja, texto, score]] | None

        lineas: list[str] = []
        puntuaciones: list[float] = []
        for fila in bruto or []:
            texto_linea = str(fila[1]).strip()
            if texto_linea:
                lineas.append(texto_linea)
                puntuaciones.append(float(fila[2]))

        confianza = sum(puntuaciones) / len(puntuaciones) if puntuaciones else 0.0
        logger.debug(
            "%s → %d líneas, confianza media %.2f",
            ruta_imagen.name,
            len(lineas),
            confianza,
        )
        return ResultadoOCR(
            texto="\n".join(lineas),
            confianza_media=round(confianza, 4),
            lineas_detectadas=len(lineas),
        )

    def preparar_entradas(self, ruta: Path) -> list[Path]:
        """Normaliza un archivo de entrada a una lista de imágenes.

        - PDF → lo rasteriza página a página (pdf2image + poppler).
        - Imagen → la devuelve tal cual.
        - Otro → `ValueError` con mensaje claro.
        """
        if es_pdf(ruta):
            return self._pdf_a_imagenes(ruta)
        if es_imagen(ruta):
            if not ruta.exists():
                raise FileNotFoundError(f"La imagen no existe: {ruta}")
            return [ruta]
        raise ValueError(f"Formato no soportado ({ruta.suffix or 'sin extensión'}): {ruta}")

    # ── Internos ───────────────────────────────────────────────────────────

    def _obtener_motor(self) -> RapidOCR:
        if self._motor is None:
            logger.info("Inicializando RapidOCR (modelos ONNX locales)…")
            self._motor = RapidOCR()
        return self._motor

    def _pdf_a_imagenes(self, ruta_pdf: Path) -> list[Path]:
        if not ruta_pdf.exists():
            raise FileNotFoundError(f"El PDF no existe: {ruta_pdf}")

        directorio_tmp = Path(tempfile.mkdtemp(prefix="facturas_ocr_"))
        try:
            paginas = convert_from_path(str(ruta_pdf), dpi=self.dpi_pdf, fmt="png")
        except (PDFInfoNotInstalledError, PDFPageCountError, OSError) as exc:
            raise RuntimeError(
                f"No se pudo rasterizar el PDF '{ruta_pdf.name}'. Comprueba que "
                "poppler está instalado (p. ej. `sudo apt install poppler-utils`)."
                f" Detalle: {exc}"
            ) from exc

        rutas: list[Path] = []
        for numero, pagina in enumerate(paginas, start=1):
            destino = directorio_tmp / f"{ruta_pdf.stem}_pag{numero:02d}.png"
            pagina.save(destino, format="PNG")
            rutas.append(destino)

        logger.info(
            "PDF '%s' → %d página(s) de imagen en %s", ruta_pdf.name, len(rutas), directorio_tmp
        )
        return rutas
