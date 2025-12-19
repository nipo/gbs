"""Built-in YAML repository loader plugin

Provides the standard YAML-based repository format loader.
"""

from ...base import BasePlugin


def gbs_register() -> BasePlugin:
    """Register the builtin YAML repository loader plugin"""
    from .plugin import BuiltinRepositoryPlugin
    return BuiltinRepositoryPlugin()


__all__ = ["gbs_register"]
