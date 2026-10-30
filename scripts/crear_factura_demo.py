"""Genera facturas sintéticas de prueba en `data/`.

Crea una factura ficticia (ñ legible por OCR) en PNG y en PDF (dos páginas,
para probar además la conversión de PDF del nodo de ingesta):

    uv run python scripts/crear_factura_demo.py
    uv run python main.py data/factura_demo.png
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

_RAIZ: Final[Path] = Path(__file__).resolve().parent.parent
_DIRECTORIO_DATOS: Final[Path] = _RAIZ / "data"

_ANCHO, _ALTO = 1000, 1250

_CABECERA: Final[tuple[str, ...]] = (
    "TECNOLOGIA ANDINA S.A.",
    "RUC: 20-12345678-9",
    "Av. Siempreviva 742, Piso 4 - C1425 - Argentina",
)

_NUMERO_FACTURA: Final[str] = "FACTURA F-2026-0042"
_FECHA: Final[str] = "Fecha de emision: 12/03/2026"
_CUIT_CLIENTE: Final[str] = "Cliente: Estudio Mackroph SRL - CUIT: 30-76543210-1"

_ITEMS: Final[tuple[tuple[str, str, str, str], ...]] = (
    ("Licencia software anual", "2", "1.250,00", "2.500,00"),
    ("Servidor rack 1U", "1", "3.400,50", "3.400,50"),
    ("Horas consultoria DevOps", "8", "95,00", "760,00"),
)
_SUBTOTAL: Final[str] = "6.660,50"
_IVA: Final[str] = "1.398,71 (21%)"
_TOTAL: Final[str] = "$ 8.059,21"

_PIE: Final[tuple[str, ...]] = (
    "Condicion de pago: transferencia bancaria a 30 dias.",
    "IBAN: AR17 0200 0123 4567 8901 2345 6789",
)


def _fuente(tamano: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Devuelve una fuente del sistema con fallback al bitmap por defecto."""
    candidatos = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    )
    for ruta in candidatos:
        try:
            return ImageFont.truetype(ruta, tamano)
        except OSError:
            continue
    return ImageFont.load_default(size=tamano)


def _dibujar_factura() -> Image.Image:
    imagen = Image.new("RGB", (_ANCHO, _ALTO), "white")
    lapiz = ImageDraw.Draw(imagen)

    fuente_titulo = _fuente(34)
    fuente_normal = _fuente(24)
    fuente_peque = _fuente(20)

    y = 40
    for linea in _CABECERA:
        lapiz.text((60, y), linea, fill="black", font=fuente_titulo if y == 40 else fuente_normal)
        y += 44 if y == 40 else 34

    y += 30
    lapiz.text((60, y), _NUMERO_FACTURA, fill="black", font=fuente_titulo)
    y += 50
    lapiz.text((60, y), _FECHA, fill="black", font=fuente_normal)
    y += 36
    lapiz.text((60, y), _CUIT_CLIENTE, fill="black", font=fuente_normal)

    # Tabla de items.
    y += 70
    lapiz.rectangle((50, y - 14, _ANCHO - 50, y + 40), outline="black", width=2)
    encabezados = (60, 470, 630, 800)
    for x, texto in zip(encabezados, ("DESCRIPCION", "CANT", "P.UNIT", "SUBTOTAL"), strict=True):
        lapiz.text((x, y), texto, fill="black", font=fuente_normal)

    y += 60
    for descripcion, cantidad, precio, subtotal in _ITEMS:
        lapiz.text((60, y), descripcion, fill="black", font=fuente_normal)
        lapiz.text((470, y), cantidad, fill="black", font=fuente_normal)
        lapiz.text((630, y), precio, fill="black", font=fuente_normal)
        lapiz.text((800, y), subtotal, fill="black", font=fuente_normal)
        lapiz.line((50, y + 38, _ANCHO - 50, y + 38), fill="gray", width=1)
        y += 58

    # Totales.
    y += 30
    for etiqueta, valor in (("Subtotal:", _SUBTOTAL), ("IVA:", _IVA)):
        lapiz.text((630, y), etiqueta, fill="black", font=fuente_normal)
        lapiz.text((800, y), valor, fill="black", font=fuente_normal)
        y += 40
    lapix_texto_total = "TOTAL:"
    lapiz.rectangle((620, y - 10, _ANCHO - 50, y + 44), outline="black", width=3)
    lapiz.text((630, y), lapix_texto_total, fill="black", font=fuente_titulo)
    lapiz.text((800, y), _TOTAL, fill="black", font=fuente_titulo)

    # Pie.
    y += 110
    for linea in _PIE:
        lapiz.text((60, y), linea, fill="black", font=fuente_peque)
        y += 30

    return imagen


def crear_demo() -> Path:
    """Genera `data/factura_demo.png` y `data/factura_demo.pdf`."""
    _DIRECTORIO_DATOS.mkdir(exist_ok=True)

    imagen = _dibujar_factura()
    ruta_png = _DIRECTORIO_DATOS / "factura_demo.png"
    imagen.save(ruta_png, format="PNG")

    ruta_pdf = _DIRECTORIO_DATOS / "factura_demo.pdf"
    imagen.save(ruta_pdf, format="PDF", resolution=150)

    print(f"Generadas: {ruta_png} y {ruta_pdf}")
    return ruta_png


if __name__ == "__main__":
    crear_demo()
