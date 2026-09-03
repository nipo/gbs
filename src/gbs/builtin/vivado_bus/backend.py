"""Vivado bus definition backend."""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import VivadoBusPackagePass, VivadoBusTranspilePass


class VivadoBusBackend(BaseBackend):
    """Backend generating Vivado IP-XACT bus definitions from YAML.

    Contributes the transpile pass whenever ``vivado-bus-definition``
    files are wanted — either requested directly or pulled in as an
    input of the bus package pass or of the Vivado IP packaging pass —
    and the package pass when a bus interface archive or directory is
    requested.
    """

    def __init__(self):
        super().__init__("gbs.builtin.vivado-bus")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: "GBSConfig | None" = None,
    ) -> list["Pass"]:
        passes = []
        if output_types & {"vivado-bus-zip", "vivado-bus-dir"}:
            passes.append(VivadoBusPackagePass(config, project_config,
                                               gbs_config))
        if "vivado-bus-definition" in output_types:
            passes.append(VivadoBusTranspilePass(config, project_config,
                                                 gbs_config))
        return passes
