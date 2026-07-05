"""Vivado IP Packaging Pass definitions"""

from __future__ import annotations
from typing import Any

from ...base import BasePass
from ...protocol import Dispatcher
from .dispatcher import VivadoIpDispatcher


class VivadoIpPackagePass(BasePass):
    """Pass that packages HDL into a Vivado IP-XACT package

    This pass uses Vivado tools to:
    - Create a project with HDL sources
    - Run ipx::package_project to create component.xml
    - Apply metadata and customization scripts
    - Generate IP zip or directory

    Input types:
        - vhdl, verilog: HDL sources for the IP
        - vivado-bus-definition: Custom bus interface XML definitions
        - vivado-ip-customization-tcl: Post-packaging TCL scripts
        - xilinx-xdc: Constraint files to include in the IP

    Output types:
        - vivado-ip-zip: Packaged IP as a zip archive
        - vivado-ip-dir: Packaged IP as a directory
    """
    name = "vivado-ip-package"
    input_types = {
        "vhdl",
        "verilog",
        "vivado-bus-definition",
        "vivado-ip-repository",
        "vivado-ip-customization-tcl",
        "xilinx-xdc",
    }
    output_types = {
        "vivado-ip-zip",
        "vivado-ip-dir",
    }

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for IP packaging.

        Packaging needs synthesizable HDL, so ``purpose`` is set to
        ``synthesis`` and the frontends and synthesis engine point to
        Vivado.
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")

        return {
            "purpose": "synthesis",
            "vendor": "xilinx",
            "vhdl_frontend": "vivado",
            "verilog_frontend": "vivado",
            "synthesis_engine": "vivado",
            "vhdl_std": vhdl_std,
        }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Vivado IP dispatcher for execution"""
        vivado_tool = self.resolve_tool_identifier("vivado")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config.get("target", {})

        return [VivadoIpDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
            ip_config=self.config,
        )]
