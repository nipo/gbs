from __future__ import annotations
import asyncio
import random
import shutil
from pathlib import Path
from importlib.resources import files, as_file

from ...build.context import BuildContext
from ...build.task import Resource, Task, ExecutorTask, ResourceTypology
from ...build import tcl
from ...utils import expand_path
from ...report_aggregator import ReportPage, aggregate_html
from .gw_sh import *
from ...build.subprocess import MessageSubprocess

class CopyBundledIeeeFiles(Task):
    """Task that copies bundled IEEE library files to build directory

    Gowin doesn't ship ieee.math_real, so we provide synthesizable versions.
    This task extracts them from resources and places them in the build directory.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="copy_bundled_ieee",
            inputs=[],
            outputs=outputs,
            description="Copy bundled IEEE math_real files"
        )

    async def work(self) -> None:
        """Copy bundled IEEE files from resources to output paths"""
        pkg_resources = files("gbs.builtin.gowin.resources.ieee")

        for output in self.outputs:
            filename = output.path.name
            # Create directory if needed
            output.path.parent.mkdir(parents=True, exist_ok=True)

            # Copy from resources
            with as_file(pkg_resources / filename) as src_path:
                shutil.copy(src_path, output.path)
            self.info(f"Copied bundled {filename} to {output.path}")

class ProjectInit(GwShCommand):
    """Initialize Gowin project in gw_sh session"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        output_base_name: str,
        output_dir: Path,
        inputs: list,
        outputs: list,
    ):
        super().__init__(dispatcher,
            name = "gowin_project_init",
            session = session,
            inputs = inputs,
            outputs = outputs,
            description = f"Gowin project init"
        )
        self.output_base_name = output_base_name
        self.output_dir = output_dir

    async def work(self) -> None:
        """Initialize Gowin project in gw_sh"""
        try:
            # Compute values from context
            target = self.dispatcher.context.get_target()
            topcell = self.dispatcher.context.get_topcell()

            # Prepare paths
            self.output_dir.mkdir(parents=True, exist_ok=True)

            # Compute constraint file paths
            pin_cst_file = self.output_dir / "aggregate_pins.cst"
            timing_sdc_file = self.output_dir / "aggregate_timing.sdc"

            self.info(f"Initializing Gowin project for {self.dispatcher.device_info}")

            # Configure device
            self.debug(f"Configuring device: {self.dispatcher.device_info.family} {self.dispatcher.device_info.part}")
            await self.command_run(tcl.Command(["set_device", "-name", self.dispatcher.device_info.family, self.dispatcher.device_info.part]))

            # Set top module
            self.debug(f"Setting top module: {topcell}")
            await self.command_run(tcl.Command(["set_option", "-top_module", topcell]))

            # Set output base name (for predictable output paths)
            self.debug(f"Setting output base name: {self.output_base_name}")
            await self.command_run(tcl.Command(["set_option", "-output_base_name", self.output_base_name]))

            # Process vendor-specific options
            # use_as_gpio: list of pin names to configure as GPIO
            use_as_gpio = target.get("use_as_gpio", [])
            if use_as_gpio:
                self.debug(f"Configuring GPIO pins: {use_as_gpio}")
                for pin_name in use_as_gpio:
                    self.debug(f"  Setting {pin_name} as GPIO")
                    await self.command_run(tcl.Command(["set_option", f"-use_{pin_name}_as_gpio", "1"]))

            # Filter inputs by type (only Resources have metadata, not VirtualResources)
            hdl_inputs = [r for r in self.inputs
                         if isinstance(r, Resource) and r.file_type in ('vhdl', 'verilog')]
            csr_inputs = [r for r in self.inputs
                         if isinstance(r, Resource) and r.file_type == 'gowin-serdes-init']

            # Add HDL files in dependency order
            self.debug(f"Adding {len(hdl_inputs)} HDL source files...")
            total = len(hdl_inputs)
            for i, resource in enumerate(hdl_inputs):
                # Get metadata attached to this resource
                lib_name = resource.library
                file_type = resource.file_type
                file_path = resource.path

                # Add file (use tcl.String for proper path escaping)
                self.debug(f"  Adding {file_path.name} (lib={lib_name}, type={file_type})")
                await self.command_run(tcl.Command(["add_file", "-type", file_type, str(file_path)]))

                # Set library property (use tcl.String for proper escaping)
                await self.command_run(tcl.Command(["set_file_prop", "-lib", lib_name, str(file_path)]))

                await self.update_progress(i / total)

            # Add SerDes CSR file if present (after HDL files, before constraints)
            for csr_resource in csr_inputs:
                csr_path = csr_resource.path
                self.info(f"Adding SerDes CSR file: {csr_path}")
                await self.command_run(tcl.Command(["set_csr", str(csr_path)]))

            # Add constraint files (even if they don't exist yet)
            for cst_file in [pin_cst_file, timing_sdc_file]:
                await self.command_run(tcl.Command(["add_file", str(cst_file.relative_to(self.output_dir))]))

            # Set project options (defaults + user overrides)
            default_options = {
                "looplimit": 0,
                "print_all_synthesis_warning": 1,
                "gen_text_timing_rpt": 1,
                "rpt_auto_place_io_info": 1,
                "bit_compress": 1,
                "retiming": 1,
                "gen_vhdl_sim_netlist": 1,
            }

            # Merge user options (target.options) with defaults
            user_options = target.get("options", {})
            options = {**default_options, **user_options}

            self.debug(f"Setting {len(options)} project options...")
            for key, value in options.items():
                self.debug(f"  set_option -{key} {value}")
                await self.command_run(tcl.Command(["set_option", f"-{key}", str(value)]))

            # Inject random 32-bit user code (build ID)
            user_code = random.randbytes(4)
            self.info(f"Injecting user_code: {user_code.hex()}")
            await self.command_run(tcl.Command(["set_option", "-user_code", user_code.hex()]))

            self.info(f"Project initialization complete")

        except Exception as e:
            self.error(f"Project init failed with exception: {e}", exc_info=True)
            raise


class Synthesis(LongRunningCommand):
    """Run Gowin synthesis via gw_sh"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        inputs: list,
        outputs: list
    ):
        super().__init__(dispatcher,
            name = "gowin_synthesis",
            session = session,
            command = tcl.Command(["run", "syn"]),
            inputs = inputs,
            outputs = outputs,
            description = "Gowin synthesis"
        )

    async def prepare(self) -> None:
        """Ensure output directory exists before synthesis"""
        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)

class PnR(LongRunningCommand):
    """Run Gowin place & route via gw_sh"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        inputs: list,
        outputs: list
    ):
        super().__init__(dispatcher,
            name = "gowin_pnr",
            session = session,
            command = tcl.Command(["run", "pnr"]),
            inputs = inputs,
            outputs = outputs,
            description = "Gowin PnR"
        )

    async def prepare(self) -> None:
        """Ensure output directory exists before PnR"""
        output = next(self.outputs)
        output.path.parent.mkdir(parents=True, exist_ok=True)

class AggregateConstraints(Task):
    """Aggregate Gowin constraint files of a single type (.cst or .sdc)"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        file_type: str,
        inputs: list,
        outputs: list
    ):
        # Generate description based on file type
        constraint_type = "pin" if file_type == "gowin-cst" else "timing"

        super().__init__(dispatcher,
            f"gowin_aggregate_{constraint_type}_constraints",
            inputs=inputs,
            outputs=outputs,
            description=f"Aggregate Gowin {constraint_type} constraints"
        )
        self.file_type = file_type

    async def work(self) -> None:
        """Aggregate constraint files (iterate over self.inputs with metadata)"""
        # Output file is the single output resource
        output, = self.outputs
        output_file = output.path

        # Filter inputs by file_type from metadata (should all match, only Resources have metadata)
        resources = [r for r in self.inputs
                    if isinstance(r, Resource) and r.file_type == self.file_type]

        # Merge all constraint files
        constraints = []
        for resource in resources:
            content = resource.path.read_text()
            constraints.append(f"# From {resource.path}\n{content}\n")

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text('\n'.join(constraints))

        constraint_type = "pin" if self.file_type == "gowin-cst" else "timing"
        self.info(f"Aggregated {len(resources)} {constraint_type} constraint files to {output_file}")


class SerDesToCsr(Task):
    """Convert Gowin SerDes TOML configuration to CSR file

    Uses the serdes_toml_to_csr tool from Gowin IDE to convert
    a TOML configuration file to a CSR initialization file.

    The tool path is:
    <gowin_dir>/IDE/bin/serdes_toml_to_csr.dist/serdes_toml_to_csr_<klut>.bin

    Where <klut> is "15k", "60k", or "138k" based on device capacity.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        klut_count: str,
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        """Initialize SerDes to CSR conversion task

        Args:
            context: Build context
            inputs: List with single TOML config file resource
            outputs: List with single CSR output file resource
        """
        super().__init__(dispatcher,
            name="gowin_serdes_to_csr",
            inputs=inputs,
            outputs=outputs,
            description="Convert SerDes TOML to CSR"
        )

    async def work(self) -> None:
        """Execute SerDes TOML to CSR conversion"""
        input, = self.inputs
        toml_file = input.path
        output, = self.outputs
        csr_file = output.path

        # Get Gowin tool path
        gowin_path = expand_path(self.dispatcher.get_tool_option("path"))

        # Build tool path
        tool_path = (
            gowin_path / "IDE" / "bin" / "serdes_toml_to_csr.dist" /
            f"serdes_toml_to_csr_{self.dispatcher.device_info.klut_count}.bin"
        )

        if not tool_path.exists():
            raise RuntimeError(
                f"SerDes tool not found at {tool_path}. "
                f"Device may not support SerDes or Gowin installation is incomplete."
            )

        # Ensure output directory exists
        csr_file.parent.mkdir(parents=True, exist_ok=True)

        self.info(f"Converting SerDes config: {toml_file} -> {csr_file}")

        # Run the conversion tool
        # Tool expects: serdes_toml_to_csr_<klut>.bin -o <output.csr> <input.toml>
        cmd = [str(tool_path), '-o', str(csr_file), str(toml_file)]

        process = MessageSubprocess(
            argv = cmd,
            cwd = str(toml_file.parent),
            env = self.dispatcher.tool_env or None,
            parent_reporter = self  # Task is the parent
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"failed with return code {process.returncode}")

        self.info("complete")


class AggregateSynthesisReport(Task):
    """Aggregate Gowin synthesis HTML reports into a single file."""

    # Synthesis reports relative to output_path/impl/gwsynthesis/
    REPORT_FILES = [
        "project_syn.rpt.html",
        "project_syn_resource.html",
    ]

    def __init__(
        self,
        dispatcher: "Dispatcher",
        output_base_name: str,
        build_dir: Path,
        inputs: list,
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="gowin_synthesis_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Gowin synthesis reports",
        )
        self.build_dir = build_dir
        self.output_base_name = output_base_name

    async def work(self) -> None:
        syn_dir = self.build_dir / "impl" / "gwsynthesis"
        pages = []
        for filename in self.REPORT_FILES:
            path = syn_dir / filename.replace("project", self.output_base_name)
            if path.exists():
                pages.append(ReportPage.from_file(path))
            else:
                self.warning(f"Synthesis report not found: {path}")

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_html(pages, title="Gowin Synthesis Report"))
        self.info(f"Aggregated {len(pages)} synthesis reports to {output.path}")


class AggregatePnrReport(Task):
    """Aggregate Gowin PnR HTML reports into a single file."""

    # PnR reports relative to output_path/impl/pnr/
    REPORT_FILES = [
        "project.rpt.html",
        "project.pin.html",
        "project.power.html",
        "project_tr_content.html",
    ]

    def __init__(
        self,
        dispatcher: "Dispatcher",
        output_base_name: str,
        build_dir: Path,
        inputs: list,
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="gowin_pnr_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Gowin PnR reports",
        )
        self.build_dir = build_dir
        self.output_base_name = output_base_name

    async def work(self) -> None:
        pnr_dir = self.build_dir / "impl" / "pnr"
        pages = []
        for filename in self.REPORT_FILES:
            path = pnr_dir / filename.replace("project", self.output_base_name)
            if path.exists():
                pages.append(ReportPage.from_file(path))
            else:
                self.warning(f"PnR report not found: {path}")

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_html(pages, title="Gowin PnR Report"))
        self.info(f"Aggregated {len(pages)} PnR reports to {output.path}")
