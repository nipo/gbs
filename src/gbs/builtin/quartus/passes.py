"""Altera Quartus Pass definitions"""

from __future__ import annotations
from typing import Any
from pathlib import Path

from ...base import BasePass
from ...protocol import Dispatcher
from ...utils import expand_path
from .dispatcher import QuartusDispatcher


class QuartusSynthesizePass(BasePass):
    """Pass that synthesizes HDL to Altera FPGA bitstream

    This pass uses Quartus Prime tools to:
    - Generate project files (.qpf, .qsf)
    - Run Analysis & Synthesis (quartus_map)
    - Run Fitter / Place & Route (quartus_fit)
    - Run Timing Analysis (quartus_sta)
    - Run Assembler / Generate bitstream (quartus_asm)

    Input types: vhdl, verilog, quartus-sdc, quartus-pin-assignment, quartus-qsys,
                 quartus-qsys-script
    Output types: quartus-sof, quartus-jam, quartus-rbf,
                  quartus-synthesis-report, quartus-pnr-report, quartus-project

    quartus-project is the odd one out: it's just the generated .qpf/.qsf,
    and requesting it alone skips the whole synthesis pipeline (see
    QuartusDispatcher._task_graph_create).
    """
    name = "quartus-synthesize"
    input_types = {
        "vhdl", "verilog", "quartus-sdc", "quartus-pin-assignment",
        "quartus-qsys", "quartus-qsys-script",
    }
    output_types = {
        "quartus-sof",
        "quartus-jam",
        "quartus-rbf",
        "quartus-synthesis-report",
        "quartus-pnr-report",
        "quartus-project",
    }

    def __init__(self,
                 config: dict[str, Any],
                 project_config: dict[str, Any] | None = None,
                 gbs_config: 'GBSConfig | None' = None):
        super().__init__(config, project_config, gbs_config)

        self._quartus_bin = None
        target = self.config.get("target", {})
        self._part = target.get("part")
        tool_name = self.resolve_tool_identifier("quartus")

        if self.gbs_config:
            tool_config = self.gbs_config.get_tool(tool_name)
            if tool_config and "path" in tool_config.config:
                self._quartus_bin = expand_path(tool_config.config["path"]) / "quartus" / "bin"

        self._part_info = {}
        if self._part and self._quartus_bin:
            self._part_info = _query_part_info(self._quartus_bin, self._part)

    def filter_vars(self) -> dict[str, Any]:
        """Contribute canonical filter variables for a Quartus build.

        Quartus runs synthesis, place-and-route and bitstream
        generation in a single flow.
        """
        vhdl_std = self.config.get("vhdl_standard", "1993")

        filter_vars: dict[str, Any] = {
            "purpose": "synthesis",
            "vendor": "altera",
            "vhdl_frontend": "quartus",
            "verilog_frontend": "quartus",
            "synthesis_engine": "quartus",
            "pnr_engine": "quartus",
            "bitstream_engine": "quartus",
            "vhdl_std": vhdl_std,
        }

        if self._part:
            filter_vars["part"] = self._part
            filter_vars["die"] = self._part

        if "family" in self._part_info:
            filter_vars["family"] = self._part_info["family"]

        if "device" in self._part_info:
            filter_vars["die"] = self._part_info["device"]

        return filter_vars

    def dispatchers(self, context) -> list[Dispatcher]:
        """Create Quartus dispatcher for execution"""
        tool = self.resolve_tool_identifier("quartus")
        vhdl_std = self.config.get("vhdl_standard", "1993")
        target = self.config["target"]

        return [QuartusDispatcher(
            context=context,
            vhdl_std=vhdl_std,
            target=target,
            tool=tool,
        )]


def _query_part_info(quartus_bin: Path, part: str) -> dict[str, str]:
    """Query device info from quartus_sh.

    Uses get_part_info TCL command to extract family and device
    name directly from the Quartus device database.

    Returns:
        Dict with 'family' and 'device' keys, or empty if query fails.
    """
    import subprocess

    import tempfile

    quartus_sh = quartus_bin / "quartus_sh"
    if not quartus_sh.exists():
        return {}

    tcl_script = (
        f'package require ::quartus::device\n'
        f'set f [get_part_info -family {part}]\n'
        f'set d [get_part_info -device {part}]\n'
        f'puts "family:$f"\n'
        f'puts "device:$d"\n'
    )

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tcl', delete=False) as f:
            f.write(tcl_script)
            tcl_path = f.name

        result = subprocess.run(
            [str(quartus_sh), "-t", tcl_path],
            capture_output=True, text=True, timeout=30,
        )

        Path(tcl_path).unlink(missing_ok=True)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return {}

    info = {}
    for line in result.stdout.splitlines():
        if line.startswith("family:"):
            # Strip braces from TCL output (e.g., "{Cyclone 10 LP}" -> "Cyclone 10 LP")
            info["family"] = line[7:].strip().strip("{}")
        elif line.startswith("device:"):
            info["device"] = line[7:].strip().strip("{}")

    return info
