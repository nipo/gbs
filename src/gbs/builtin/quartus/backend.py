"""Altera Quartus Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import QuartusSynthesizePass


class QuartusBackend(BaseBackend):
    """Altera Quartus FPGA synthesis backend

    Provides Quartus synthesis pass and dispatcher for executing FPGA builds.
    Supports Quartus Prime Lite and Standard editions.
    """

    def __init__(self):
        super().__init__("gbs.builtin.quartus")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list:
        """Contribute Quartus passes based on desired outputs"""
        passes = []

        quartus_types = QuartusSynthesizePass.output_types
        if output_types & quartus_types:
            target = config.get("target", {})
            part = target.get("part")
            if not part:
                self.warning("Quartus backend skipped: no part selected")
                return []
            passes.append(QuartusSynthesizePass(config, project_config, gbs_config))

        return passes
