"""Pruebas unitarias para el registro dinámico de herramientas (ToolRegistry)."""

from __future__ import annotations

import pytest
from langchain_core.tools import BaseTool, tool

from tools.mcp_dummy_tool import consultar_erp, consultar_tipo_cambio
from tools.registry import ToolMetadata, ToolRegistry, default_registry, get_global_registry


def test_tool_metadata() -> None:
    """Valida la inmutabilidad y atributos de ToolMetadata."""
    meta = ToolMetadata(name="calc", description="calculates", source="local", tags=("math",))
    assert meta.name == "calc"
    assert meta.tags == ("math",)
    assert not meta.is_async


def test_registry_register_function() -> None:
    """Valida el registro de una función Python pura mediante decorador."""
    reg = ToolRegistry("test_registry")

    @reg.register(name="sumar", description="Suma dos enteros", tags=["matematicas"])
    def sumar(a: int, b: int) -> int:
        return a + b

    assert reg.has_tool("sumar")
    assert "sumar" in reg.list_tool_names()
    meta = reg.get_tool_metadata("sumar")
    assert meta.source == "local"
    assert "matematicas" in meta.tags

    # Invocación directa
    assert reg.execute_tool("sumar", a=2, b=3) == 5

    # Export a LangChain tool
    lc_tool = reg.get_langchain_tool("sumar")
    assert isinstance(lc_tool, BaseTool)
    assert lc_tool.run({"a": 2, "b": 3}) == 5


def test_registry_register_langchain_tool() -> None:
    """Valida la importación directa de una BaseTool preexistente."""
    reg = ToolRegistry("lc_registry")

    @tool
    def restar(a: int, b: int) -> int:
        """Resta b de a."""
        return a - b

    reg.register_langchain_tool(restar, tags=["operaciones"])
    assert reg.has_tool("restar")
    assert reg.execute_tool("restar", a=10, b=4) == 6


def test_registry_errores() -> None:
    """Valida excepciones ante nombres duplicados o herramientas no encontradas."""
    reg = ToolRegistry("err_registry")

    @reg.register(name="f1", description="d1")
    def f1() -> str:
        return "ok"

    with pytest.raises(ValueError, match="already registered"):

        @reg.register(name="f1", description="duplicate")
        def f2() -> str:
            return "duplicate"

    with pytest.raises(KeyError, match="not registered"):
        reg.get_tool("inexistente")

    with pytest.raises(KeyError):
        reg.execute_tool("inexistente")


def test_mcp_dummy_tools() -> None:
    """Valida la ejecución de las herramientas mockup de ERP y divisas."""
    # Proveedor conocido
    res_conocido = consultar_erp("TECNOLOGIA ANDINA S.A.", "FAC-001")
    assert res_conocido["encontrado"] is True
    assert res_conocido["datos_erp"]["currency"] == "EUR"

    # Proveedor desconocido
    res_desconocido = consultar_erp("PROVEEDOR FANTASMA", "FAC-999")
    assert res_desconocido["encontrado"] is False

    # Tipo de cambio
    fx = consultar_tipo_cambio("USD", "EUR")
    assert fx["tasa"] == 0.92
    assert fx["estado"] == "CONSULTA_EXITOSA"


def test_global_registry() -> None:
    """Verifica que el registro global singleton funcione correctamente."""
    g_reg = get_global_registry()
    assert isinstance(g_reg, ToolRegistry)
    assert g_reg.name == "global_mcp_registry"
    assert default_registry.name == "global_mcp_registry"


def test_discover_tools() -> None:
    """Verifica que la auto-detección de módulos descubra las herramientas de tools."""
    reg = ToolRegistry("discover_registry")
    discovered = reg.discover_tools()
    assert any("mcp_dummy_tool" in mod for mod in discovered)
    assert reg.has_tool("consultar_erp")
    assert reg.has_tool("consultar_tipo_cambio")
