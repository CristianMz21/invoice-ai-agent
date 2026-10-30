"""Pruebas unitarias para logging_config."""

from __future__ import annotations

import logging

from logging_config import configurar_logging


def test_configurar_logging() -> None:
    """Verifica que configurar_logging ajuste el root logger."""
    configurar_logging(nivel=logging.DEBUG, silenciar_ruido=True)
    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert logging.getLogger("httpx").level == logging.WARNING
