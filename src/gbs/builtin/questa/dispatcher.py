"""QuestaSim/ModelSim Dispatcher

Dispatcher implementation for QuestaSim/ModelSim GUI project generation.
"""

from __future__ import annotations
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from ...utils import expand_path
from .task import GenerateQuestaProject, GenerateGuiScript


# Accepted input file types
ACCEPTED_INPUT_TYPES = {"vhdl", "verilog"}


class QuestaDispatcher(BaseDispatcher):
    """QuestaSim/ModelSim GUI project dispatcher

    Workflow:
      First process() call:
        - Creates MPF project generation task
        - Creates GUI launcher script generation task
        - Attaches output resources

      Subsequent process() calls:
        - Attaches any pending HDL files to the project task

    Priority: 500 (main compilation)
    """

    def __init__(
        self,
        context: BuildContext,
        vhdl_std: str = "1993",
        questa_tool: str = "questa",
    ):
        super().__init__(context, "questa", tool_name=questa_tool, priority=500)
        self.vhdl_std = vhdl_std
        self.questa_tool = questa_tool
        self._project_task: GenerateQuestaProject | None = None
        self._gui_task: GenerateGuiScript | None = None

    def _get_vsim_executable(self) -> Path:
        """Get vsim executable path

        Returns:
            Path to vsim executable

        Raises:
            RuntimeError: If tool is not configured
        """
        tool_config = self.context.get_tool(self.questa_tool)
        prefix = expand_path(tool_config.get("path", tool_config.get("prefix", "")))

        if prefix:
            vsim_path = prefix / "bin" / "vsim"
        else:
            # Fall back to expecting vsim in PATH
            vsim_path = Path("vsim")

        return vsim_path

    async def process(self) -> None:
        """Process HDL sources

        On first call, creates project and GUI launcher tasks.
        On every call, attaches any pending HDL files to the project task.
        """
        if self._project_task is None:
            await self._create_tasks()

        # Attach any pending HDL files
        await self._attach_pending_inputs()

    async def _create_tasks(self) -> None:
        """Create MPF project and GUI launcher tasks"""
        vsim_executable = self._get_vsim_executable()
        topcell = self.context.get_topcell()

        # Create MPF project file resource
        mpf_path = self.context.output_path / f"{topcell}.mpf"
        mpf_resource = self.context.get_resource(
            mpf_path,
            file_type="questa-project",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name,
        )

        # Create GUI launcher script resource
        gui_script_path = self.context.output_path / "questa_gui.sh"
        gui_resource = self.context.get_resource(
            gui_script_path,
            file_type="questa-gui-launcher",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name,
        )

        # Create project generation task (no inputs yet, added dynamically)
        self._project_task = GenerateQuestaProject(
            dispatcher=self,
            vhdl_std=self.vhdl_std,
            inputs=[],
            outputs=[mpf_resource],
        )

        # Create GUI launcher task (depends on MPF file)
        self._gui_task = GenerateGuiScript(
            dispatcher=self,
            vsim_executable=vsim_executable,
            inputs=[mpf_resource],
            outputs=[gui_resource],
        )

        # Add dependency: GUI task depends on project task output
        self._gui_task.dependency_add(mpf_resource)

        # Add outputs to pending queue
        self.context.add_pending(mpf_resource)
        self.context.add_pending(gui_resource)

        self.info("Created QuestaSim GUI project tasks")

    async def _attach_pending_inputs(self) -> None:
        """Attach any pending HDL files to the project task"""
        existing_paths = {r.path for r in self._project_task.inputs}

        for file_type in ACCEPTED_INPUT_TYPES:
            for source in list(self.context.filter_pending(file_type=file_type)):
                if source.path in existing_paths:
                    continue

                self.debug(f"Attaching input: {source.path} (type={file_type})")

                resource = self.context.get_resource(source.path)
                self._project_task.inputs.append(resource)
                self._project_task.dependency_add(resource)
                self.context.remove_pending(source.path)
