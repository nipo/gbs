"""Builtin repository loader plugin

Provides the standard YAML-based repository format.
"""

from ...base import BasePlugin


class BuiltinRepositoryPlugin(BasePlugin):
    """Plugin providing YAML repository loader"""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.repository",
            description="Built-in YAML repository format loader",
            version="1.0.0"
        )

    def enumerate_repository_parsers(self) -> dict[str, type]:
        """Provide YAML repository loader

        Returns:
            Dict mapping "yaml" to YAMLRepositoryLoader class
        """
        from .yaml_loader import YAMLRepositoryLoader
        return {
            "yaml": YAMLRepositoryLoader
        }


__all__ = ["BuiltinRepositoryPlugin"]
