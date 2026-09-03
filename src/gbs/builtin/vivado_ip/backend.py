"""Vivado IP Packaging Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import VivadoIpPackagePass


class VivadoIpBackend(BaseBackend):
    """Vivado IP packaging backend

    Creates IP-XACT packages from HDL sources using Vivado's
    ipx::package_project flow.
    """

    def __init__(self):
        super().__init__("gbs.builtin.vivado-ip")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list:
        """Contribute Vivado IP packaging passes based on desired outputs"""
        passes = []

        ip_types = {"vivado-ip-zip", "vivado-ip-dir"}
        if output_types & ip_types:
            target = config.get("target", {})
            part = target.get("part")
            if not part:
                self.logger.warning("Vivado IP backend skipped: no part selected")
                return []
            passes.append(VivadoIpPackagePass(config, project_config, gbs_config))

        return passes
