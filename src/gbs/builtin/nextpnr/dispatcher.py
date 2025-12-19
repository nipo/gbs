"""nextpnr Dispatcher - FPGA place-and-route"""

from __future__ import annotations
from pathlib import Path

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class NextpnrDispatcher(BaseDispatcher):
    """nextpnr place-and-route dispatcher

    This dispatcher takes a JSON netlist and:
    1. Runs nextpnr to place and route the design
    2. Generates ASCII bitstream (.asc)
    """

    def __init__(
            self,
            context: BuildContext,
            nextpnr_tool: str = "nextpnr-ice40",
            part: str = "",
            package: str = "",
    ):
        super().__init__(context, "nextpnr-ice40", tool_name=nextpnr_tool)
        self.nextpnr_tool = nextpnr_tool
        self.part = part
        self.package = package
        self._nextpnr_executable: str | None = None
        self._pnr_task: task.PlaceAndRoute | None = None

    def _get_nextpnr_executable(self) -> str:
        """Get nextpnr executable path (cached)

        Returns:
            Executable path
        """
        if self._nextpnr_executable is None:
            tool_config = self.context.get_tool(self.nextpnr_tool, required=False)
            if tool_config:
                executable = tool_config.get("executable", "nextpnr-ice40")
            else:
                executable = "nextpnr-ice40"
            self._nextpnr_executable = str(expand_path(executable))
            self.debug(f"Using nextpnr executable: {self._nextpnr_executable}")

        return self._nextpnr_executable

    async def process(self) -> None:
        """Run place-and-route using nextpnr"""
        # Create PnR task if possible
        if not self._pnr_task:
            # Find the JSON netlist input
            netlist_resources = list(self.context.filter_pending(file_type=["ice40-netlist-json"]))

            if not netlist_resources:
                # No netlist yet, wait for next process() call
                return

            if len(netlist_resources) > 1:
                self.warning(f"Multiple netlists found, using first: {netlist_resources[0].path}")

            netlist_resource = netlist_resources[0]
            topcell = self.context.get_topcell()
            topcell_library = self.context.get_topcell_library()

            # Create output ASC file
            asc_path = self.context.output_path / f"{topcell}.asc"
            asc_resource = self.context.get_resource(
                asc_path,
                file_type="ice40-asc",
                library=topcell_library,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )
            # Add ASC to pending queue
            self.context.add_pending(asc_resource)

            # Create PnR task with netlist only initially
            self._pnr_task = task.PlaceAndRoute(
                dispatcher=self,
                inputs=[netlist_resource],
                outputs=[asc_resource],
            )

            # Remove netlist from pending (consumed by PnR)
            dependents = self.context.remove_pending(netlist_resource.path)
            for dep in dependents:
                self._pnr_task.dependency_add(dep)

        # On every process() call, check for new PCF files
        pcf_resources = list(self.context.filter_pending(file_type=["ice40-pcf"]))
        for pcf_resource in pcf_resources:
            self.info(f"Adding PCF constraint: {pcf_resource.path.name}")
            self._pnr_task.inputs.append(pcf_resource)

            # Remove PCF from pending
            pcf_dependents = self.context.remove_pending(pcf_resource.path)
            for dep in pcf_dependents:
                self._pnr_task.dependency_add(dep)
            self._pnr_task.dependency_add(pcf_resource)
