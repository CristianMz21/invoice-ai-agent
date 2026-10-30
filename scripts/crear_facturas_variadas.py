"""Genera un set variado de facturas sintéticas de prueba en `data/`.

Amplía `crear_factura_demo.py` con muestras de estilos opuestos:

- **Automáticas** (impresas, limpias): OCR local directo, distinta moneda y formato.
- **Manuales** (manuscrita a lápiz, escaneo de baja calidad): degradan el OCR
  a propósito para ejercitar el fallback de visión de Gemini.

Archivos generados:

    factura_norte_arte.png         digital limpia (ARS, formato 1.234,56)
    factura_tecnoexpress_eur.pdf   digital europea, 2 páginas (EUR)
    factura_servicios_tec.jpeg     digital limpia en formato JPEG
    factura_hoja_taller.png        manuscrita clara (fuente caligráfica Z003)
    factura_kiosco_central.jpg     escaneo sucio (rotada, borrosa, JPEG 45)
    factura_fotocopia_lavada.jpg   fotocopia desvaída → cae al fallback visual

Uso:

    uv run python scripts/crear_facturas_variadas.py
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from logging_config import configurar_logging

logger = logging.getLogger(__name__)

_DIR: Final[Path] = Path(__file__).resolve().parent.parent / "data"

_FUENTES_IMPRENTA: Final[tuple[str, ...]] = (
    "/usr/share/fonts/urw-base35/NimbusSans-Regular.otf",
    "/usr/share/fonts/msttcore/comic.ttf",
)
_FUENTE_MANUSCRITA: Final[str] = "/usr/share/fonts/urw-base35/Z003-MediumItalic.otf"

_COL_ITEM, _COL_CANT, _COL_UNIT, _COL_SUBTOTAL = 60, 470, 630, 800
_COL_DER: Final[int] = 940  # columna derecha de los totales (valores alineados aquí)


def _fuente(tamano: int, candidatos: tuple[str, ...] = _FUENTES_IMPRENTA) -> ImageFont.FreeTypeFont:
    """Carga la primera fuente disponible entre los candidatos."""
    for ruta in *candidatos, _FUENTE_MANUSCRITA:
        try:
            return ImageFont.truetype(ruta, tamano)
        except OSError:
            continue
    return ImageFont.load_default(size=tamano)  # type: ignore[return-value]


def _tabla_items(lapiz: ImageDraw.ImageDraw, y0: int, items: tuple[tuple[str, str, str, str], ...]) -> int:
    """Dibuja la tabla de items; devuelve la `y` final del encabezado de totales."""
    ancho = 1000
    fuente = _fuente(22)
    lapiz.rectangle((50, y0 - 14, ancho - 50, y0 + 40), outline="black", width=2)
    for x, texto in zip(
        (_COL_ITEM, _COL_CANT, _COL_UNIT, _COL_SUBTOTAL),
        ("DESCRIPCION", "CANT", "P.UNIT", "SUBTOTAL"),
        strict=True,
    ):
        lapiz.text((x, y0), texto, fill="black", font=fuente)
    y = y0 + 60
    for descripcion, cantidad, precio, subtotal in items:
        lapiz.text((_COL_ITEM, y), descripcion, fill="black", font=fuente)
        lapiz.text((_COL_CANT, y), cantidad, fill="black", font=fuente)
        lapiz.text((_COL_UNIT, y), precio, fill="black", font=fuente)
        lapiz.text((_COL_SUBTOTAL, y), subtotal, fill="black", font=fuente)
        lapiz.line((50, y + 38, ancho - 50, y + 38), fill="gray", width=1)
        y += 58
    return y


def _comun(
    lapiz: ImageDraw.ImageDraw,
    y0: int,
    cabecera: tuple[str, ...],
    factura: str,
    fecha: str,
    cliente: str,
    tamano_titulo: int = 34,
) -> int:
    """Dibuja cabecera, número, fecha y cliente; devuelve la `y` libre siguiente."""
    fuente_titulo = _fuente(tamano_titulo)
    fuente_normal = _fuente(24)
    y = y0
    for i, linea in enumerate(cabecera):
        lapiz.text((60, y), linea, fill="black", font=fuente_titulo if i == 0 else fuente_normal)
        y += 44 if i == 0 else 34
    y += 30
    lapiz.text((60, y), factura, fill="black", font=fuente_titulo)
    y += 50
    lapiz.text((60, y), fecha, fill="black", font=fuente_normal)
    y += 36
    lapiz.text((60, y), cliente, fill="black", font=fuente_normal)
    return y + 70


def _totales(
    lapiz: ImageDraw.ImageDraw,
    y0: int,
    pares: tuple[tuple[str, str], ...],
    total_tamano: int = 30,
    margen: int = 630,
) -> int:
    """Bloque de totales con valores alineados a la derecha; devuelve la `y` siguiente.

    Cada valor se mide con `textlength` y se ancla a `_COL_DER`: los importes
    largos ya no desbordan el recuadro ni el borde del lienzo, y la etiqueta
    (y el recuadro) se corren a la izquierda si hiciera falta espacio.
    """
    fuente_normal = _fuente(24)
    fuente_total = _fuente(total_tamano)
    y = y0
    for etiqueta, valor in pares[:-1]:
        lapiz.text((margen, y), etiqueta, fill="black", font=fuente_normal)
        valor_x = _COL_DER - lapiz.textlength(valor, font=fuente_normal)
        lapiz.text((valor_x, y), valor, fill="black", font=fuente_normal)
        y += 40

    etiqueta_total, valor_total = pares[-1]
    valor_x = _COL_DER - lapiz.textlength(valor_total, font=fuente_total)
    etiqueta_x = min(margen, int(valor_x - lapiz.textlength(etiqueta_total, font=fuente_total) - 12))
    lapiz.rectangle((etiqueta_x - 10, y - 8, 950, y + 46), outline="black", width=3)
    lapiz.text((etiqueta_x, y + 2), etiqueta_total, fill="black", font=fuente_total)
    lapiz.text((valor_x, y + 2), valor_total, fill="black", font=fuente_total)
    return y + 110


def _pie(lapiz: ImageDraw.ImageDraw, y0: int, lineas: tuple[str, ...]) -> None:
    fuente = _fuente(20)
    y = y0
    for linea in lineas:
        lapiz.text((60, y), linea, fill="black", font=fuente)
        y += 30


# ── Facturas automáticas (impresas) ──────────────────────────────────────────


def _recorte_inferior(imagen: Image.Image, margen: int = 70) -> Image.Image:
    """Ajusta el alto hasta el último contenido + `margen` px (ancho intacto).

    Elimina el blanco sobrante del lienzo fijo (1200+px) para que el papel
    termine poco después del pie, como un documento real.
    """
    oscura = imagen.convert("L").point(lambda pixel: 255 if pixel < 240 else 0)
    caja = oscura.getbbox()
    if caja is None:
        return imagen
    hasta = min(imagen.height, caja[3] + margen)
    return imagen.crop((0, 0, imagen.width, hasta))


def _factura_norte_arte() -> Image.Image:
    """Factura limpia de indumentaria (ARS, formato argentino 1.234,56)."""
    imagen = Image.new("RGB", (1000, 1250), "white")
    lapiz = ImageDraw.Draw(imagen)
    y = _comun(
        lapiz,
        40,
        ("NORTE ARTE INDUMENTARIA S.R.L.", "CUIT: 30-44556677-8", "Obispo Trejo 245, Cordoba - Argentina"),
        "FACTURA B N° 0002-00015233",
        "Fecha de emision: 28/04/2026",
        "Cliente: Estudio Mackroph SRL - CUIT: 30-76543210-1",
    )
    items: Final[tuple[tuple[str, str, str, str], ...]] = (
        ("Camisetas premium x unidad", "12", "4.500,00", "54.000,00"),
        ("Velas decorativas aroma", "30", "850,00", "25.500,00"),
        ("Cajas para envio", "20", "375,00", "7.500,00"),
    )
    y = _tabla_items(lapiz, y, items)
    y = _totales(
        lapiz, y + 30, (("Subtotal:", "87.000,00"), ("IVA 21%:", "18.270,00"), ("TOTAL:", "$ 105.270,00"))
    )
    _pie(
        lapiz, y, ("Condicion de pago: 30 dias F.F. Sistema ARS", "Gracias por su compra - vendedor: piso 1")
    )
    return _recorte_inferior(imagen)


def _factura_tecnoexpress(es_copia: bool) -> Image.Image:
    """Factura europea en EUR (formato 1.234,56), con cabecera de copia opcional."""
    imagen = Image.new("RGB", (1000, 1250), "white")
    lapiz = ImageDraw.Draw(imagen)
    extra: tuple[str, ...] = ("NIF: B12345678", "Calle Mayor 15, 28013 Madrid - Espana")
    if es_copia:
        extra = ("Copia del cliente", *extra)
    y = _comun(
        lapiz,
        40,
        ("TECNOEXPRESS SOLUCIONES S.L.", *extra),
        "COPIA M-2026/0374" if es_copia else "FACTURA M-2026/0374",
        "Fecha de emision: 02/05/2026",
        "Cliente: Grupo Delta S.A. - NIF: B87654321",
    )
    items: Final[tuple[tuple[str, str, str, str], ...]] = (
        ("Servicio instalacion ERP por hora", "6", "95,50", "573,00"),
        ("Servidor usado Dell R630", "1", "1.180,00", "1.180,00"),
        ("Soporte remoto mensual", "1", "145,75", "145,75"),
    )
    y = _tabla_items(lapiz, y, items)
    y = _totales(
        lapiz, y + 30, (("Base imponible:", "1.898,75"), ("IVA 21%:", "398,74"), ("TOTAL:", "EUR 2.297,49"))
    )
    _pie(lapiz, y, ("Forma de pago: domiciliacion bancaria SEPA 5 dias.", "Vencimiento: 01/06/2026"))
    return _recorte_inferior(imagen)


def _factura_servicios_tec() -> Image.Image:
    """Servidores TI (ARS grandes importes), formato `.jpg` y limpia."""
    imagen = Image.new("RGB", (1000, 1250), "white")
    lapiz = ImageDraw.Draw(imagen)
    y = _comun(
        lapiz,
        40,
        ("SERVICIOS TECH SERV S.A.", "CUIT: 30-70123456-3", "Av. Independencia 3000, Buenos Aires"),
        "FACTURA A N° 0004-00027514",
        "Fecha de emision: 15/05/2026",
        "Cliente: Cooperativa Andes Limitada - CUIT: 30-55889900-4",
    )
    items: Final[tuple[tuple[str, str, str, str], ...]] = (
        ("Mantenimiento preventivo servidores", "3", "85.000,00", "255.000,00"),
        ("Licencias monitorizacion", "5", "12.400,00", "62.000,00"),
        ("Backup cloud mensual", "2", "45.500,00", "91.000,00"),
    )
    y = _tabla_items(lapiz, y, items)
    y = _totales(
        lapiz, y + 30, (("Subtotal:", "408.000,00"), ("IVA 21%:", "85.680,00"), ("TOTAL:", "$ 493.680,00"))
    )
    _pie(lapiz, y, ("Contacto soporte: soporte@techserv.example - serie BS", "Referencia PO: 2026-TEC-0877"))
    return _recorte_inferior(imagen)


# ── Facturas manuales (manuscrita / escaneo dañado) ──────────────────────────


def _linea_manual(
    lapiz: ImageDraw.ImageDraw,
    x: float,
    y: float,
    texto: str,
    fuente: ImageFont.FreeTypeFont,
    rng: random.Random,
    tinta: str = "#3d3b36",
) -> float:
    """Escribe `texto` palabra por palabra con ± jitter vertical (a mano)."""
    x_actual, y_actual = x, y
    for palabra in texto.split():
        lapiz.text((x_actual, y_actual + rng.uniform(-5.0, 5.0)), palabra, fill=tinta, font=fuente)
        x_actual += lapiz.textlength(palabra, font=fuente) + rng.uniform(4.0, 9.0)
    return y_actual + fuente.size * 1.55


def _ruido_puntos(
    imagen: Image.Image, rng: random.Random, cantidad: int = 1200, color: str = "#5a5852"
) -> None:
    """Grafiado tipo polvo/fibra del escaneo sobre la imagen (mutación in place)."""
    lapiz = ImageDraw.Draw(imagen)
    ancho, alto = imagen.size
    for _ in range(cantidad):
        lapiz.point((rng.randrange(ancho), rng.randrange(alto)), fill=color)


def _nota_taller_base(tinta: str, papel: tuple[int, int, int]) -> Image.Image:
    """Nota del taller a mano con la tinta y el papel indicados (sin degradar)."""
    rng = random.Random(7)
    imagen = Image.new("RGB", (1050, 1350), papel)
    lapiz = ImageDraw.Draw(imagen)
    fuente = _fuente(30, (_FUENTE_MANUSCRITA,))

    y = 60.0
    y = _linea_manual(lapiz, 70, y, "TALLER HOJA TECNICA", fuente, rng, tinta)
    y = _linea_manual(lapiz, 90, y, "Repuestos y reparaciones", fuente, rng, tinta)
    y = _linea_manual(lapiz, 90, y, "Pedido interno SJ-0291", fuente, rng, tinta)
    y += 12
    y = _linea_manual(lapiz, 90, y, "Reparacion tablero principal  14.800", fuente, rng, tinta)
    y = _linea_manual(lapiz, 90, y, "Mano de obra x 4 horas       20.000", fuente, rng, tinta)
    y += 14
    lapiz.line((560, y, 980, y), fill=tinta, width=2)
    y = _linea_manual(lapiz, 560, y, "TOTAL $  34.800", fuente, rng, tinta)
    lapiz.line((560, y - 4, 980, y - 4), fill=tinta, width=2)
    return imagen


def _factura_hoja_taller() -> Image.Image:
    """Manuscrita a lápiz sobre papel crema, claramente legible (OCR directa)."""
    tinta, papel = "#3d3b36", (250, 247, 238)
    base = _nota_taller_base(tinta, papel)
    _ruido_puntos(base, random.Random(7), cantidad=1500, color="#9a978e")
    logger.debug("Manuscrita dibujada (fuente Z003, giro 3°)")
    return base.rotate(3.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=papel)


def _factura_fotocopia_lavada() -> Image.Image:
    """Fotocopia lavada de la misma nota: tinta desvaída + giro 7° + pérdida.

    Parámetros calibrados empíricamente: RapidOCR devuelve 0 caracteres
    (conf 0.00) → el router deriva a `vision_fallback`, que sí puede
    leerla porque la nota sigue siendo reconocible a simple vista.
    """
    tinta, papel = "#b7b4ad", (252, 250, 244)
    base = _nota_taller_base(tinta, papel)
    _ruido_puntos(base, random.Random(7), cantidad=3000, color="#9a978e")
    girada = base.rotate(7.0, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=papel)
    chica = girada.resize((int(girada.width * 0.25), int(girada.height * 0.25)), Image.Resampling.BILINEAR)
    vuelta = chica.resize(girada.size, Image.Resampling.BILINEAR)
    return vuelta.filter(ImageFilter.GaussianBlur(1.6))


def _factura_kiosco_central() -> Image.Image:
    """Factura tipo C (IVA incluido) simulando escaneo sucio: giro + blur + JPEG 45."""
    imagen = Image.new("RGB", (1000, 1250), "white")
    lapiz = ImageDraw.Draw(imagen)
    y = _comun(
        lapiz,
        40,
        ("KIOSCO CENTRAL 24HS S.R.L.", "CUIT: 30-69001122-5", "Corrientes 1235, CABA"),
        "FACTURA C N° 0004-00041221",
        "Fecha de emision: 21/05/2026",
        "Cliente: consumidor final",
    )
    items: Final[tuple[tuple[str, str, str, str], ...]] = (
        ("Gaseosa 2.25 lt", "6", "1.850,00", "11.100,00"),
        ("Snacks salados", "10", "760,00", "7.600,00"),
        ("Panificados surtidos", "4", "590,00", "2.360,00"),
    )
    y = _tabla_items(lapiz, y, items)
    y = _totales(lapiz, y + 30, (("IVA incluido:", "21.060,00"), ("TOTAL:", "$ 21.060,00")))
    _pie(lapiz, y, ("Caja 1 - Cajero: turno manana", "Sin alquiler de sistema"))
    sucia = imagen.rotate(2.2, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    sucia = sucia.filter(ImageFilter.GaussianBlur(0.6))
    _ruido_puntos(sucia, random.Random(99), cantidad=700, color="#7c7a75")
    return sucia


# ── Persistencia ────────────────────────────────────────────────────────────


def _guardar(imagen: Image.Image, nombre: str, *, calidad_jpeg: int | None = None) -> Path:
    """Guarda en `data/` según la extensión; JPEG respeta `calidad_jpeg`."""
    _DIR.mkdir(exist_ok=True)
    ruta = _DIR / nombre
    kwargs: dict[str, object] = {"format": ruta.suffix[1:].upper()}
    if calidad_jpeg is not None:
        kwargs.update(quality=calidad_jpeg)
    if kwargs.get("format") in {"JPG", "JPEG"}:
        kwargs["format"] = "JPEG"
        imagen.convert("RGB").save(ruta, **kwargs)  # type: ignore[arg-type]
    else:
        imagen.save(ruta, **kwargs)  # type: ignore[arg-type]
    logger.info("Generada: %s", ruta)
    return ruta


def crear_facturas_variadas() -> list[Path]:
    """Genera las 6 muestras nuevas en `data/` y devuelve sus rutas."""
    tecno_a, tecno_b = _factura_tecnoexpress(False), _factura_tecnoexpress(True)
    rutas = [
        _guardar(_factura_norte_arte(), "factura_norte_arte.png"),
        _guardar(tecno_a, "factura_tecnoexpress_eur.pdf"),
        _guardar(_factura_servicios_tec(), "factura_servicios_tec.jpeg"),
        _guardar(_factura_hoja_taller(), "factura_hoja_taller.png"),
        _guardar(_factura_fotocopia_lavada(), "factura_fotocopia_lavada.jpg", calidad_jpeg=50),
        _guardar(_factura_kiosco_central(), "factura_kiosco_central.jpg", calidad_jpeg=45),
    ]
    ruta_pdf = _DIR / "factura_tecnoexpress_eur.pdf"
    tecno_a.save(ruta_pdf, format="PDF", save_all=True, append_images=[tecno_b], resolution=150)
    logger.debug("PDF de 2 páginas escrito: %s", ruta_pdf)
    return rutas


if __name__ == "__main__":
    configurar_logging()
    crear_facturas_variadas()
