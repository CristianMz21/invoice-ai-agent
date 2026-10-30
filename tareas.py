"""Tareas de automatización del proyecto (entry points de consola).

Puestas a disposición por `[project.scripts]` en `pyproject.toml`:

    uv run lint    → ruff check --fix + ruff format + mypy
    uv run test    → pytest con reporte de cobertura
    uv run dev     → servidor Streamlit (argumentos extra se reenvían,
                     p. ej. `uv run dev -- --server.port 9000`)

Cada tarea ejecuta el subcomando con el intérprete del venv (`sys.executable`)
y con cwd fijado a la raíz del proyecto, de modo que funcione igual desde
cualquier directorio de trabajo.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path
from typing import Final

from logging_config import configurar_logging

configurar_logging()
RAIZ_PROYECTO: Final[Path] = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def _ejecutar(comando: list[str], descripcion: str) -> bool:
    """Ejecuta un comando en la raíz del proyecto; True si terminó bien."""
    logger.info("──── %s (%s) ────", descripcion, " ".join(comando))
    resultado = subprocess.run(comando, cwd=RAIZ_PROYECTO)
    if resultado.returncode != 0:
        logger.error("%s falló con código %s", descripcion, resultado.returncode)
        return False
    return True


def lint() -> int:
    """Quality gates: ruff (check + format) y mypy, en secuencia."""
    pasos: list[tuple[list[str], str]] = [
        ([sys.executable, "-m", "ruff", "check", "--fix", "."], "ruff check --fix"),
        ([sys.executable, "-m", "ruff", "format", "."], "ruff format"),
        ([sys.executable, "-m", "mypy", "."], "mypy"),
    ]
    exito = all(_ejecutar(comando, descripcion) for comando, descripcion in pasos)
    logger.info("lint %s", "OK" if exito else "FALLÓ")
    return 0 if exito else 1


def test() -> int:
    """Suite completa de tests con reporte de cobertura."""
    exito = _ejecutar(
        [sys.executable, "-m", "pytest", "--cov", "--cov-report=term-missing", "-q"],
        "pytest + cobertura",
    )
    logger.info("test %s", "OK" if exito else "FALLÓ")
    return 0 if exito else 1


def dev() -> int:
    """Levanta el servidor Streamlit reenviando argumentos extra.

    Ejemplos: `uv run dev`, `uv run dev -- --server.port 9000`, `uv run dev -- --help`.
    """
    extras = [argumento for argumento in sys.argv[1:] if argumento != "dev"]
    return subprocess.run(
        [sys.executable, "-m", "streamlit", "run", "app.py", *extras],
        cwd=RAIZ_PROYECTO,
    ).returncode


if __name__ == "__main__":
    logger.warning("Usa `uv run lint|test|dev` en lugar de ejecutar este módulo")
    sys.exit(1)
