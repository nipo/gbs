"""Vivado IP Packaging Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import VivadoIpPackagePass, VivadoIpSynthesizePass


class VivadoIpBackend(BaseBackend):
    """Vivado IP packaging backend

    Creates IP-XACT packages from HDL sources using Vivado's
    ipx::package_project flow, and checks that a packaged IP
    synthesizes out of context.
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
        """Contribute the packaging and synthesis check passes

        The check reads a packaged IP. When the IP comes from HDL
        sources, the planner asks again for the check's input types and
        gets the packaging pass to chain in front of it.
        """
        passes = []

        packages = bool(output_types & {"vivado-ip-zip", "vivado-ip-dir"})
        checks = "vivado-ip-synthesis-report" in output_types
        if not (packages or checks):
            return passes

        target = config.get("target", {})
        if not target.get("part"):
            self.logger.warning("Vivado IP backend skipped: no part selected")
            return passes

        if packages:
            passes.append(VivadoIpPackagePass(config, project_config, gbs_config))
        if checks:
            passes.append(VivadoIpSynthesizePass(config, project_config, gbs_config))

        return passes
