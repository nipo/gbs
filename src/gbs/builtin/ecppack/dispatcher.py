"""ecppack Dispatcher - ECP5 bitstream generation"""

from __future__ import annotations

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class EcppackDispatcher(BaseDispatcher):
    """ecppack bitstream generation dispatcher

    This dispatcher takes a text config (.config) and:
    1. Converts it to binary format (.bit)
    """

    def __init__(
        self,
        context: BuildContext,
        ecppack_tool: str = "ecppack",
    ):
        super().__init__(context, "ecppack", tool_name=ecppack_tool)
        self._ecppack_executable: str | None = None

    def _get_ecppack_executable(self) -> str:
        """Get ecppack executable path (cached)

        Returns:
            Executable path
        """
        if self._ecppack_executable is None:
            if self.tool_config:
                executable = self.tool_config.get("executable", "ecppack")
            else:
                executable = "ecppack"
            self._ecppack_executable = str(expand_path(executable))
            self.debug(f"Using ecppack executable: {self._ecppack_executable}")

        return self._ecppack_executable

    async def process(self) -> None:
        """Generate binary bitstream using ecppack"""
        # Find the config input
        config_resources = list(self.context.filter_pending(file_type=["ecp5-config"]))

        if not config_resources:
            self.debug("No ECP5 config found for ecppack")
            return

        if len(config_resources) > 1:
            self.warning(f"Multiple config files found, using first: {config_resources[0].path}")

        config_resource = config_resources[0]
        topcell = self.context.get_topcell()
        topcell_library = self.context.get_topcell_library()

        # Create output BIT file (ecp5-bitstream is an alias for ecp5-bit)
        bit_path = self.context.output_path / f"{topcell}.bit"
        # Use ecp5-bitstream as the primary type
        bit_resource = self.context.get_resource(
            bit_path,
            file_type="ecp5-bitstream",
            library=topcell_library,
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name,
        )

        # Create ecppack task
        pack_task = task.Pack(
            dispatcher=self,
            inputs=[config_resource],
            outputs=[bit_resource],
        )

        # Remove config from pending (consumed by ecppack)
        dependents = self.context.remove_pending(config_resource.path)
        for dep in dependents:
            pack_task.dependency_add(dep)

        # Add BIT to pending queue
