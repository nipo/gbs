"""QuestaSim/ModelSim Dispatcher

Dispatcher implementation for QuestaSim/ModelSim simulation flow.
"""

from __future__ import annotations
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from ...utils import expand_path
from .task import GenerateBatchScript, GenerateSimulatorScript


# Accepted input file types
ACCEPTED_INPUT_TYPES = {"vhdl", "verilog"}


class QuestaDispatcher(BaseDispatcher):
    """QuestaSim/ModelSim simulation dispatcher

    Workflow:
      First process() call:
        - Creates batch script generation task
        - Creates simulator script generation task
        - Attaches output resources

      Subsequent process() calls:
        - Attaches any pending HDL files to the batch script task

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
        self._batch_task: GenerateBatchScript | None = None
        self._simulator_task: GenerateSimulatorScript | None = None

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

        On first call, creates batch and simulator tasks.
        On every call, attaches any pending HDL files to the batch task.
        """
        if self._batch_task is None:
            await self._create_tasks()

        # Attach any pending HDL files
        await self._attach_pending_inputs()

    async def _create_tasks(self) -> None:
        """Create batch script and simulator script tasks"""
        vsim_executable = self._get_vsim_executable()

        # Create batch script resource
        batch_script_path = self.context.output_path / "questa_batch.do"
        batch_script_resource = self.context.get_resource(
            batch_script_path,
            file_type="questa-batch-script",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Create simulator script resource
        simulator_script_path = self.context.output_path / "simulator.sh"
        simulator_resource = self.context.get_resource(
            simulator_script_path,
            file_type="questa-simulator",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name,
        )

        # Create batch script task (no inputs yet, added dynamically)
        self._batch_task = GenerateBatchScript(
            dispatcher=self,
            vhdl_std=self.vhdl_std,
            inputs=[],
            outputs=[batch_script_resource],
        )

        # Create simulator script task (depends on batch script)
        self._simulator_task = GenerateSimulatorScript(
            dispatcher=self,
            vsim_executable=vsim_executable,
            inputs=[batch_script_resource],
            outputs=[simulator_resource],
        )

        # Add dependency: simulator task depends on batch task output
        self._simulator_task.dependency_add(batch_script_resource)

        # Add outputs to pending queue
        self.context.add_pending(batch_script_resource)
        self.context.add_pending(simulator_resource)

        self.logger.info("Created QuestaSim build tasks")

    async def _attach_pending_inputs(self) -> None:
        """Attach any pending HDL files to the batch script task"""
        existing_paths = {r.path for r in self._batch_task.inputs}

        for file_type in ACCEPTED_INPUT_TYPES:
            for source in list(self.context.filter_pending(file_type=file_type)):
                if source.path in existing_paths:
                    continue

                self.logger.debug(f"Attaching input: {source.path} (type={file_type})")

                resource = self.context.get_resource(source.path)
                resource.metadata = {
                    'file_type': file_type,
                    'library': source.library,
                    'variant': getattr(source, 'variant', None),
                }

                self._batch_task.inputs.append(resource)
                self._batch_task.dependency_add(resource)
                self.context.remove_pending(source.path)
