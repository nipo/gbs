"""Vivado IP packaging and synthesis check pass definitions"""

from __future__ import annotations
from typing import Any

from ...protocol import Dispatcher
from .dispatcher import VivadoIpCheckDispatcher, VivadoIpDispatcher
from ..vivado.base import VivadoPassBase


class VivadoIpPackagePass(VivadoPassBase):
    """Pass that packages HDL into a Vivado IP-XACT package

    This pass uses Vivado tools to:
    - Create a project with HDL sources
    - Run ipx::package_project to create component.xml
    - Apply metadata and customization scripts
    - Generate IP zip or directory

    Input types:
        - vhdl, verilog: HDL sources for the IP
        - vivado-bus-definition: Custom bus interface XML definitions
        - vivado-bus-zip: Archive of custom bus interface XML definitions
        - vivado-ip-customization-tcl: Post-packaging TCL scripts
        - vivado-bd-tcl: Bd instance param propagation TCL scripts
        - vivado-xgui-tcl: GUI propagation TCL scripts
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
        "vivado-bus-zip",
        "vivado-ip-repository",
        "vivado-ip-customization-tcl",
        "vivado-bd-tcl",
        "vivado-xgui-tcl",
        "xilinx-xdc",
    }
    output_types = {
        "vivado-ip-zip",
        "vivado-ip-dir",
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


class VivadoIpSynthesizePass(VivadoPassBase):
    """Pass checking that a packaged IP synthesizes out of context

    The IP is instantiated by VLNV in a scratch project, its targets are
    generated and it is synthesized with synth_ip, the way a user design
    would get it from the IP catalog. This exercises the package itself:
    file groups, libraries, customization scripts and supported families.

    Customization parameters come from the `synthesis_check_config`
    backend configuration mapping.

    Input types:
        - vivado-ip-zip, vivado-ip-dir: The packaged IP under check
        - vivado-ip-repository: IP the checked one depends on
        - vivado-bus-definition, vivado-bus-zip: Custom bus interface
          definitions the IP refers to

    Output types:
        - vivado-ip-synthesis-report: Utilization report of the IP
    """
    name = "vivado-ip-synthesize"
    input_types = {
        "vivado-ip-zip",
        "vivado-ip-dir",
        "vivado-ip-repository",
        "vivado-bus-definition",
        "vivado-bus-zip",
    }
    output_types = {
        "vivado-ip-synthesis-report",
    }

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create the synthesis check dispatcher"""
        vivado_tool = self.resolve_tool_identifier("vivado")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config.get("target", {})

        return [VivadoIpCheckDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
            params=self.config.get("synthesis_check_config") or {},
        )]
