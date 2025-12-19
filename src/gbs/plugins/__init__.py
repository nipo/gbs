"""Plugin system for GBS extensibility

The plugin system provides a unified way to extend GBS with:
- Backends (planning + execution)
- Repository parsers (source definition loaders)

Plugins are discovered automatically from:
- Built-in modules (gbs.builtin.*)
- External namespace packages (gbs.plugin.*)

For Plugin protocol and base class, import from gbs.protocol and gbs.base
"""

from .loader import PluginRegistry, get_plugin_registry, reset_plugin_registry

__all__ = ["PluginRegistry", "get_plugin_registry", "reset_plugin_registry"]
