"""Vivado IP Packaging Dispatcher"""

from __future__ import annotations
from pathlib import Path
from typing import Any

from ...build.context import BuildContext
from ...build.task import ResourceTypology
from .task import VivadoIpPackageTask
from ..vivado.base import VivadoDispatcherBase


# Input file types accepted by the IP packaging dispatcher
ACCEPTED_INPUT_TYPES = {
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


class VivadoIpDispatcher(VivadoDispatcherBase):
    """Vivado IP packaging dispatcher

    Workflow:
      First process() call:
        - Creates IP packaging task with output resources
        - Registers all output types (zip and/or dir)

      Subsequent process() calls:
        - Attaches any pending files of accepted types to the task
    """

    def __init__(
        self,
        context: BuildContext,
        vhdl_std: str = "2008",
        vivado_tool: str = "vivado",
        target: dict[str, str] | None = None,
        ip_config: dict[str, Any] | None = None,
    ):
        super().__init__(
            context,
            "vivado-ip",
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
        )
        self.ip_config = ip_config or {}
        self._package_task: VivadoIpPackageTask | None = None

    async def process(self) -> None:
        """Process input files"""
        if self._package_task is None:
            await self._create_package_task()

        if self._package_task is not None:
            self.inputs_attach(self._package_task, ACCEPTED_INPUT_TYPES)

    async def _create_package_task(self) -> None:
        """Create the IP packaging task with output resources"""
        session = self.session_get()
        part = self.target.get("part")

        if not part:
            raise RuntimeError("Vivado IP backend requires 'part' in target configuration")

        # Create output resources based on what's requested
        outputs = []

        for dest in self.context.filter_pending(file_type="vivado-ip-zip"):
            outputs.append(dest)

        for dest in self.context.filter_pending(file_type="vivado-ip-dir"):
            outputs.append(dest)

        if not outputs:
            return

        self._package_task = VivadoIpPackageTask(
            dispatcher=self,
            session=session,
            part=part,
            ip_config=self.ip_config,
            inputs=[],
            outputs=outputs,
        )
        self.attach_definition_dependencies(self._package_task)

        self.info(f"Created Vivado IP packaging task for part {part}")
