"""GBS Backend Package

This package contains concrete backend implementations for the GBS build system.
Also re-exports base classes for convenience.

Built-in backends are registered as plugins via the register() function.
"""

# Re-export base classes from gbs.model.backend
from ..model.backend import (
    Backend,
    BaseBackend,
    BackendRegistry,
    run_backend_iteration,
)

# Re-export all concrete backends
from .ghdl import GHDLBackend
from .verilog_to_vhdl import VerilogToVHDLBackend
from .mem_init import MemInitBackend
from .gowin import GowinBackend

__all__ = [
    # Base classes
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
    # Concrete backends
    "GHDLBackend",
    "VerilogToVHDLBackend",
    "MemInitBackend",
    "GowinBackend",
    # Plugin interface
    "register",
]


def register(plugin_registry):
    """Register built-in backends as plugins

    This allows built-in backends to be treated the same as external plugins.
    Also contributes default tool configurations.

    Args:
        plugin_registry: PluginRegistry instance
    """
    from ..config import ToolConfig

    # Register backends
    plugin_registry.register_backend("GHDLBackend", GHDLBackend)
    plugin_registry.register_backend("VerilogToVHDLBackend", VerilogToVHDLBackend)
    plugin_registry.register_backend("MemInitBackend", MemInitBackend)
    plugin_registry.register_backend("GowinBackend", GowinBackend)

    # Contribute default tools
    default_tools = [
        # GHDL from PATH
        ToolConfig(
            name="ghdl",
            variant="system",
            config={"executable": "ghdl"}
        ),
        # GCC from PATH
        ToolConfig(
            name="gcc",
            variant="system",
            config={"executable": "gcc"}
        ),
    ]

    plugin_registry.contribute_tool_defaults(default_tools)
