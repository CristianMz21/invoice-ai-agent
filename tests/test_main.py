"""Pruebas unitarias para el runner CLI (main.py)."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pytest

import main
from agent import EstadoAgente
from models import InvoiceItem, InvoiceModel


def test_cargar_dotenv(tmp_path: Path) -> None:
    """Verifica la carga de variables estilo KEY=VALUE desde archivo .env."""
    env_file = tmp_path / ".env.test"
    env_file.write_text(
        "# Comentario\n"
        "TEST_VAR_1=valor_uno\n"
        'TEST_VAR_2="valor con comillas"\n'
        "\n"
        "TEST_VAR_3='comilla simple'\n"
    )

    main._cargar_dotenv(env_file)
    assert os.getenv("TEST_VAR_1") == "valor_uno"
    assert os.getenv("TEST_VAR_2") == "valor con comillas"
    assert os.getenv("TEST_VAR_3") == "comilla simple"


def test_resolver_entradas(tmp_path: Path) -> None:
    """Verifica el filtrado de archivos válidos e ignorado de extensiones desconocidas."""
    (tmp_path / "factura1.pdf").write_text("dummy")
    (tmp_path / "factura2.png").write_text("dummy")
    (tmp_path / "doc.txt").write_text("dummy")

    args = argparse.Namespace(archivo=None, directorio=str(tmp_path))
    entradas = main._resolver_entradas(args)
    nombres = {p.name for p in entradas}
    assert nombres == {"factura1.pdf", "factura2.png"}

    args_archivo = argparse.Namespace(archivo=str(tmp_path / "factura1.pdf"), directorio=None)
    assert len(main._resolver_entradas(args_archivo)) == 1


def test_registrar_resumen() -> None:
    """Evalúa el log y retorno booleano de _registrar_resumen."""
    estado_ok: EstadoAgente = {
        "archivo_entrada": "factura.pdf",
        "factura": InvoiceModel(proveedor="Test", total=100.0, items=[InvoiceItem(descripcion="Item")]),
        "registro_id": 1,
        "metodo_extraccion": "ocr+llm",
    }
    assert main._registrar_resumen(estado_ok) is True

    estado_error: EstadoAgente = {
        "archivo_entrada": "factura.pdf",
        "factura": None,
        "error": "Fallo OCR",
    }
    assert main._registrar_resumen(estado_error) is False


def test_main_sin_entradas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica retorno 1 si no hay archivos a procesar."""
    monkeypatch.setattr(
        main,
        "_interpretar_argumentos",
        lambda: argparse.Namespace(archivo=None, directorio="nonexistent", debug=True),
    )
    assert main.main() == 1


def test_main_error_api_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifica retorno 2 si falta la configuración de API Key."""
    dummy_pdf = tmp_path / "factura.pdf"
    dummy_pdf.write_text("dummy")
    monkeypatch.setattr(
        main,
        "_interpretar_argumentos",
        lambda: argparse.Namespace(archivo=str(dummy_pdf), directorio=None, debug=False),
    )
    monkeypatch.setattr(
        main,
        "construir_grafo",
        lambda: (_ for _ in ()).throw(RuntimeError("Falta la API key")),
    )
    assert main.main() == 2


def test_main_exito(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verifica retorno 0 ante ejecución exitosa del pipeline."""
    dummy_pdf = tmp_path / "factura.png"
    dummy_pdf.write_text("dummy")
    monkeypatch.setattr(
        main,
        "_interpretar_argumentos",
        lambda: argparse.Namespace(archivo=str(dummy_pdf), directorio=None, debug=False),
    )
    monkeypatch.setattr(main, "construir_grafo", lambda: "grafo_mock")
    monkeypatch.setattr(main, "inicializar_bd", lambda: None)
    monkeypatch.setattr(
        main,
        "ejecutar_archivo",
        lambda grafo, ruta: {  # type: ignore[misc,return-value]
            "archivo_entrada": str(ruta),
            "factura": InvoiceModel(proveedor="OK", total=10.0),
            "registro_id": 1,
            "metodo_extraccion": "mock",
        },
    )
    assert main.main() == 0
