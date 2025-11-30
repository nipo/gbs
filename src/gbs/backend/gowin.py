"""Gowin FPGA Synthesis Backend for GBS

This module implements the Gowin synthesis backend that:
1. Synthesizes VHDL/Verilog to netlist (via gw_sh)
2. Aggregates constraints from multiple sources
3. Runs place & route to generate bitstream (via gw_sh)

The backend supports multi-iteration processing:
- First pass: Synthesis (HDL → netlist)
- Second pass: PnR (netlist + constraints → bitstream)

Between passes, other backends (e.g., NSL) can generate constraints
by inspecting the netlist.
"""

from __future__ import annotations
import asyncio
import csv
import logging
import random
import re
from typing import Any, AsyncIterator
from pathlib import Path

from ..model.backend import BaseBackend
from ..model.build import BuildContext, BuildFileSet, BuildResource, Resource, Task, MessageSeverity, ToolMessage


def parse_device_csv(gowin_path: Path, device: str, logger: logging.Logger) -> tuple[str, str]:
    """Parse Gowin device CSV to get device characteristics and set_device parameters

    Args:
        gowin_path: Path to Gowin installation
        device: Device part number from project config (e.g., "GW1NR-LV9QN88PC6/I5")
        logger: Logger for warnings and errors

    Returns:
        Tuple of (part_group, part_number) for set_device command
    """
    csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

    if not csv_path.exists():
        logger.warning(f"Device CSV not found at {csv_path}, using device as-is")
        return ("FPGA", device)  # Fallback

    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)

            # Find matching row (match column 2 with device)
            for row in reader:
                if len(row) < 10:
                    continue  # Skip malformed rows

                # Column 2 (index 1) is the device part number
                if row[1].strip() == device.strip():
                    # Extract device characteristics
                    family = row[3].strip() if len(row) > 3 else ""      # Column 4 (index 3)

                    logger.info(f"Device info: {device} -> family={family}")

                    # For set_device command, use family as part_group
                    # and full device as part_number
                    # (Gowin expects: set_device -name <family> <full_part_number>)
                    return (family, device)

            # Device not found in CSV
            logger.warning(f"Device {device} not found in CSV, using as-is")
            return ("FPGA", device)

    except Exception as e:
        logger.error(f"Error parsing device CSV: {e}")
        return ("FPGA", device)  # Fallback

class ProgressIndication:
    """A progress indication specific to long-running tasks of gw_sh"""
    
    def __init__(self, percent, message):
        self.percent = percent
        self.message = message
    
class Session:
    """Shared gw_sh interactive session with command serialization

    Manages a persistent gw_sh subprocess that maintains synthesis state.
    All commands are serialized via asyncio.Lock since gw_sh is non-concurrent.
    """

    def __init__(self, gw_sh_executable: Path, work_dir: Path, logger: logging.Logger):
        self.gw_sh_executable = gw_sh_executable
        self.work_dir = work_dir
        self.logger = logger
        self.process: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()

    async def _ensure_started(self):
        """Start gw_sh subprocess if not already running"""
        if self.process is not None:
            return

        self.process = await asyncio.create_subprocess_exec(
            str(self.gw_sh_executable),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self.work_dir,
        )

        await self._tcl_send("set tcl_interactive 1")
        async for msg in self._tcl_messages_receive():
            pass

    # Regex patterns for parsing Gowin output
    progress_pattern = re.compile(r'^\[(?P<pct>[0-9]+)%\] (?P<message>.*)$')
    msg_pattern = re.compile(r'^(?P<level>[A-Z]+) +\(?(?P<ex>[A-Z0-9]+)\)? ?: (?P<message>.*)$')
    file_pattern = re.compile(r'^(?P<explaination>[^\(]+)\("(?P<filename>[^"]+)":(?P<line>[0-9]+)\)$')
    prompt = "% "

    # Map Gowin levels to MessageSeverity
    severity_map = {
        'NOTE': MessageSeverity.NOTICE,
        'INFO': MessageSeverity.INFO,
        'WARN': MessageSeverity.WARNING,
        'WARNING': MessageSeverity.WARNING,
        'ERROR': MessageSeverity.ERROR,
        'FATAL': MessageSeverity.FATAL,
    }

    @classmethod
    def log_line_parse(cls, line: str) -> Optional[ToolMessage | ProgressIndication]:
        """Parse a line into a ToolMessage"""
        if not line:
            return None

        match = cls.progress_pattern.match(line)
        if match:
            return ProgressIndication(int(match.group("pct")), match.group("message"))
        
        # Try to match Gowin message format
        match = cls.msg_pattern.match(line)
        if not match:
            # Unstructured output - create DEBUG message
            return ToolMessage(
                severity=MessageSeverity.DEBUG,
                message=line,
            )

        level_str = match.group('level')
        ex_code = match.group('ex')
        message = match.group('message')

        # Get message severity
        severity = cls.severity_map.get(level_str, MessageSeverity.DEBUG)

        # Check if message contains file/line info
        file_match = cls.file_pattern.match(message)
        if not file_match:
            return ToolMessage(
                severity=severity,
                message=message,
                identifier=ex_code,
            )

        explanation = file_match.group('explaination')
        filename = file_match.group('filename')
        line_num = int(file_match.group('line'))

        return ToolMessage(
            severity=severity,
            message=explanation,
            identifier=ex_code,
            file_path=Path(filename),
            line=line_num,
        )

    async def _tcl_send(self, tcl_command: str) -> None:
        """Send TCL command to interpreter.

        This method should be called with lock held.
        """
        self.logger.debug(f"% {tcl_command}")

        # Send command with newline
        self.process.stdin.write(f"{tcl_command}\n".encode('utf-8'))
        await self.process.stdin.drain()

    async def _tcl_messages_receive(self) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Receive messages from the interpreter. End iteration when
        we see prompt.

        This method should be called with lock held.
        """
        buffer = ""
        while True:
            chunk = await self.process.stdout.read(1024)
            if not chunk:
                raise RuntimeError("gw_sh process terminated unexpectedly")

            buffer += chunk.decode('utf-8', errors='replace')

            while True:
                try:
                    line, buffer = buffer.split("\n", 1)
                except ValueError:
                    break

                msg = self.log_line_parse(line)
                if msg:
                    yield msg

            if buffer.lstrip().endswith(self.prompt):
                break
        
    async def command_interact(self, tcl_command: str) -> AsyncIterator[ToolMessage | ProgressIndication]:
        """Send TCL command and yield ToolMessage objects as they arrive (serialized)

        Parses Gowin output and creates ToolMessage instances for structured messages.
        Lines matching "LEVEL (EXnnnn) : message" are parsed into ToolMessage objects.
        Other lines are yielded as DEBUG-level ToolMessage objects.

        Args:
            tcl_command: TCL command to execute

        Yields:
            ToolMessage or gowin-specific progress instances for each line of output
        """

        async with self.lock:
            await self._ensure_started()
            await self._tcl_send(tcl_command)
            async for msg in self._tcl_messages_receive():
                yield msg

    async def close(self):
        """Shutdown gw_sh cleanly"""
        if self.process is not None:
            async with self.lock:
                try:
                    self.process.stdin.write(b"exit\n")
                    await self.process.stdin.drain()
                    await asyncio.wait_for(self.process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
                finally:
                    self.process = None


class GwShTask(Task):
    def __init__(
            self,
            context: BuildContext,
            name: str,
            session: Session,
            inputs: list,
            outputs: list,
            description: str = "",
    ):
        super().__init__(context = context,
                         name = name,
                         inputs = inputs,
                         outputs = outputs,
                         description = description)
        self.session = session

    async def command_interact(self, tcl_command: str) -> AsyncIterator[ToolMessage]:
        """
        Helper that wraps shell task, iterate through messages
        """
        async for msg in self.session.command_interact(tcl_command):
            if isinstance(msg, ProgressIndication):
                await self.update_progress(msg.percent / 100, msg.message)
                continue
            self.add_message_obj(msg)
            yield msg

    async def command_run(self, tcl_command: str) -> None:
        """
        Helper that runs a command and wait for completion
        """
        async for msg in self.command_interact(tcl_command):
            pass

class LongRunningCommand(GwShTask):
    """Run Gowin long running via gw_sh"""

    async def work(self, command) -> None:
        """Run command via gw_sh (project already initialized)"""
        await self.command_run(command)

        self.logger.info(f"{command} finished")

# Task classes for Gowin backend operations

class ProjectInitTask(GwShTask):
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


class SynthesisTask(LongRunningCommand):
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
        
class PnRTask(LongRunningCommand):
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

class AggregateConstraintsTask(Task):
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


class GowinBackend(BaseBackend):
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

        # Define resource paths
        netlist_file = self.output_dir / "impl" / "gwsynthesis" / f"{output_base_name}.vg"
        netlist_resource = context.get_resource(netlist_file)

        # Virtual resource that indicates project has been initialized in gw_sh session
        # This is volatile - the session state doesn't persist across builds
        init_marker_resource = context.get_virtual_resource("gowin_project_init")

        # Check if we have HDL sources
        has_hdl = bool(fileset.filter(file_type="vhdl") or fileset.filter(file_type="verilog"))

        if has_hdl and self._pin_cst_task is None:
            # First call with HDL sources - create all tasks
            self.logger.debug("Creating all Gowin build tasks")
            await self._create_all_tasks(context, fileset, target, output_base_name, netlist_resource, init_marker_resource)

        elif self._pin_cst_task is not None:
            # Subsequent calls - add new constraint files to existing tasks
            await self._update_constraint_inputs(context, fileset)

    async def _create_all_tasks(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        target: dict,
        output_base_name: str,
        netlist_resource: Resource,
        init_marker_resource: Resource
    ) -> None:
        """Create all Gowin build tasks on first call

        Creates: init, synthesis, constraint aggregation (pin + timing), and PnR tasks.
        Stores references to constraint aggregation tasks for dynamic input updates.
        """

        session = self._get_session(context)

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
        init_task = ProjectInitTask(
            context=context,
            session=session,
            gowin_tool=self.gowin_tool,
            output_base_name=output_base_name,
            output_dir=self.output_dir,
            inputs=hdl_input_resources,
            outputs=[init_marker_resource]
        )

        # Create synthesis task
        synth_task = SynthesisTask(
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
        self._pin_cst_task = AggregateConstraintsTask(
            context=context,
            file_type="gowin-cst",
            inputs=cst_input_resources,
            outputs=[pin_cst_resource]
        )

        # Create timing constraint aggregation task (.sdc files)
        # Store reference for dynamic input updates
        self._timing_sdc_task = AggregateConstraintsTask(
            context=context,
            file_type="gowin-sdc",
            inputs=sdc_input_resources,
            outputs=[timing_sdc_resource]
        )

        # Create PnR task (depends on init + netlist + constraints)
        pnr_task = PnRTask(
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

