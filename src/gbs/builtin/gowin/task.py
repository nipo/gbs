from __future__ import annotations
import asyncio
import random
from pathlib import Path

from ...build.context import BuildContext
from ...build.task import Resource, Task, ExecutorTask
from .gw_sh import *
from .device_info import parse_device_csv, get_device_info
from ...build.subprocess import MessageSubprocess

class ProjectInit(GwShCommand):
    """Initialize Gowin project in gw_sh session"""

    def __init__(
        self,
        context: BuildContext,
        session: Session,
        gowin_tool: str,
        output_base_name: str,
        output_dir: Path,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context,
            name = "gowin_project_init",
            session = session,
            inputs = inputs,
            outputs = outputs,
            description = f"Gowin project init"
        )
        self.gowin_tool = gowin_tool
        self.output_base_name = output_base_name
        self.output_dir = output_dir

    async def work(self) -> None:
        """Initialize Gowin project in gw_sh"""
        try:
            # Compute values from context
            target = self.context.project.raw_config.get("target", {})
            device = target.get("part")
            topcell = self.context.get_topcell()

            # Prepare paths
            self.output_dir.mkdir(parents=True, exist_ok=True)

            # Get Gowin tool config and parse device CSV
            gowin_config = self.context.get_tool(self.gowin_tool)
            gowin_path = Path(gowin_config["path"])
            part_group, part_number = parse_device_csv(gowin_path, device, self.logger)

            # Compute constraint file paths
            pin_cst_file = self.output_dir / "aggregate_pins.cst"
            timing_sdc_file = self.output_dir / "aggregate_timing.sdc"

            self.logger.info(f"Initializing Gowin project for {device}")

            # Configure device
            self.logger.debug(f"Configuring device: {part_group} {part_number}")
            await self.command_run(f"set_device -name {part_group} {part_number}")

            # Set top module
            self.logger.debug(f"Setting top module: {topcell}")
            await self.command_run(f"set_option -top_module {topcell}")

            # Set output base name (for predictable output paths)
            self.logger.debug(f"Setting output base name: {self.output_base_name}")
            await self.command_run(f"set_option -output_base_name {self.output_base_name}")

            # Process vendor-specific options
            # use_as_gpio: list of pin names to configure as GPIO
            use_as_gpio = target.get("use_as_gpio", [])
            if use_as_gpio:
                self.logger.debug(f"Configuring GPIO pins: {use_as_gpio}")
                for pin_name in use_as_gpio:
                    self.logger.debug(f"  Setting {pin_name} as GPIO")
                    await self.command_run(f"set_option -use_{pin_name}_as_gpio 1")

            # Filter inputs by type
            hdl_inputs = [r for r in self.inputs if r.metadata.get('file_type') in ('vhdl', 'verilog')]
            csr_inputs = [r for r in self.inputs if r.metadata.get('file_type') == 'gowin-serdes-init']

            # Add HDL files in dependency order
            self.logger.debug(f"Adding {len(hdl_inputs)} HDL source files...")
            total = len(hdl_inputs)
            for i, resource in enumerate(hdl_inputs):
                # Get metadata attached to this resource
                lib_name = resource.metadata.get('library')
                file_type = resource.metadata.get('file_type')
                file_path = resource.path

                # Add file
                self.logger.debug(f"  Adding {file_path.name} (lib={lib_name}, type={file_type})")
                await self.command_run(f"add_file -type {file_type} {{{file_path}}}")

                # Set library property
                await self.command_run(f"set_file_prop -lib {{{lib_name}}} {{{file_path}}}")

                await self.update_progress(i / total)

            # Add SerDes CSR file if present (after HDL files, before constraints)
            for csr_resource in csr_inputs:
                csr_path = csr_resource.path
                self.logger.info(f"Adding SerDes CSR file: {csr_path}")
                await self.command_run(f"set_csr {{{csr_path}}}")

            # Add constraint files (even if they don't exist yet)
            for cst_file in [pin_cst_file, timing_sdc_file]:
                if cst_file.exists() or True:  # Always add placeholder
                    await self.command_run(f"add_file {{{cst_file.relative_to(self.output_dir)}}}")

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

            self.logger.debug(f"Setting {len(options)} project options...")
            for key, value in options.items():
                self.logger.debug(f"  set_option -{key} {value}")
                await self.command_run(f"set_option -{key} {value}")

            # Inject random 32-bit user code (build ID)
            user_code = random.randbytes(4)
            self.logger.info(f"Injecting user_code: {user_code.hex()}")
            await self.command_run(f"set_option -user_code {{{user_code.hex()}}}")

            self.logger.info(f"Project initialization complete")

        except Exception as e:
            self.logger.error(f"Project init failed with exception: {e}", exc_info=True)
            raise


class Synthesis(LongRunningCommand):
    """Run Gowin synthesis via gw_sh"""

    def __init__(
        self,
        context: BuildContext,
        session: Session,
        inputs: list,
        outputs: list
    ):
        super().__init__(
            context,
            name = "gowin_synthesis",
            session = session,
            inputs = inputs,
            outputs = outputs,
            description = "Gowin synthesis"
        )
        
    async def work(self) -> None:
        """Run synthesis via gw_sh (project already initialized)"""
        self.outputs[0].path.parent.mkdir(parents=True, exist_ok=True)
        return await super().work("run syn")
        
class PnR(LongRunningCommand):
    """Run Gowin place & route via gw_sh"""

    def __init__(
        self,
        context: BuildContext,
        session: Session,
        inputs: list,
        outputs: list
    ):
        super().__init__(
            context,
            name = "gowin_pnr",
            session = session,
            inputs = inputs,
            outputs = outputs,
            description = "Gowin PnR"
        )

    async def work(self) -> None:
        """Run place & route via gw_sh (project already initialized)"""
        self.outputs[0].path.parent.mkdir(parents=True, exist_ok=True)
        return await super().work("run pnr")

class AggregateConstraints(Task):
    """Aggregate Gowin constraint files of a single type (.cst or .sdc)"""

    def __init__(
        self,
        context: BuildContext,
        file_type: str,
        inputs: list,
        outputs: list
    ):
        # Generate description based on file type
        constraint_type = "pin" if file_type == "gowin-cst" else "timing"

        super().__init__(
            context,
            f"gowin_aggregate_{constraint_type}_constraints",
            inputs=inputs,
            outputs=outputs,
            description=f"Aggregate Gowin {constraint_type} constraints"
        )
        self.file_type = file_type

    async def work(self) -> None:
        """Aggregate constraint files (iterate over self.inputs with metadata)"""
        # Output file is the single output resource
        output_file = self.outputs[0].path

        # Filter inputs by file_type from metadata (should all match)
        resources = [r for r in self.inputs if r.metadata.get('file_type') == self.file_type]

        # Merge all constraint files
        constraints = []
        for resource in resources:
            content = resource.path.read_text()
            constraints.append(f"# From {resource.path}\n{content}\n")

        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text('\n'.join(constraints))

        constraint_type = "pin" if self.file_type == "gowin-cst" else "timing"
        self.logger.info(f"Aggregated {len(resources)} {constraint_type} constraint files to {output_file}")


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
        context: BuildContext,
        gowin_tool: str,
        klut_count: str,
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        """Initialize SerDes to CSR conversion task

        Args:
            context: Build context
            gowin_tool: Tool identifier for Gowin installation lookup
            klut_count: Device klut category ("15k", "60k", "138k")
            inputs: List with single TOML config file resource
            outputs: List with single CSR output file resource
        """
        super().__init__(
            context,
            name="gowin_serdes_to_csr",
            inputs=inputs,
            outputs=outputs,
            description="Convert SerDes TOML to CSR"
        )
        self.gowin_tool = gowin_tool
        self.klut_count = klut_count

    async def work(self) -> None:
        """Execute SerDes TOML to CSR conversion"""
        toml_file = self.inputs[0].path
        csr_file = self.outputs[0].path

        # Get Gowin tool path
        gowin_config = self.context.get_tool(self.gowin_tool)
        gowin_path = Path(gowin_config["path"])

        # Build tool path
        tool_path = (
            gowin_path / "IDE" / "bin" / "serdes_toml_to_csr.dist" /
            f"serdes_toml_to_csr_{self.klut_count}.bin"
        )

        if not tool_path.exists():
            raise RuntimeError(
                f"SerDes tool not found at {tool_path}. "
                f"Device may not support SerDes or Gowin installation is incomplete."
            )

        # Ensure output directory exists
        csr_file.parent.mkdir(parents=True, exist_ok=True)

        self.logger.info(f"Converting SerDes config: {toml_file} -> {csr_file}")

        # Run the conversion tool
        # Tool expects: serdes_toml_to_csr_<klut>.bin -o <output.csr> <input.toml>
        cmd = [str(tool_path), '-o', str(csr_file), str(toml_file)]

        process = MessageSubprocess(
            argv = cmd,
            cwd = str(toml_file.parent),
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"failed with return code {process.returncode}")

        self.logger.info("complete")
