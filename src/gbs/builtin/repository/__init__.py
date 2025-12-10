"""Built-in YAML repository loader plugin

Provides the standard YAML-based repository format loader.
"""

from ...plugins.plugin import Plugin


def gbs_register() -> Plugin:
    """Register the builtin YAML repository loader plugin"""
    from .plugin import BuiltinRepositoryPlugin
    return BuiltinRepositoryPlugin()


__all__ = ["gbs_register"]
