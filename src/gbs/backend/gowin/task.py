from __future__ import annotations
import random
from pathlib import Path

from ...model.build import BuildContext, Resource, Task
from .gw_sh import *
from .device_info import parse_device_csv

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
        outputs: list
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
            topcell = self.context.project.topcell

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

            # Add HDL files in dependency order (iterate over self.inputs with metadata)
            self.logger.debug(f"Adding {len(self.inputs)} HDL source files...")
            total = len(self.inputs)
            for i, resource in enumerate(self.inputs):
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
