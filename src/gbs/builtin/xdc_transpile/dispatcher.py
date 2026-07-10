"""XDC transpile dispatcher.

Takes the Vivado XDC source files plus the synthesized netlist and
emits a nextpnr-xilinx constraint file. The netlist is read for its
top-level port list but is left in the pending queue for place-and-route
to consume.
"""

from __future__ import annotations

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class XdcTranspileDispatcher(BaseDispatcher):
    """Emit a nextpnr constraint file from Vivado XDC sources."""

    def __init__(
        self,
        context: BuildContext,
        yosys_tool: str,
        port_properties: frozenset[str],
    ):
        super().__init__(context, "xdc-transpile", tool_name=yosys_tool)
        self.port_properties = port_properties
        self._yosys_executable: str | None = None
        self._task: task.Transpile | None = None

    def get_yosys_executable(self) -> str:
        if self._yosys_executable is None:
            executable = self.get_tool_option("executable", "yosys")
            self._yosys_executable = str(expand_path(executable))
            self.debug(f"Using yosys executable: {self._yosys_executable}")
        return self._yosys_executable

    async def process(self) -> None:
        if self._task is not None:
            return

        netlists = list(self.context.filter_pending(file_type=["xilinx-netlist-json"]))
        if not netlists:
            # Synthesis has not produced the netlist yet.
            return

        xdc_inputs = list(self.context.filter_pending(file_type=["xilinx-xdc"]))
        if not xdc_inputs:
            # No Vivado constraints to translate.
            return

        if len(netlists) > 1:
            self.warning(
                f"Multiple netlists found, using first for port extraction: "
                f"{netlists[0].path}"
            )
        netlist = netlists[0]

        topcell = self.context.get_topcell()
        topcell_library = self.context.get_topcell_library()
        output = self.context.get_resource(
            self.context.output_path / f"{topcell}.nextpnr.xdc",
            file_type="nextpnr-xdc",
            library=topcell_library,
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )
        self.context.add_pending(output)

        self._task = task.Transpile(
            dispatcher=self,
            netlist=netlist,
            xdc_inputs=xdc_inputs,
            output=output,
            port_properties=self.port_properties,
        )
        self.attach_definition_dependencies(self._task)
