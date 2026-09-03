"""Vivado IP-XACT bus definition plugin."""

from ...base import BasePlugin
from .backend import VivadoBusBackend


class VivadoBusPlugin(BasePlugin):
    """Plugin generating Vivado IP-XACT bus definitions from YAML."""

    def __init__(self):
        super().__init__(
            name="gbs.builtin.vivado-bus",
            description="Generate Vivado IP-XACT bus definitions from YAML",
            version="1.0.0",
        )

    def enumerate_backends(self):
        return [VivadoBusBackend()]


def gbs_register():
    """Plugin registration entry point."""
    return VivadoBusPlugin()


__all__ = ["VivadoBusPlugin", "VivadoBusBackend", "gbs_register"]
