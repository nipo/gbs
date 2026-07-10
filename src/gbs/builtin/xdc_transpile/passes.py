"""XDC transpile pass definitions."""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import XdcTranspileDispatcher
from .transpiler import DEFAULT_PORT_PROPERTIES


class XdcTranspilePass(BasePass):
    """Translate Vivado XDC into nextpnr-xilinx constraints.

    nextpnr-xilinx reads a constraint file with ``--xdc`` but accepts
    only a small subset of Vivado's XDC and does not expand wildcard
    port patterns. This pass consumes the Vivado ``xilinx-xdc`` sources
    and the synthesized netlist and emits a ``nextpnr-xdc`` file with
    port patterns resolved against the real ports and only nextpnr-safe
    properties retained.

    The netlist is read for its port list but not consumed, so
    place-and-route still receives it.

    Configuration:
        port_properties: List of ``set_property`` names emitted into the
            nextpnr file, replacing the built-in set (PACKAGE_PIN, LOC,
            IOSTANDARD, SLEW, DRIVE, PULLUP, PULLDOWN). Case-insensitive.
        extra_port_properties: Names added to the set in force. Use this
            to opt into further properties a given nextpnr build accepts.

    Input types: xilinx-netlist-json, xilinx-xdc
    Output types: nextpnr-xdc
    """
    name = "xdc-transpile"
    input_types = {"xilinx-netlist-json", "xilinx-xdc"}
    output_types = {"nextpnr-xdc"}
    part_prefix = ("xc7",)

    def probe(self) -> str | None:
        target = self.config.get("target") or {}
        part = (target.get("part") or "").lower()
        if not any(part.startswith(p) for p in self.part_prefix):
            return (
                f"target part {part!r} is not 7-series; the XDC transpiler "
                f"only feeds the nextpnr-xilinx flow"
            )
        return self.probe_tool("yosys")

    def filter_vars(self) -> dict[str, Any]:
        from .. import xilinx_part
        ret: dict[str, Any] = {"purpose": "synthesis", "vendor": "xilinx"}
        target = self.config.get("target", {})
        part = target.get("part")
        if part:
            ret["part"] = part
            ret.update(xilinx_part.filter_vars(part))
        return ret

    def port_properties(self) -> frozenset[str]:
        """Resolve the emitted property set from configuration."""
        override = self.config.get("port_properties")
        base = set(override) if override is not None else set(DEFAULT_PORT_PROPERTIES)
        base |= set(self.config.get("extra_port_properties", []))
        return frozenset(p.upper() for p in base)

    def dispatchers(self, context) -> list[Dispatcher]:
        yosys_tool = self.resolve_tool_identifier("yosys")
        return [XdcTranspileDispatcher(
            context=context,
            yosys_tool=yosys_tool,
            port_properties=self.port_properties(),
        )]
