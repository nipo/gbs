"""Vivado IP Packaging Dispatcher"""

from __future__ import annotations
from pathlib import Path
from typing import Any

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from ...utils import expand_path, resolve_tool_exe
from .task import VivadoIpPackageTask
from ..vivado.vivado_tcl import Session


# Input file types accepted by the IP packaging dispatcher
ACCEPTED_INPUT_TYPES = {
    "vhdl",
    "verilog",
    "vivado-bus-definition",
    "vivado-ip-customization-tcl",
    "vivado-bd-tcl",
    "vivado-xgui-tcl",
    "xilinx-xdc",
}


class VivadoIpDispatcher(BaseDispatcher):
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
        super().__init__(context, "vivado-ip", tool_name=vivado_tool)
        self.vhdl_std = vhdl_std
        self.target = target or {}
        self.ip_config = ip_config or {}
        self._session: Session | None = None
        self._package_task: VivadoIpPackageTask | None = None

    def _get_session(self) -> Session:
        """Get or create shared Vivado TCL session"""
        if self._session is None:
            vivado_path = expand_path(self.get_tool_option("path"))

            try:
                vivado_exe = resolve_tool_exe(vivado_path / "bin" / "vivado")
            except FileNotFoundError:
                raise RuntimeError(f"Vivado not found at {vivado_path}")

            self._session = Session(
                argv=[
                    str(vivado_exe),
                    "-mode", "tcl",
                    "-nojournal",
                    "-nolog",
                ],
                cwd=self.context.output_path,
                env=self.tool_env or None,
                use_pty=True,
            )

        return self._session

    async def process(self) -> None:
        """Process input files"""
        if self._package_task is None:
            await self._create_package_task()

        await self._attach_pending_inputs()

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _create_package_task(self) -> None:
        """Create the IP packaging task with output resources"""
        session = self._get_session()
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

    async def _attach_pending_inputs(self) -> None:
        """Attach any pending files of accepted types to the package task"""
        if self._package_task is None:
            return

        existing_paths = {r.path for r in self._package_task.inputs}

        for file_type in ACCEPTED_INPUT_TYPES:
            for source in list(self.context.filter_pending(file_type=file_type)):
                if source.path in existing_paths:
                    continue

                self.debug(f"Attaching input: {source.path} (type={file_type})")
                self._package_task.add_input(source)
