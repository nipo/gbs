from __future__ import annotations
import logging
from typing import Any
from pathlib import Path

from ...model.dispatcher import BaseDispatcher
from ...model.build import BuildContext, BuildFileSet, BuildResource
from . import task
from .gw_sh import Session

class GowinDispatcher(BaseDispatcher):
    """Gowin FPGA synthesis backend

    Workflow:

    First process() call (with HDL sources):
      - Creates all tasks: init, synthesis, constraint aggregation, PnR
      - Synthesis: VHDL/Verilog → netlist.vg
      - Constraint aggregation: .cst/.sdc → aggregated files
      - PnR: netlist + constraints → bitstream
      - Adds netlist to fileset for other backends

    Subsequent process() calls:
      - Dynamically adds new constraint files as inputs to aggregation tasks
      - Task system automatically re-runs tasks with new inputs

    This allows other backends (e.g., NSL) to generate constraints between
    iterations by inspecting the netlist.

    Priority: 600 (synthesis/backend compilation)
    """

    def __init__(
        self,
        output_dir: Path | str = "build",
        gowin_tool: str = "gowin",
        output_base_name: str | None = None,
    ):
        super().__init__("gowin", priority=600)
        self.output_dir = Path(output_dir)
        self.gowin_tool = gowin_tool
        self.output_base_name = output_base_name
        self._session: Session | None = None
        self._device_info: dict[str, str] | None = None  # Cached device characteristics

        # Task references (created on first process() call with HDL)
        self._pin_cst_task: Task | None = None
        self._timing_sdc_task: Task | None = None

    def _get_session(self, context: BuildContext) -> Session:
        """Get or create shared gw_sh session"""
        if self._session is None:
            gowin_config = context.get_tool(self.gowin_tool)
            gowin_path = Path(gowin_config["path"])
            gw_sh = gowin_path / "IDE" / "bin" / "gw_sh"

            if not gw_sh.exists():
                raise RuntimeError(f"gw_sh not found at {gw_sh}")

            self._session = Session(gw_sh, self.output_dir, self.logger)

        return self._session

    def get_filter_variables(self, context: BuildContext) -> dict[str, Any]:
        """Provide filter variables for Gowin synthesis

        Provides device characteristics from CSV if device is configured.

        Returns:
            Filter variables for synthesis targeting
        """
        filter_vars = {
            "target-usage": "synthesis",
            "vendor": "gowin",
        }

        # Parse device info if not already cached and device is configured
        if self._device_info is None and context.project:
            device = context.project.raw_config.get("device")
            if device:
                try:
                    gowin_config = context.get_tool(self.gowin_tool, required=False)
                    if gowin_config:
                        gowin_path = Path(gowin_config["path"])
                        # Parse CSV to populate self._device_info
                        self._parse_device_csv(gowin_path, device)
                except Exception as e:
                    self.logger.debug(f"Could not parse device info: {e}")

        # Add device characteristics if available
        if self._device_info:
            filter_vars.update({
                "device-family": self._device_info.get("family", ""),
                "device-package": self._device_info.get("package", ""),
                "device-voltage": self._device_info.get("voltage", ""),
                "device-speed": self._device_info.get("speed", ""),
            })

        return filter_vars

    def _parse_device_csv(self, gowin_path: Path, device: str) -> tuple[str, str]:
        """Parse Gowin device CSV to get device characteristics and set_device parameters

        Wrapper around module-level parse_device_csv that caches device info.

        Args:
            gowin_path: Path to Gowin installation
            device: Device part number from project config (e.g., "GW1NR-LV9QN88PC6/I5")

        Returns:
            Tuple of (part_group, part_number) for set_device command

        Side effects:
            Populates self._device_info with device characteristics for filter variables
        """
        # Parse device info if not already cached
        if self._device_info is None:
            csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

            if csv_path.exists():
                try:
                    with open(csv_path, 'r', encoding='utf-8') as f:
                        reader = csv.reader(f)

                        for row in reader:
                            if len(row) < 10:
                                continue

                            if row[1].strip() == device.strip():
                                # Cache device characteristics for filter variables
                                self._device_info = {
                                    "family": row[3].strip() if len(row) > 3 else "",
                                    "package": row[6].strip() if len(row) > 6 else "",
                                    "voltage": row[7].strip() if len(row) > 7 else "",
                                    "speed": row[8].strip() if len(row) > 8 else "",
                                }
                                break
                except Exception as e:
                    self.logger.debug(f"Could not cache device info: {e}")

        # Call module-level function for actual parsing
        return parse_device_csv(gowin_path, device, self.logger)

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process HDL sources and constraints

        Creates all tasks on first call with HDL sources.
        On subsequent calls, adds new constraint files as inputs to existing tasks.
        """

        # Get target from project config
        if not context.project:
            raise ValueError("No project configured")

        target = context.project.raw_config.get("target", {})
        device = target.get("part")
        if not device:
            # No target device configured - skip Gowin backend (simulation-only project)
            self.logger.debug("No target device configured, skipping Gowin backend")
            return

        # Get output base name
        output_base_name = self.output_base_name or context.project.topcell

        # Check if we have HDL sources
        has_hdl = bool(fileset.filter(file_type="vhdl") or fileset.filter(file_type="verilog"))

        if has_hdl and self._pin_cst_task is None:
            # First call with HDL sources - create all tasks
            self.logger.debug("Creating all Gowin build tasks")
            await self._create_all_tasks(context, fileset, target, output_base_name)

        elif self._pin_cst_task is not None:
            # Subsequent calls - add new constraint files to existing tasks
            await self._update_constraint_inputs(context, fileset)

    async def _create_all_tasks(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        target: dict,
        output_base_name: str
    ) -> None:
        """Create all Gowin build tasks on first call

        Creates: init, synthesis, constraint aggregation (pin + timing), and PnR tasks.
        Stores references to constraint aggregation tasks for dynamic input updates.
        """

        session = self._get_session(context)

        # Define resource paths
        netlist_file = self.output_dir / "impl" / "gwsynthesis" / f"{output_base_name}.vg"
        netlist_resource = context.get_resource(netlist_file)

        # Virtual resource that indicates project has been initialized in gw_sh session
        # This is volatile - the session state doesn't persist across builds
        init_marker_resource = context.get_virtual_resource("gowin_project_init")

        # Get HDL sources in dependency order
        vhdl_sources = list(fileset.filter(file_type="vhdl"))
        verilog_sources = list(fileset.filter(file_type="verilog"))

        # Get constraint files (may be empty on first call)
        cst_sources = list(fileset.filter(file_type="gowin-cst"))
        sdc_sources = list(fileset.filter(file_type="gowin-sdc"))

        # Define constraint file paths
        pin_cst_file = self.output_dir / "aggregate_pins.cst"
        timing_sdc_file = self.output_dir / "aggregate_timing.sdc"
        pin_cst_resource = context.get_resource(pin_cst_file)
        timing_sdc_resource = context.get_resource(timing_sdc_file)

        # Define bitstream output
        bitstream_file = self.output_dir / "impl" / "pnr" / f"{output_base_name}.fs"
        bitstream_resource = context.get_resource(bitstream_file)

        # Create HDL input resources with metadata
        hdl_input_resources = []
        for source in vhdl_sources + verilog_sources:
            resource = context.get_resource(source.path)
            resource.metadata = {
                'file_type': source.file_type,
                'library': source.library,
                'language': 'vhdl' if source in vhdl_sources else 'verilog',
            }
            hdl_input_resources.append(resource)

        # Create project init task
        init_task = task.ProjectInit(
            context=context,
            session=session,
            gowin_tool=self.gowin_tool,
            output_base_name=output_base_name,
            output_dir=self.output_dir,
            inputs=hdl_input_resources,
            outputs=[init_marker_resource]
        )

        # Create synthesis task
        synth_task = task.Synthesis(
            context=context,
            session=session,
            inputs=[init_marker_resource],
            outputs=[netlist_resource]
        )

        # Add netlist to fileset for PnR
        netlist_br = BuildResource(
            resource=netlist_resource,
            file_type="gowin-netlist",
            library="work",
            is_source=False,
            generated_by=self.name
        )
        fileset.add(netlist_br)

        # Create .cst constraint input resources with metadata
        cst_input_resources = []
        for source in cst_sources:
            resource = context.get_resource(source.path)
            resource.metadata = {
                'file_type': source.file_type,
            }
            cst_input_resources.append(resource)

        # Create .sdc constraint input resources with metadata
        sdc_input_resources = []
        for source in sdc_sources:
            resource = context.get_resource(source.path)
            resource.metadata = {
                'file_type': source.file_type,
            }
            sdc_input_resources.append(resource)

        # Create pin constraint aggregation task (.cst files)
        # Store reference for dynamic input updates
        self._pin_cst_task = task.AggregateConstraints(
            context=context,
            file_type="gowin-cst",
            inputs=cst_input_resources,
            outputs=[pin_cst_resource]
        )

        # Create timing constraint aggregation task (.sdc files)
        # Store reference for dynamic input updates
        self._timing_sdc_task = task.AggregateConstraints(
            context=context,
            file_type="gowin-sdc",
            inputs=sdc_input_resources,
            outputs=[timing_sdc_resource]
        )

        # Create PnR task (depends on init + netlist + constraints)
        pnr_task = task.PnR(
            context=context,
            session=session,
            inputs=[init_marker_resource, netlist_resource, pin_cst_resource, timing_sdc_resource],
            outputs=[bitstream_resource]
        )

        # Add bitstream to fileset
        bitstream_br = BuildResource(
            resource=bitstream_resource,
            file_type="bitstream",
            library=None,
            is_source=False,
            generated_by=self.name
        )
        fileset.add(bitstream_br)

    async def _update_constraint_inputs(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Add new constraint files as inputs to existing aggregation tasks

        Called on subsequent process() iterations when new constraint files appear.
        """

        # Get all constraint files from current fileset
        cst_sources = list(fileset.filter(file_type="gowin-cst"))
        sdc_sources = list(fileset.filter(file_type="gowin-sdc"))

        # Get current input paths from tasks
        existing_cst_paths = {r.path for r in self._pin_cst_task.inputs}
        existing_sdc_paths = {r.path for r in self._timing_sdc_task.inputs}

        # Find new .cst files
        for source in cst_sources:
            if source.path not in existing_cst_paths:
                self.logger.debug(f"Adding new .cst constraint: {source.path}")
                resource = context.get_resource(source.path)
                resource.metadata = {
                    'file_type': source.file_type,
                }
                self._pin_cst_task.inputs.append(resource)
                # Set up dependency so task waits for this resource
                self._pin_cst_task.dependency_add(resource)

        # Find new .sdc files
        for source in sdc_sources:
            if source.path not in existing_sdc_paths:
                self.logger.debug(f"Adding new .sdc constraint: {source.path}")
                resource = context.get_resource(source.path)
                resource.metadata = {
                    'file_type': source.file_type,
                }
                self._timing_sdc_task.inputs.append(resource)
                # Set up dependency so task waits for this resource
                self._timing_sdc_task.dependency_add(resource)


