"""yavs Backend implementation"""

from __future__ import annotations
from typing import Any

from ...base import BaseBackend
from .passes import YavsSimulatePass, YavsRunPass


class YavsBackend(BaseBackend):
    """yavs Backend for VHDL simulation

    Configuration options:
        - vhdl_standard: VHDL standard ("1993", "2008" or "2019")
        - max_simulation_time: run stop time (e.g. "100 ms")
        - success_regex: regex the simulation log must match for success
    """

    RUN_OUTPUT_TYPES = {"simulation-log", "simulation-success", "waveform-fst"}

    def __init__(self):
        super().__init__("gbs.builtin.yavs")

    def contribute_passes(
        self,
        config: dict[str, Any],
        output_types: set[str],
        project_config: dict[str, Any] | None = None,
        gbs_config: 'GBSConfig | None' = None
    ) -> list['Pass']:
        passes = []

        if output_types & {"simulator", "yavs-simulator"}:
            passes.append(YavsSimulatePass(config, project_config, gbs_config))

        if output_types & self.RUN_OUTPUT_TYPES:
            passes.append(YavsRunPass(config, project_config, gbs_config))

        return passes
