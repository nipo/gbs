"""openxc7 Pass definitions."""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import Openxc7Dispatcher
from .. import xilinx_part


class Openxc7Pass(BasePass):
    """Convert FASM (nextpnr-xilinx output) to a Xilinx bitstream.

    Runs two chained subprocesses: fasm2frames (FASM -> frame deltas)
    and xc7frames2bit (frames -> .bit).

    Input types: nextpnr-fasm
    Output types: xilinx-bitstream
    """
    name = "openxc7"
    input_types = {"nextpnr-fasm"}
    output_types = {"bitstream", "xilinx-bitstream"}

    def probe(self) -> str | None:
        target = self.config.get("target") or {}
        part = (target.get("part") or "").lower()
        if not part.startswith("xc7"):
            return f"target part {part!r} is not 7-series; openxc7 only supports xc7"
        return self.probe_tool("fasm2frames")

    def filter_vars(self) -> dict[str, Any]:
        ret: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "xilinx",
            "bitstream_engine": "openxc7",
        }
        target = self.config.get("target", {})
        part = target.get("part")
        if part:
            ret["part"] = part
            ret.update(xilinx_part.filter_vars(part))
        return ret

    def dispatchers(self, context) -> list[Dispatcher]:
        target = self.config.get("target", {})
        part = target.get("part", "")
        fasm2frames_tool = self.resolve_tool_identifier("fasm2frames")
        xc7frames2bit_tool = self.config.get("xc7frames2bit_tool", "xc7frames2bit")
        return [Openxc7Dispatcher(
            context=context,
            part=part,
            fasm2frames_tool=fasm2frames_tool,
            xc7frames2bit_tool=xc7frames2bit_tool,
        )]
