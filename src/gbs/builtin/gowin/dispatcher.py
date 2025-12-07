from __future__ import annotations
import logging
import shutil
import csv
from importlib.resources import files, as_file
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task
from .gw_sh import Session
from .device_info import get_device_info, parse_device_csv

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
        gowin_tool: str = "gowin",
    ):
        super().__init__("gowin", priority=600)
        self.gowin_tool = gowin_tool
        self.output_base_name = "project"
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

            self._session = Session(gw_sh, context.output_path, self.logger)

        return self._session

    def _copy_bundled_ieee_files(self, context: BuildContext) -> list[tuple[Path, str]]:
        """Copy bundled IEEE library files to build directory

        Gowin doesn't ship ieee.math_real, so we provide synthesizable versions.
        Files are copied to build dir and returned as (path, library) tuples.

        Returns:
            List of (file_path, library_name) for injected files
        """
        injected = []
        ieee_dir = context.output_path / "ieee"
        ieee_dir.mkdir(parents=True, exist_ok=True)

        pkg_resources = files("gbs.builtin.gowin.resources.ieee")

        for filename in ["math_real.vhdl", "math_real-body.vhdl"]:
            dest_path = ieee_dir / filename
            if not dest_path.exists():
                with as_file(pkg_resources / filename) as src_path:
                    shutil.copy(src_path, dest_path)
                self.logger.debug(f"Copied bundled {filename} to {dest_path}")
            injected.append((dest_path, "ieee"))

        return injected

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
            target = context.project.raw_config.get("target", {})
            device = target.get("part")
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
                "target_part": self._device_info.get("part", ""),
                "target_part_name": self._device_info.get("part_name", ""),
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
                                    "part": device,
                                    "part_name": row[3].strip() if len(row) > 3 else "",
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
        context: BuildContext
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
        output_base_name = self.output_base_name or context.get_topcell()

        # Check if we have HDL sources
        has_hdl = bool(list(context.filter_pending(file_type=["vhdl", "verilog"])))

        if has_hdl and self._pin_cst_task is None:
            # First call with HDL sources - create all tasks
            self.logger.debug("Creating all Gowin build tasks")
            await self._create_all_tasks(context, target, output_base_name)

        elif self._pin_cst_task is not None:
            # Subsequent calls - add new constraint files to existing tasks
            await self._update_constraint_inputs(context)

    async def _create_all_tasks(
        self,
        context: BuildContext,
        target: dict,
        output_base_name: str
    ) -> None:
        """Create all Gowin build tasks on first call

        Creates: init, synthesis, constraint aggregation (pin + timing), and PnR tasks.
        Stores references to constraint aggregation tasks for dynamic input updates.
        """

        session = self._get_session(context)

        # Define resource paths
        netlist_file = context.output_path / "impl" / "gwsynthesis" / f"{output_base_name}.vg"
        netlist_resource = context.get_resource(
            netlist_file,
            file_type="gowin-netlist",
            library="work",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )

        # Virtual resource that indicates project has been initialized in gw_sh session
        # This is volatile - the session state doesn't persist across builds
        init_marker_resource = context.get_virtual_resource("gowin_project_init")

        # Get HDL sources in dependency order
        vhdl_sources = list(context.filter_pending(file_type=["vhdl"]))
        verilog_sources = list(context.filter_pending(file_type=["verilog"]))

        # Get constraint files (may be empty on first call)
        cst_sources = list(context.filter_pending(file_type=["gowin-cst"]))
        sdc_sources = list(context.filter_pending(file_type=["gowin-sdc"]))
        serdes_config_sources = list(context.filter_pending(file_type=["gowin-serdes-config"]))

        # Define constraint file paths
        pin_cst_file = context.output_path / "aggregate_pins.cst"
        timing_sdc_file = context.output_path / "aggregate_timing.sdc"
        pin_cst_resource = context.get_resource(
            pin_cst_file,
            file_type="gowin-cst",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        timing_sdc_resource = context.get_resource(
            timing_sdc_file,
            file_type="gowin-sdc",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )

        # Define bitstream output
        bitstream_file = context.output_path / "impl" / "pnr" / f"{output_base_name}.fs"
        bitstream_bin_file = context.output_path / "impl" / "pnr" / f"{output_base_name}.bin"
        bitstream_resource = context.get_resource(
            bitstream_file,
            file_type="gowin-fs",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name
        )
        bitstream_bin_resource = context.get_resource(
            bitstream_bin_file,
            file_type="gowin-bin",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name
        )

        # Create HDL input resources with metadata
        hdl_input_resources = []

        # Inject bundled IEEE library files (math_real) - must come first
        for injected_path, library in self._copy_bundled_ieee_files(context):
            resource = context.get_resource(
                injected_path,
                file_type='vhdl',
                library=library,
                typology=ResourceTypology.SOURCE
            )
            # Add custom metadata for Gowin task
            resource.metadata['language'] = 'vhdl'
            hdl_input_resources.append(resource)

        for source in vhdl_sources + verilog_sources:
            # Add custom metadata for Gowin task
            source.metadata['language'] = 'vhdl' if source in vhdl_sources else 'verilog'
            hdl_input_resources.append(source)

        # Handle SerDes configuration if present (Gowin 5-series)
        # Must be done before ProjectInit since CSR file is added to project
        serdes_csr_resource = None
        if serdes_config_sources:
            # Get device info to determine klut_count for tool selection
            gowin_config = context.get_tool(self.gowin_tool)
            gowin_path = Path(gowin_config["path"])
            device = target.get("part")
            device_info = get_device_info(gowin_path, device, self.logger)

            if device_info.klut_count is None:
                self.logger.warning(
                    f"SerDes config found but device {device} doesn't appear to support SerDes"
                )
            else:
                # Create CSR output path
                serdes_csr_file = context.output_path / "serdes_init.csr"
                serdes_csr_resource = context.get_resource(
                    serdes_csr_file,
                    file_type="gowin-serdes-init",
                    typology=ResourceTypology.INTERMEDIATE,
                    generated_by=self.name
                )

                # Get input TOML resource (use first if multiple)
                if len(serdes_config_sources) > 1:
                    self.logger.warning(
                        f"Multiple SerDes configs found, using first: {serdes_config_sources[0].path}"
                    )
                toml_resource = serdes_config_sources[0]

                # Create SerDes to CSR conversion task
                task.SerDesToCsr(
                    context=context,
                    gowin_tool=self.gowin_tool,
                    klut_count=device_info.klut_count,
                    inputs=[toml_resource],
                    outputs=[serdes_csr_resource]
                )

                # Add CSR to pending queue
                context.add_pending(serdes_csr_resource)

                self.logger.info(f"SerDes CSR will be generated from {serdes_config_sources[0].path}")

        # Build init task inputs: HDL files + optional CSR
        init_inputs = list(hdl_input_resources)
        if serdes_csr_resource is not None:
            serdes_csr_resource.metadata = {'file_type': 'gowin-serdes-init'}
            init_inputs.append(serdes_csr_resource)

        # Create project init task
        init_task = task.ProjectInit(
            context=context,
            session=session,
            gowin_tool=self.gowin_tool,
            output_base_name=output_base_name,
            output_dir=context.output_path,
            inputs=init_inputs,
            outputs=[init_marker_resource],
        )

        # Create synthesis task
        synth_task = task.Synthesis(
            context=context,
            session=session,
            inputs=[init_marker_resource],
            outputs=[netlist_resource]
        )

        # Add netlist to pending queue
        context.add_pending(netlist_resource)

        # Use constraint sources directly (they already have file_type metadata)
        cst_input_resources = list(cst_sources)
        sdc_input_resources = list(sdc_sources)

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
            outputs=[bitstream_resource, bitstream_bin_resource]
        )

        # Add bitstreams to pending queue
        context.add_pending(bitstream_resource)
        context.add_pending(bitstream_bin_resource)

    async def _update_constraint_inputs(
        self,
        context: BuildContext
    ) -> None:
        """Add new constraint files as inputs to existing aggregation tasks

        Called on subsequent process() iterations when new constraint files appear.
        """

        # Get all constraint files from pending queue
        cst_sources = list(context.filter_pending(file_type=["gowin-cst"]))
        sdc_sources = list(context.filter_pending(file_type=["gowin-sdc"]))

        # Get current input paths from tasks
        existing_cst_paths = {r.path for r in self._pin_cst_task.inputs}
        existing_sdc_paths = {r.path for r in self._timing_sdc_task.inputs}

        # Find new .cst files
        for source in cst_sources:
            if source.path not in existing_cst_paths:
                self.logger.debug(f"Adding new .cst constraint: {source.path}")
                self._pin_cst_task.inputs.append(source)
                # Set up dependency so task waits for this resource
                self._pin_cst_task.dependency_add(source)

        # Find new .sdc files
        for source in sdc_sources:
            if source.path not in existing_sdc_paths:
                self.logger.debug(f"Adding new .sdc constraint: {source.path}")
                self._timing_sdc_task.inputs.append(source)
                # Set up dependency so task waits for this resource
                self._timing_sdc_task.dependency_add(source)


