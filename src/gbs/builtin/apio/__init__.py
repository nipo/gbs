"""Apio Toolchain Plugin

Registers a `type: apio` toolchain provider that scans an apio package
tree (default ~/.apio/packages) and yields ToolConfig entries for any
GBS-visible tool it finds.

Example config::

    toolchains:
      - type: apio
        # root: ~/.apio/packages    (default)
"""

from ...base import BasePlugin
from .provider import ApioToolchainProvider


class ApioPlugin(BasePlugin):
    def __init__(self):
        super().__init__(
            name="gbs.builtin.apio",
            description="Discover tools installed via apio (~/.apio/packages)",
            version="1.0.0",
        )

    def enumerate_toolchain_providers(self):
        return {"apio": ApioToolchainProvider}


def gbs_register():
    return ApioPlugin()


__all__ = ["gbs_register"]
