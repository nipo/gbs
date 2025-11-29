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
import re
from typing import Any
from pathlib import Path

from gbs.model.backend import BaseBackend
from gbs.model.build import BuildContext, BuildFileSet, BuildResource, ExecutorTask, Resource


class GowinSession:
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
            stderr=asyncio.subprocess.PIPE,
            cwd=self.work_dir,
        )

        # Enable interactive mode to get prompts
        self.process.stdin.write(b"set tcl_interactive 1\n")
        await self.process.stdin.drain()

        # Wait for initial prompt
        await self._read_until_prompt()

    async def _read_until_prompt(self) -> str:
        """Read output until '% ' prompt (with or without preceding newline)"""
        output = ""
        while True:
            chunk = await self.process.stdout.read(1)
            if not chunk:
                raise RuntimeError("gw_sh process terminated unexpectedly")

            char = chunk.decode('utf-8', errors='replace')
            output += char

            # Check for '% ' pattern (percent followed by space)
            # Can be '\n% ' (with output) or just '% ' (no output)
            if output.endswith('% '):
                # Remove the '% ' from output
                result = output[:-2]
                # Strip trailing newline if present
                if result.endswith('\n'):
                    result = result[:-1]
                return result

    async def send_command(self, tcl_command: str):
        """Send TCL command and yield response lines as they arrive (serialized)

        Lines are automatically logged at appropriate levels:
        - Lines matching "LEVEL (EXnnnn) : message" are logged at that level
        - Other lines are logged at DEBUG level

        Args:
            tcl_command: TCL command to execute

        Yields:
            Response lines (without prompt or trailing newlines)
        """
        # Regex patterns for parsing Gowin output
        msg_pattern = re.compile(r'^(?P<level>[A-Z]+) +\(?(?P<ex>[0-9]+)\)? ?: (?P<message>.*)$')
        file_pattern = re.compile(r'^(?P<explaination>[^\(]+)\("(?P<filename>[^"]+)":(?P<line>[0-9]+)\)$')

        # Map Gowin levels to Python logging levels
        level_map = {
            'NOTE': logging.INFO,
            'WARN': logging.WARNING,
            'ERROR': logging.ERROR,
        }

        def log_line(line: str):
            """Parse and log a line at the appropriate level"""
            if not line:
                return

            # Try to match Gowin message format
            match = msg_pattern.match(line)
            if match:
                level_str = match.group('level')
                ex_code = match.group('ex')
                message = match.group('message')

                # Get Python logging level
                log_level = level_map.get(level_str, logging.DEBUG)

                # Check if message contains file/line info
                file_match = file_pattern.match(message)
                if file_match:
                    explanation = file_match.group('explaination')
                    filename = file_match.group('filename')
                    line_num = file_match.group('line')
                    self.logger.log(log_level, f"{filename}:{line_num}: {ex_code}, {explanation}")
                else:
                    self.logger.log(log_level, f"{ex_code}: {message}")
            else:
                # Unstructured output - log at DEBUG
                self.logger.debug(line)

        async with self.lock:
            await self._ensure_started()

            self.logger.debug(f"% {tcl_command}")

            # Send command with newline
            self.process.stdin.write(f"{tcl_command}\n".encode('utf-8'))
            await self.process.stdin.drain()

            # Read and yield lines until prompt
            buffer = ""
            while True:
                chunk = await self.process.stdout.read(1)
                if not chunk:
                    raise RuntimeError("gw_sh process terminated unexpectedly")

                char = chunk.decode('utf-8', errors='replace')
                buffer += char

                # Check for '% ' prompt (percent followed by space)
                if buffer.endswith('% '):
                    # Remove prompt from buffer
                    buffer = buffer[:-2]
                    # Strip trailing newline if present
                    if buffer.endswith('\n'):
                        buffer = buffer[:-1]
                    # Process and yield any remaining lines
                    if buffer:
                        for line in buffer.split('\n'):
                            log_line(line)
                            if line:  # Skip empty lines
                                yield line
                    return

                # Yield complete lines as we receive them
                if char == '\n':
                    line = buffer[:-1]  # Remove the newline
                    log_line(line)
                    if line:  # Skip empty lines
                        yield line
                    buffer = ""

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


class GowinBackend(BaseBackend):
    """Gowin FPGA synthesis backend

    Multi-iteration workflow:

    Iteration 1 (if HDL sources present):
      - Synthesis: VHDL/Verilog → netlist.vg
      - Add netlist to fileset

    Iteration 2 (if netlist + constraints present):
      - Aggregate constraints (merge .cst and .sdc files)
      - PnR: netlist + constraints → bitstream

    Other backends (e.g., NSL) can run between iterations to generate
    constraints by inspecting the netlist.

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
        self._session: GowinSession | None = None
        self._synthesis_done = False  # Track if synthesis has been scheduled
        self._pnr_done = False  # Track if PnR has been scheduled
        self._device_info: dict[str, str] | None = None  # Cached device characteristics

    def _get_session(self, context: BuildContext) -> GowinSession:
        """Get or create shared gw_sh session"""
        if self._session is None:
            gowin_config = context.get_tool(self.gowin_tool)
            gowin_path = Path(gowin_config["path"])
            gw_sh = gowin_path / "IDE" / "bin" / "gw_sh"

            if not gw_sh.exists():
                raise RuntimeError(f"gw_sh not found at {gw_sh}")

            self._session = GowinSession(gw_sh, self.output_dir, self.logger)

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

        Args:
            gowin_path: Path to Gowin installation
            device: Device part number from project config (e.g., "GW1NR-LV9QN88PC6/I5")

        Returns:
            Tuple of (part_group, part_number) for set_device command

        Side effects:
            Populates self._device_info with device characteristics:
            - family: Device family name (column 4)
            - package: Package type (column 7)
            - voltage: Voltage variant (column 8)
            - speed: Speed grade (column 9)
        """
        csv_path = gowin_path / "IDE" / "data" / "device" / "device_info.csv"

        if not csv_path.exists():
            self.logger.warning(f"Device CSV not found at {csv_path}, using device as-is")
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
                        package = row[6].strip() if len(row) > 6 else ""     # Column 7 (index 6)
                        voltage = row[7].strip() if len(row) > 7 else ""     # Column 8 (index 7)
                        speed = row[8].strip() if len(row) > 8 else ""       # Column 9 (index 8)

                        # Cache device info for filter variables
                        self._device_info = {
                            "family": family,
                            "package": package,
                            "voltage": voltage,
                            "speed": speed,
                        }

                        self.logger.info(f"Device info: {device} -> family={family}, pkg={package}, v={voltage}, speed={speed}")

                        # For set_device command, use family as part_group
                        # and full device as part_number
                        # (Gowin expects: set_device -name <family> <full_part_number>)
                        return (family, device)

                # Device not found in CSV
                self.logger.warning(f"Device {device} not found in CSV, using as-is")
                return ("FPGA", device)

        except Exception as e:
            self.logger.error(f"Error parsing device CSV: {e}")
            return ("FPGA", device)  # Fallback

    async def process(
        self,
        context: BuildContext,
        fileset: BuildFileSet
    ) -> None:
        """Process HDL sources or run PnR (multi-iteration)

        Iteration 1: Create synthesis task if HDL sources present
        Iteration 2+: Create PnR task if netlist + constraints present
        """

        # Get device from project config
        if not context.project:
            raise ValueError("No project configured")

        device = context.project.raw_config.get("device")
        if not device:
            raise ValueError("Device part number required for Gowin synthesis (add 'device:' to project root)")

        # Get output base name
        output_base_name = self.output_base_name or context.project.topcell

        # Define resource paths
        netlist_file = self.output_dir / "impl" / "gwsynthesis" / f"{output_base_name}.vg"
        netlist_resource = context.get_resource(netlist_file)

        # Check what stage we're in
        has_hdl = bool(fileset.filter(file_type="vhdl") or fileset.filter(file_type="verilog"))
        has_netlist = bool(fileset.filter(file_type="gowin-netlist"))

        if has_hdl and not self._synthesis_done:
            # ===== ITERATION 1: SYNTHESIS =====
            self.logger.debug("Scheduling synthesis task")
            await self._create_synthesis_task(context, fileset, device, output_base_name, netlist_resource)
            self._synthesis_done = True

        elif has_netlist and not self._pnr_done:
            # ===== ITERATION 2+: PNR =====
            self.logger.debug("Scheduling PnR task")
            await self._create_pnr_task(context, fileset, device, output_base_name, netlist_resource)
            self._pnr_done = True

    async def _create_synthesis_task(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        device: str,
        output_base_name: str,
        netlist_resource: BuildResource
    ) -> None:
        """Create synthesis task: HDL → netlist"""

        session = self._get_session(context)

        # Get HDL sources in dependency order
        vhdl_sources = list(fileset.filter(file_type="vhdl"))
        verilog_sources = list(fileset.filter(file_type="verilog"))

        # Get constraint sources (will be created later, but declare them now)
        cst_sources = list(fileset.filter(file_type="gowin-cst"))
        sdc_sources = list(fileset.filter(file_type="gowin-sdc"))

        # Aggregate constraint files
        pin_cst_file = self.output_dir / "aggregate_pins.cst"
        timing_sdc_file = self.output_dir / "aggregate_timing.sdc"

        # Get device info
        gowin_config = context.get_tool(self.gowin_tool)
        gowin_path = Path(gowin_config["path"])
        part_group, part_number = self._parse_device_csv(gowin_path, device)

        # Define synthesis executor
        async def synthesis_executor(ctx, inputs):
            """Run synthesis via gw_sh"""
            try:
                self.logger.info(f"Running Gowin synthesis for {device}")

                # Configure device
                self.logger.debug(f"Configuring device: {part_group} {part_number}")
                async for line in session.send_command(f"set_device -name {part_group} {part_number}"):
                    pass

                # Set top module
                topcell = context.project.topcell
                self.logger.debug(f"Setting top module: {topcell}")
                async for line in session.send_command(f"set_option -top_module {topcell}"):
                    pass

                # Set output base name (for predictable output paths)
                self.logger.debug(f"Setting output base name: {output_base_name}")
                async for line in session.send_command(f"set_option -output_base_name {output_base_name}"):
                    pass

                # Add HDL files in dependency order (use sources from closure, not fileset)
                # The fileset may contain generated files (like the netlist) that shouldn't be inputs
                all_sources = vhdl_sources + verilog_sources

                self.logger.debug(f"Adding {len(all_sources)} HDL source files...")
                for source in all_sources:
                    lib_name = source.library
                    file_type = source.file_type
                    file_path = source.path

                    # Add file
                    self.logger.debug(f"  Adding {file_path.name} (lib={lib_name}, type={file_type})")
                    async for line in session.send_command(f"add_file -type {file_type} {{{file_path}}}"):
                        pass

                    # Set library property
                    async for line in session.send_command(f"set_file_prop -lib {{{lib_name}}} {{{file_path}}}"):
                        pass

                # Add constraint files (even if they don't exist yet)
                for cst_file in [pin_cst_file, timing_sdc_file]:
                    if cst_file.exists() or True:  # Always add placeholder
                        async for line in session.send_command(f"add_file {{{cst_file}}}"):
                            pass  # Ignore output - file might not exist yet

                # Run synthesis
                self.logger.info("Starting synthesis...")
                async for line in session.send_command("run syn"):
                    pass

                self.logger.info(f"Synthesis complete: {netlist_resource.path}")
                return [netlist_resource.path]

            except Exception as e:
                self.logger.error(f"Synthesis failed with exception: {e}", exc_info=True)
                raise

        # Create synthesis task
        input_resources = [context.get_resource(s.path) for s in vhdl_sources + verilog_sources]

        synth_task = ExecutorTask(
            context,
            "gowin_synthesis",
            inputs=input_resources,
            outputs=[netlist_resource],
            executor=synthesis_executor,
            description=f"Gowin synthesis: {device}"
        )

        # Add netlist to fileset for next iteration
        # Use "gowin-netlist" type so NSL backend can find it for CDC analysis
        netlist_br = BuildResource(
            resource=netlist_resource,
            file_type="gowin-netlist",
            library="work",
            is_source=False,
            generated_by=self.name
        )
        fileset.add(netlist_br)

    async def _create_pnr_task(
        self,
        context: BuildContext,
        fileset: BuildFileSet,
        device: str,
        output_base_name: str,
        netlist_resource: BuildResource
    ) -> None:
        """Create PnR task: netlist + constraints → bitstream"""

        session = self._get_session(context)

        # Collect all constraint sources
        cst_sources = list(fileset.filter(file_type="gowin-cst"))
        sdc_sources = list(fileset.filter(file_type="gowin-sdc"))

        # Define aggregated constraint files
        pin_cst_file = self.output_dir / "aggregate_pins.cst"
        timing_sdc_file = self.output_dir / "aggregate_timing.sdc"

        pin_cst_resource = context.get_resource(pin_cst_file)
        timing_sdc_resource = context.get_resource(timing_sdc_file)

        # Define bitstream output
        bitstream_file = self.output_dir / "impl" / "pnr" / f"{output_base_name}.fs"
        bitstream_resource = context.get_resource(bitstream_file)

        # Define constraint aggregation executor
        async def aggregate_constraints_executor(ctx, inputs):
            """Merge all constraint files"""
            self.logger.info("Aggregating constraint files")

            # Merge all .cst files
            pin_constraints = []
            for source in cst_sources:
                content = source.path.read_text()
                pin_constraints.append(f"# From {source.path}\n{content}\n")

            pin_cst_file.parent.mkdir(parents=True, exist_ok=True)
            pin_cst_file.write_text('\n'.join(pin_constraints))
            self.logger.info(f"Aggregated {len(cst_sources)} pin constraint files")

            # Merge all .sdc files
            timing_constraints = []
            for source in sdc_sources:
                content = source.path.read_text()
                timing_constraints.append(f"# From {source.path}\n{content}\n")

            timing_sdc_file.parent.mkdir(parents=True, exist_ok=True)
            timing_sdc_file.write_text('\n'.join(timing_constraints))
            self.logger.info(f"Aggregated {len(sdc_sources)} timing constraint files")

            return [pin_cst_file, timing_sdc_file]

        # Define PnR executor
        async def pnr_executor(ctx, inputs):
            """Run place & route via gw_sh (session continues from synthesis)"""
            self.logger.info("Running Gowin PnR")

            # Note: HDL files and constraints already added during synthesis
            # gw_sh maintains state across commands

            # Run PnR (also generates bitstream)
            self.logger.info("Starting place & route...")
            async for line in session.send_command("run pnr"):
                pass

            self.logger.info(f"Bitstream generated: {bitstream_file}")
            return [bitstream_file]

        # Create constraint aggregation task
        cst_input_resources = [context.get_resource(s.path) for s in cst_sources + sdc_sources]

        cst_task = ExecutorTask(
            context,
            "gowin_aggregate_constraints",
            inputs=cst_input_resources,
            outputs=[pin_cst_resource, timing_sdc_resource],
            executor=aggregate_constraints_executor,
            description="Aggregate Gowin constraints"
        )

        # Create PnR task (depends on netlist + constraints)
        pnr_task = ExecutorTask(
            context,
            "gowin_pnr",
            inputs=[netlist_resource, pin_cst_resource, timing_sdc_resource],
            outputs=[bitstream_resource],
            executor=pnr_executor,
            description=f"Gowin PnR: {device}"
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
