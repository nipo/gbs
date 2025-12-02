"""GBS Backend Package

This package contains concrete backend implementations for the GBS build system.
Also re-exports base classes for convenience.

Built-in backends are registered as plugins via the register() function.
"""

# Re-export base classes from gbs.model.dispatcher
from ..model.dispatcher import (
    Dispatcher as Backend,
    BaseDispatcher as BaseBackend,
    DispatcherRegistry as BackendRegistry,
    run_dispatcher_iteration as run_backend_iteration,
)

# Re-export all concrete dispatchers (with backward compatible names)
from .ghdl import GHDLDispatcher
from .verilog_to_vhdl import VerilogToVHDLDispatcher
from .mem_init import MemInitDispatcher
from .gowin import GowinDispatcher

# Backward compatibility aliases
GHDLBackend = GHDLDispatcher
VerilogToVHDLBackend = VerilogToVHDLDispatcher
MemInitBackend = MemInitDispatcher
GowinBackend = GowinDispatcher

__all__ = [
    # Base classes
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
    # Concrete dispatchers (new names)
    "GHDLDispatcher",
    "VerilogToVHDLDispatcher",
    "MemInitDispatcher",
    "GowinDispatcher",
    # Backward compatible names
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

    # Register dispatchers (using new names internally)
    plugin_registry.register_backend("GHDLBackend", GHDLDispatcher)
    plugin_registry.register_backend("VerilogToVHDLBackend", VerilogToVHDLDispatcher)
    plugin_registry.register_backend("MemInitBackend", MemInitDispatcher)
    plugin_registry.register_backend("GowinBackend", GowinDispatcher)

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
