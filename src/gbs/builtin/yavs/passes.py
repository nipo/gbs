"""yavs Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import YavsDispatcher, YavsRunDispatcher


class YavsSimulatePass(BasePass):
    """Analyze VHDL sources by library with yavs and elaborate the top

    Input types: vhdl, vhpidirect-c
    Output types: simulator, yavs-simulator
    """
    name = "yavs-simulate"
    input_types = {"vhdl", "vhpidirect-c"}
    output_types = {"simulator", "yavs-simulator"}

    def probe(self) -> str | None:
        return self.probe_tool("yavs")

    def filter_vars(self) -> dict[str, Any]:
        return {
            "purpose": "simulation",
            "vhdl_frontend": "yavs",
            "simulation_engine": "yavs",
            "vhdl_std": self.config.get("vhdl_standard", "1993"),
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        return [YavsDispatcher(
            context=context,
            vhdl_std=self.config.get("vhdl_standard", "1993"),
            tool_name=self.resolve_tool_identifier("yavs"),
        )]


class YavsRunPass(BasePass):
    """Run a yavs simulator for its log, success marker and waveform

    Input types: yavs-simulator
    Output types: simulation-log, simulation-success, waveform-fst

    Configuration options (from the output group's simulation block):
        - max_simulation_time: stop time (e.g. "100 ms", "1 us")
        - success_regex: regex that must appear in the log for success
    """
    name = "yavs-run"
    input_types = {"yavs-simulator"}
    output_types = {"waveform-fst", "simulation-log", "simulation-success"}

    def probe(self) -> str | None:
        return self.probe_tool("yavs")

    def dispatchers(self, context) -> list[Dispatcher]:
        return [YavsRunDispatcher(
            context=context,
            tool_name=self.resolve_tool_identifier("yavs"),
            config=self.config,
        )]
