"""GHDL Backend implementation"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...backend.protocol import BaseBackend
from ...planner.passes import Pass
from .passes import GHDLAnalyzePass, GHDLSimulatePass, GHDLRunPass

class GHDLBackend(BaseBackend):
    """GHDL Backend for VHDL analysis and simulation

    Provides two passes:
    - GHDLAnalyzePass: Analyzes VHDL to library intermediates (ghdl-cf)
    - GHDLSimulatePass: Creates simulator executable from libraries

    Configuration options:
        - vhdl_standard: VHDL standard (e.g., "1993", "2008", "2019")
        - ghdl_tool: Tool identifier for lookup (default: "ghdl")
    """

    def __init__(self):
        super().__init__("gbs.builtin.ghdl")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list[Pass]:
        """Contribute GHDL passes based on desired outputs

        Args:
            config: Backend configuration
            output_types: Set of desired output types
            project_config: Project-level configuration (raw_config)
            gbs_config: GBS configuration (tools, etc.)

        Returns:
            List of Pass instances that can help produce the outputs
        """
        passes = []

        # Contribute analysis pass if ghdl-cf intermediates are needed
        if "ghdl-cf" in output_types:
            passes.append(GHDLAnalyzePass(config, project_config, gbs_config))

        # Contribute simulation pass if simulator executable is needed
        # The analysis pass will be pulled in automatically via input dependencies
        if "ghdl-simulator" in output_types or "simulator" in output_types:
            passes.append(GHDLSimulatePass(config, project_config, gbs_config))

        # Contribute run pass if waveform or simulation log outputs are needed
        waveform_types = {"waveform-vcd", "waveform-ghw", "waveform-fst"}
        if waveform_types & output_types or "simulation-log" in output_types:
            passes.append(GHDLRunPass(config, project_config, gbs_config))

        return passes
