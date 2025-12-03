"""Plugin system for GBS extensibility

The plugin system provides a unified way to extend GBS with:
- Backends (planning + execution)
- Repository parsers (source definition loaders)

Plugins are discovered automatically from:
- Built-in modules (gbs.builtin.*)
- External namespace packages (gbs.plugin.*)
"""

from .plugin import Plugin
from .loader import PluginRegistry, get_plugin_registry, reset_plugin_registry

__all__ = ["Plugin", "PluginRegistry", "get_plugin_registry", "reset_plugin_registry"]
