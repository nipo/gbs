from __future__ import annotations
import logging
import csv
from typing import Any
from pathlib import Path

from ...backend.dispatcher import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task
from .gw_sh import Session
from .device_info import DeviceInfo, get_device_info

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
            context: BuildContext,
            vhdl_std: str,
            gowin_tool: str,
            gowin_path: Path,
            device_info: DeviceInfo
    ):
        super().__init__(context, "gowin", tool_name=gowin_tool, priority=600)
        self.gowin_tool = gowin_tool
        self.gowin_path = gowin_path
        self.output_base_name = "project"
        self.vhdl_std = vhdl_std
        self.device_info = device_info
        self._session: Session | None = None

        # Task references (created on first process() call with HDL)
        self._pin_cst_task: Task | None = None
        self._timing_sdc_task: Task | None = None

    def _get_session(self) -> Session:
        """Get or create shared gw_sh session"""
        if self._session is None:
            gw_sh = self.gowin_path / "IDE" / "bin" / "gw_sh"

            if not gw_sh.exists():
                raise RuntimeError(f"gw_sh not found at {gw_sh}")

            self._session = Session(
                argv=[str(gw_sh)],
                cwd=self.context.output_path,
                use_pty=True,
            )

        return self._session

    async def process(self) -> None:
        """Process HDL sources and constraints

        Creates all tasks on first call with HDL sources.
        On subsequent calls, adds new constraint files as inputs to existing tasks.
        """

        # Get target from output group configuration
        if not self.context.project:
            raise ValueError("No project configured")

        if not self.device_info:
            # No target device configured - skip Gowin backend (simulation-only project)
            self.logger.debug("No target device configured, skipping Gowin backend")
            return

        # Get output base name
        output_base_name = self.output_base_name or self.context.get_topcell()

        # Check if we have HDL sources
        has_hdl = bool(list(self.context.filter_pending(file_type=["vhdl", "verilog"])))

        if has_hdl and self._pin_cst_task is None:
            # First call with HDL sources - create all tasks
            self.logger.debug("Creating all Gowin build tasks")
            await self._create_all_tasks(output_base_name)

        elif self._pin_cst_task is not None:
            # Subsequent calls - add new constraint files to existing tasks
            await self._update_constraint_inputs()

    async def _create_all_tasks(
        self,
        output_base_name: str
    ) -> None:
        """Create all Gowin build tasks on first call

        Creates: init, synthesis, constraint aggregation (pin + timing), and PnR tasks.
        Stores references to constraint aggregation tasks for dynamic input updates.
        """

        session = self._get_session()

        # Define resource paths
        netlist_file = self.context.output_path / "impl" / "gwsynthesis" / f"{output_base_name}.vg"
        netlist_resource = self.context.get_resource(
            netlist_file,
            file_type="gowin-netlist",
            library="work",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )

        # Virtual resource that indicates project has been initialized in gw_sh session
        # This is volatile - the session state doesn't persist across builds
        init_marker_resource = self.context.get_virtual_resource("gowin_project_init")

        # Get HDL sources in dependency order
        vhdl_sources = list(self.context.filter_pending(file_type=["vhdl"]))
        verilog_sources = list(self.context.filter_pending(file_type=["verilog"]))

        # Get constraint files (may be empty on first call)
        cst_sources = list(self.context.filter_pending(file_type=["gowin-cst"]))
        sdc_sources = list(self.context.filter_pending(file_type=["gowin-sdc"]))
        serdes_config_sources = list(self.context.filter_pending(file_type=["gowin-serdes-config"]))

        # Define constraint file paths
        pin_cst_file = self.context.output_path / "aggregate_pins.cst"
        timing_sdc_file = self.context.output_path / "aggregate_timing.sdc"
        pin_cst_resource = self.context.get_resource(
            pin_cst_file,
            file_type="gowin-cst",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )
        timing_sdc_resource = self.context.get_resource(
            timing_sdc_file,
            file_type="gowin-sdc",
            typology=ResourceTypology.INTERMEDIATE,
            generated_by=self.name
        )

        # Define bitstream output
        bitstream_file = self.context.output_path / "impl" / "pnr" / f"{output_base_name}.fs"
        bitstream_bin_file = self.context.output_path / "impl" / "pnr" / f"{output_base_name}.bin"
        bitstream_resource = self.context.get_resource(
            bitstream_file,
            file_type="gowin-fs",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name
        )
        bitstream_bin_resource = self.context.get_resource(
            bitstream_bin_file,
            file_type="gowin-bin",
            typology=ResourceTypology.OUTPUT,
            generated_by=self.name
        )

        # Create HDL input resources with metadata
        hdl_input_resources = []

        # Create resources for bundled IEEE library files (math_real)
        # These will be generated by a task
        ieee_dir = self.context.output_path / "ieee"
        ieee_file_resources = []
        for filename in ["math_real.vhdl", "math_real-body.vhdl"]:
            ieee_path = ieee_dir / filename
            resource = self.context.get_resource(
                ieee_path,
                file_type='vhdl',
                library='ieee',
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name
            )
            resource.metadata['language'] = 'vhdl'
            ieee_file_resources.append(resource)
            hdl_input_resources.append(resource)

        # Create task to copy bundled IEEE files
        task.CopyBundledIeeeFiles(
            dispatcher=self,
            outputs=ieee_file_resources
        )

        for source in vhdl_sources + verilog_sources:
            # Add custom metadata for Gowin task
            source.metadata['language'] = 'vhdl' if source in vhdl_sources else 'verilog'
            hdl_input_resources.append(source)

        # Handle SerDes configuration if present (Gowin 5-series)
        # Must be done before ProjectInit since CSR file is added to project
        serdes_csr_resource = None
        if serdes_config_sources:
            if self.device_info.klut_count is None:
                self.logger.warning(
                    f"SerDes config found but device {self.device_info} doesn't appear to support SerDes"
                )
            else:
                # Create CSR output path
                serdes_csr_file = self.context.output_path / "serdes_init.csr"
                serdes_csr_resource = self.context.get_resource(
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
                    dispatcher=self,
                    gowin_tool=self.gowin_tool,
                    klut_count=self.device_info.klut_count,
                    inputs=[toml_resource],
                    outputs=[serdes_csr_resource]
                )

                # Add CSR to pending queue
                self.context.add_pending(serdes_csr_resource)

                self.logger.info(f"SerDes CSR will be generated from {serdes_config_sources[0].path}")

        # Build init task inputs: HDL files + optional CSR
        init_inputs = list(hdl_input_resources)
        if serdes_csr_resource is not None:
            serdes_csr_resource.metadata = {'file_type': 'gowin-serdes-init'}
            init_inputs.append(serdes_csr_resource)

        # Create project init task
        init_task = task.ProjectInit(
            dispatcher=self,
            session=session,
            gowin_tool=self.gowin_tool,
            output_base_name=output_base_name,
            output_dir=self.context.output_path,
            inputs=init_inputs,
            outputs=[init_marker_resource],
        )

        # Create synthesis task
        synth_task = task.Synthesis(
            dispatcher=self,
            session=session,
            inputs=[init_marker_resource],
            outputs=[netlist_resource]
        )

        # Add netlist to pending queue
        self.context.add_pending(netlist_resource)

        # Use constraint sources directly (they already have file_type metadata)
        cst_input_resources = list(cst_sources)
        sdc_input_resources = list(sdc_sources)

        # Create pin constraint aggregation task (.cst files)
        # Store reference for dynamic input updates
        self._pin_cst_task = task.AggregateConstraints(
            dispatcher=self,
            file_type="gowin-cst",
            inputs=cst_input_resources,
            outputs=[pin_cst_resource]
        )

        # Create timing constraint aggregation task (.sdc files)
        # Store reference for dynamic input updates
        self._timing_sdc_task = task.AggregateConstraints(
            dispatcher=self,
            file_type="gowin-sdc",
            inputs=sdc_input_resources,
            outputs=[timing_sdc_resource]
        )

        # Create PnR task (depends on init + netlist + constraints)
        pnr_task = task.PnR(
            dispatcher=self,
            session=session,
            inputs=[init_marker_resource, netlist_resource, pin_cst_resource, timing_sdc_resource],
            outputs=[bitstream_resource, bitstream_bin_resource]
        )

        # Add bitstreams to pending queue
        self.context.add_pending(bitstream_resource)
        self.context.add_pending(bitstream_bin_resource)

    async def _update_constraint_inputs(self) -> None:
        """Add new constraint files as inputs to existing aggregation tasks

        Called on subsequent process() iterations when new constraint files appear.
        """

        # Get all constraint files from pending queue
        cst_sources = list(self.context.filter_pending(file_type=["gowin-cst"]))
        sdc_sources = list(self.context.filter_pending(file_type=["gowin-sdc"]))

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


