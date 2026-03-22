"""icepack Dispatcher - Ice40 bitstream generation"""

from __future__ import annotations

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class IcepackDispatcher(BaseDispatcher):
    """icepack bitstream generation dispatcher

    This dispatcher takes an ASCII bitstream (.asc) and:
    1. Converts it to binary format (.bin)
    """

    def __init__(
        self,
        context: BuildContext,
        icepack_tool: str = "icepack",
    ):
        super().__init__(context, "icepack", tool_name=icepack_tool)
        self._icepack_executable: str | None = None

    def _get_icepack_executable(self) -> str:
        """Get icepack executable path (cached)

        Returns:
            Executable path
        """
        if self._icepack_executable is None:
            if self.tool_config:
                executable = self.tool_config.get("executable", "icepack")
            else:
                executable = "icepack"
            self._icepack_executable = str(expand_path(executable))
            self.debug(f"Using icepack executable: {self._icepack_executable}")

        return self._icepack_executable

    async def process(self) -> None:
        """Generate binary bitstream using icepack"""
        # Find the ASC bitstream input
        asc_resources = list(self.context.filter_pending(file_type=["ice40-asc"]))

        if not asc_resources:
            self.debug("No ice40 ASC bitstream found for icepack")
            return

        if len(asc_resources) > 1:
            self.warning(f"Multiple ASC files found, using first: {asc_resources[0].path}")

        asc_resource = asc_resources[0]
        topcell = self.context.get_topcell()
        topcell_library = self.context.get_topcell_library()

        # Create output BIN file (ice40-bitstream is an alias for ice40-bin)
        bin_path = self.context.output_path / f"{topcell}.bin"
        # Use ice40-bitstream as the primary type
        bin_resource = self.context.get_resource(
            bin_path,
            file_type="ice40-bitstream",
            library=topcell_library,
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name,
        )

        # Create icepack task
        pack_task = task.Pack(
            dispatcher=self,
            inputs=[asc_resource],
            outputs=[bin_resource],
        )

        # Remove ASC from pending (consumed by icepack)
        dependents = self.context.remove_pending(asc_resource.path)
        for dep in dependents:
            pack_task.dependency_add(dep)

        # Add BIN to pending queue
