"""Enterprise Tool Registry & Plugin Manager.

Provides dynamic registration, discovery, and conversion of execution tools
(including local Python callables, LangChain tools, and remote Model Context Protocol (MCP) servers).
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolMetadata(object):
    """Metadata describing a registered tool and its origin."""

    name: str
    description: str
    source: str = "local"  # "local", "mcp", or "langchain"
    tags: tuple[str, ...] = field(default_factory=tuple)
    is_async: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.tags, (list, set)):
            object.__setattr__(self, "tags", tuple(self.tags))


@dataclass
class ToolEntry(object):
    """Internal registry entry encapsulating tool metadata and callable implementation."""

    metadata: ToolMetadata
    callable_func: Callable[..., Any]
    langchain_tool: BaseTool | None = None


def _resolver_directorio_paquete(
    package_path_or_name: str | Path | None,
) -> tuple[Path, str] | None:
    """Resuelve la ruta y el prefijo de paquete para la introspección dinámica."""
    if package_path_or_name is None:
        return Path(__file__).resolve().parent, "tools."
    if isinstance(package_path_or_name, Path):
        return package_path_or_name, f"{package_path_or_name.name}."
    module = importlib.import_module(package_path_or_name)
    if not hasattr(module, "__path__"):
        return None
    return Path(module.__path__[0]), f"{package_path_or_name}."


class ToolRegistry(object):
    """Central registry and plugin manager for agentic tools.

    Supports:
    1. Decorator-based registration (`@registry.register_tool(...)` or `@registry.register(...)`).
    2. Dynamic filesystem plugin discovery (`discover_tools(...)`).
    3. Seamless export to LangChain `BaseTool` instances for `llm.bind_tools()`.
    4. Adapter bridging for Model Context Protocol (MCP) tool schemas.
    """

    def __init__(self, name: str = "default", inherit_global: bool = False) -> None:
        self.name = name
        self._tools: dict[str, ToolEntry] = {}
        if inherit_global and name != "global_mcp_registry":
            global_reg = get_global_registry()
            self._tools.update(global_reg._tools)
        logger.debug("Initialized ToolRegistry '%s' with %d tools", self.name, len(self._tools))

    def register(
        self,
        name: str | None = None,
        description: str | None = None,
        tags: Sequence[str] = (),
        source: str = "local",
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a Python callable or tool in the registry."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or func.__name__
            if tool_name in self._tools:
                raise ValueError(f"Tool '{tool_name}' is already registered in registry '{self.name}'.")

            tool_desc = description or inspect.getdoc(func) or f"Tool: {tool_name}"
            clean_desc = " ".join(tool_desc.strip().split())

            is_async = inspect.iscoroutinefunction(func)
            metadata = ToolMetadata(
                name=tool_name,
                description=clean_desc,
                source=source,
                tags=tuple(tags),
                is_async=is_async,
            )

            # Build LangChain StructuredTool for LLM binding
            lc_tool = StructuredTool.from_function(
                func=func if not is_async else None,
                coroutine=func if is_async else None,
                name=tool_name,
                description=clean_desc,
            )

            self._tools[tool_name] = ToolEntry(
                metadata=metadata,
                callable_func=func,
                langchain_tool=lc_tool,
            )
            logger.info("Registered tool '%s' [source=%s] in registry '%s'", tool_name, source, self.name)
            return func

        return decorator

    # Alias for backwards/test compatibility
    register_tool = register

    def register_langchain_tool(
        self,
        tool: BaseTool,
        source: str = "langchain",
        tags: Sequence[str] = (),
    ) -> None:
        """Directly register an existing LangChain BaseTool."""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered in registry '{self.name}'.")

        is_async = getattr(tool, "is_async", False)
        metadata = ToolMetadata(
            name=tool.name,
            description=tool.description,
            source=source,
            tags=tuple(tags),
            is_async=is_async,
        )

        def _invoke_lc(**kw: Any) -> Any:
            return tool.invoke(kw)

        self._tools[tool.name] = ToolEntry(
            metadata=metadata,
            callable_func=_invoke_lc,
            langchain_tool=tool,
        )
        logger.info("Registered LangChain tool '%s' in registry '%s'", tool.name, self.name)

    def register_mcp_tool(
        self,
        name: str,
        description: str,
        handler: Callable[..., Any],
        _schema: dict[str, Any] | None = None,
        tags: Sequence[str] = ("mcp",),
    ) -> None:
        """Register a remote or simulated MCP (Model Context Protocol) tool."""
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered in registry '{self.name}'.")

        is_async = inspect.iscoroutinefunction(handler)
        metadata = ToolMetadata(
            name=name,
            description=description,
            source="mcp",
            tags=tuple(tags),
            is_async=is_async,
        )

        lc_tool = StructuredTool.from_function(
            func=handler if not is_async else None,
            coroutine=handler if is_async else None,
            name=name,
            description=description,
        )

        self._tools[name] = ToolEntry(
            metadata=metadata,
            callable_func=handler,
            langchain_tool=lc_tool,
        )
        logger.info("Registered MCP tool '%s' in registry '%s'", name, self.name)

    def has_tool(self, name: str) -> bool:
        """Check if a tool with the given name is registered."""
        return name in self._tools

    def list_tool_names(self) -> list[str]:
        """List names of all registered tools."""
        return list(self._tools.keys())

    def get_tool(self, name: str) -> ToolEntry:
        """Retrieve a registered tool entry by name."""
        if name not in self._tools:
            disponibles = ", ".join(self._tools.keys())
            raise KeyError(
                f"Tool '{name}' is not registered in registry '{self.name}'. Available: {disponibles}"
            )
        return self._tools[name]

    def get_tool_metadata(self, name: str) -> ToolMetadata:
        """Retrieve metadata for a registered tool."""
        return self.get_tool(name).metadata

    def get_langchain_tool(self, name: str) -> BaseTool:
        """Retrieve the LangChain BaseTool instance for a registered tool."""
        tool = self.get_tool(name).langchain_tool
        if tool is None:
            raise ValueError(f"Tool '{name}' does not have a LangChain tool representation.")
        return tool

    def execute_tool(self, name: str, **kwargs: Any) -> Any:
        """Execute a tool dynamically by name with arguments."""
        entry = self.get_tool(name)
        logger.debug("Executing tool '%s' with args: %s", name, kwargs)
        return entry.callable_func(**kwargs)

    def list_tools(self) -> list[ToolMetadata]:
        """List metadata for all currently registered tools."""
        return [entry.metadata for entry in self._tools.values()]

    def get_langchain_tools(self) -> list[BaseTool]:
        """Export all registered tools as LangChain BaseTool instances for LLM binding."""
        tools: list[BaseTool] = []
        for entry in self._tools.values():
            if entry.langchain_tool is not None:
                tools.append(entry.langchain_tool)
        return tools

    def _sincronizar_herramientas_globales(self) -> None:
        """Sincroniza herramientas del registro global si esta instancia no es global."""
        global_reg = get_global_registry()
        if self is not global_reg:
            for tool_name, entry in global_reg._tools.items():
                if tool_name not in self._tools:
                    self._tools[tool_name] = entry

    def discover_tools(self, package_path_or_name: str | Path | None = None) -> list[str]:
        """Auto-discover and load tool modules in a package or directory.

        Iterates through the target module path and dynamically imports modules,
        triggering `@register_tool` decorators automatically.
        """
        resolucion = _resolver_directorio_paquete(package_path_or_name)
        if resolucion is None:
            return []
        target_dir, package_prefix = resolucion
        if not target_dir.exists():
            logger.warning("Discovery path '%s' does not exist", target_dir)
            return []

        discovered: list[str] = []
        for _, module_name, is_pkg in pkgutil.iter_modules([str(target_dir)]):
            if is_pkg or module_name.startswith("_") or module_name == "registry":
                continue
            full_module = f"{package_prefix}{module_name}"
            try:
                importlib.import_module(full_module)
                discovered.append(full_module)
                logger.info("Successfully discovered tool module: %s", full_module)
            except Exception as exc:
                logger.error("Failed to dynamically import tool module '%s': %s", full_module, exc)

        self._sincronizar_herramientas_globales()
        return discovered


# ── Global Singleton Instance & Helpers ──────────────────────────────────────

_GLOBAL_REGISTRY: ToolRegistry | None = None


def get_global_registry() -> ToolRegistry:
    """Return the global ToolRegistry singleton instance."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ToolRegistry(name="global_mcp_registry")
    return _GLOBAL_REGISTRY


class _DefaultRegistryProxy(object):
    """Proxy object ensuring access to the global registry singleton."""

    def __getattr__(self, name: str) -> Any:
        return getattr(get_global_registry(), name)


default_registry: Any = _DefaultRegistryProxy()


def register_tool(
    name: str | None = None,
    description: str | None = None,
    tags: Sequence[str] = (),
    source: str = "local",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Convenience decorator targeting the global default registry."""
    return get_global_registry().register(name=name, description=description, tags=tags, source=source)
