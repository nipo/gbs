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
from .gowin import GowinDispatcher

# Backward compatibility aliases
GHDLBackend = GHDLDispatcher
GowinBackend = GowinDispatcher

__all__ = [
    # Base classes
    "Backend",
    "BaseBackend",
    "BackendRegistry",
    "run_backend_iteration",
    # Concrete dispatchers (new names)
    "GHDLDispatcher",
    "GowinDispatcher",
    # Backward compatible names
    "GHDLBackend",
    "GowinBackend",
    # Plugin interface
    "register",
]


def register(plugin_registry):
    """Register built-in backend default tool configurations

    Contributes default tool configurations for built-in backends.

    Args:
        plugin_registry: PluginRegistry instance
    """
    from ..config import ToolConfig

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
