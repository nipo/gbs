"""nextpnr Dispatcher - FPGA place-and-route"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


@dataclass
class NextpnrTargetConfig:
    """Configuration for a specific nextpnr target.

    Two device-family styles exist. Lattice targets (ice40, ecp5)
    take `--<part> --package <pkg>` on the command line. Xilinx uses
    a pre-generated chipdb binary and takes `--chipdb <path>` instead
    - the part+package are baked into the chipdb, so `part_flag` is
    None for xilinx and the dispatcher supplies the chipdb path.
    """
    name: str                    # Target name (e.g., "ice40", "ecp5", "xilinx")
    default_executable: str      # Default executable name
    netlist_type: str            # Input netlist file type
    output_type: str             # Output file type
    output_extension: str        # Output file extension
    output_flag: str             # Command-line flag for output file
    constraint_type: str         # Constraint file type
    constraint_flag: str         # Command-line flag for constraint file
    use_chipdb: bool = False     # True for --chipdb style targets (xilinx)


# Target configurations
NEXTPNR_TARGETS = {
    "ice40": NextpnrTargetConfig(
        name="ice40",
        default_executable="nextpnr-ice40",
        netlist_type="ice40-netlist-json",
        output_type="ice40-asc",
        output_extension=".asc",
        output_flag="--asc",
        constraint_type="ice40-pcf",
        constraint_flag="--pcf",
    ),
    "ecp5": NextpnrTargetConfig(
        name="ecp5",
        default_executable="nextpnr-ecp5",
        netlist_type="ecp5-netlist-json",
        output_type="ecp5-config",
        output_extension=".config",
        output_flag="--textcfg",
        constraint_type="ecp5-lpf",
        constraint_flag="--lpf",
    ),
    "xilinx": NextpnrTargetConfig(
        name="xilinx",
        default_executable="nextpnr-xilinx",
        netlist_type="xilinx-netlist-json",
        output_type="nextpnr-fasm",
        output_extension=".fasm",
        output_flag="--fasm",
        constraint_type="nextpnr-xdc",
        constraint_flag="--xdc",
        use_chipdb=True,
    ),
}


class NextpnrDispatcher(BaseDispatcher):
    """nextpnr place-and-route dispatcher

    This dispatcher takes a JSON netlist and:
    1. Runs nextpnr to place and route the design
    2. Generates target-specific output (ASC for ice40, config for ecp5)
    """

    def __init__(
            self,
            context: BuildContext,
            target: str,
            nextpnr_tool: str,
            part: str = "",
            package: str = "",
            speed: str = "",
    ):
        self.target_config = NEXTPNR_TARGETS[target]
        super().__init__(context, f"nextpnr-{target}", tool_name=nextpnr_tool)
        self.part = part
        self.package = package
        self.speed = speed
        self._nextpnr_executable: str | None = None
        self._pnr_task: task.PlaceAndRoute | None = None

    def _get_nextpnr_executable(self) -> str:
        """Get nextpnr executable path (cached)

        Returns:
            Executable path
        """
        if self._nextpnr_executable is None:
            executable = self.get_tool_option("executable", self.target_config.default_executable)
            self._nextpnr_executable = str(expand_path(executable))
            self.debug(f"Using nextpnr executable: {self._nextpnr_executable}")

        return self._nextpnr_executable

    def get_chipdb_path(self) -> Path:
        """Resolve the --chipdb binary path for xilinx targets.

        Combines `chipdb_root` (a directory from tool config, populated
        by the apio provider) with the chipdb key derived from `part`.
        The chipdb file is named `<name><package>.bin` - speed grade
        is baked into the prjxray part.json rather than the chipdb.

        Raises:
            BuildError: If chipdb_root is unset, the part cannot be parsed,
                or the .bin file does not exist.
        """
        from ...build.task import BuildError
        from .. import xilinx_part
        chipdb_root = self.get_tool_option("chipdb_root", None)
        if chipdb_root is None:
            raise BuildError(
                f"nextpnr-xilinx needs a 'chipdb_root' in its tool config; "
                f"install openxc7 via apio, or set it explicitly on "
                f"tools:{self.tool_name}."
            )
        key = xilinx_part.chipdb_key(self.part)
        if key is None:
            raise BuildError(
                f"Cannot derive chipdb name from part '{self.part}'; "
                f"expected the vivado-style xc<name>-<speed><package> form."
            )
        chipdb_path = expand_path(chipdb_root) / f"{key}.bin"
        if not chipdb_path.is_file():
            raise BuildError(
                f"chipdb {chipdb_path} not found; the openxc7 install "
                f"ships a limited set of pre-generated chipdbs, extra "
                f"parts need to be built with bbasm."
            )
        return chipdb_path

    async def process(self) -> None:
        """Run place-and-route using nextpnr"""
        tc = self.target_config

        # Create PnR task if possible
        if not self._pnr_task:
            # Find the JSON netlist input
            netlist_resources = list(self.context.filter_pending(file_type=[tc.netlist_type]))

            if not netlist_resources:
                # No netlist yet, wait for next process() call
                return

            if len(netlist_resources) > 1:
                self.warning(f"Multiple netlists found, using first: {netlist_resources[0].path}")

            netlist_resource = netlist_resources[0]
            topcell = self.context.get_topcell()
            topcell_library = self.context.get_topcell_library()

            # Create output file
            output_path = self.context.output_path / f"{topcell}{tc.output_extension}"
            output_resource = self.context.get_resource(
                output_path,
                file_type=tc.output_type,
                library=topcell_library,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )

            # Always generate log file as intermediate resource
            log_resource = self.context.get_resource(
                self.context.output_path / "pnr.log",
                file_type="nextpnr-log",
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )

            # Create PnR task with netlist only initially
            self._pnr_task = task.PlaceAndRoute(
                dispatcher=self,
                inputs=[netlist_resource],
                outputs=[output_resource, log_resource],
            )
            self.attach_definition_dependencies(self._pnr_task)

            # Aggregate on demand
            for dest in self.context.filter_pending(file_type="nextpnr-pnr-report"):
                task.AggregatePnrReport(
                    dispatcher=self,
                    inputs=[log_resource],
                    outputs=[dest],
                )

        # On every process() call, check for new constraint files
        constraint_resources = list(self.context.filter_pending(file_type=[tc.constraint_type]))
        for constraint_resource in constraint_resources:
            self.info(f"Adding constraint: {constraint_resource.path.name}")
            self._pnr_task.add_input(constraint_resource)
