from __future__ import annotations
from pathlib import Path
from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
import re
import asyncio
import shlex
import subprocess
import sys

class GhdlInvocation(MessageSubprocess):
    error_line = re.compile(r'(?P<file>[^:]+):(?P<line>[0-9]+):(?P<column>[0-9]+):(?P<level>[a-z]+):(?P<message>.+)$')

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
    }

    async def stderr_transform(self, lines):
        multiline = None
        async for line in lines:
            if multiline and line.startswith(" "):
                if multiline.extended_message:
                    multiline.extended_message += "\n"+line
                else:
                    multiline.extended_message = line
                continue

            if multiline:
                yield multiline
                multiline = None
                
            m = self.error_line.match(line)
            if not m:
                yield ToolMessage(severity = MessageSeverity.INFO,
                                  message = line)
                continue

            severity = self.level_map.get(m.group("level").lower(), MessageSeverity.INFO)
            multiline = ToolMessage(severity = severity,
                                    origin = "ghdl",
                                    message = m.group("message"),
                                    file_path = m.group("file"),
                                    line = int(m.group("line")),
                                    column = int(m.group("column")))

        if multiline:
            yield multiline

class Import(Task):
    """GHDL import task (ghdl -i + ghdl -a)"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        library_name: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"ghdl_import_{library_name}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL import {library_name}"
        )
        self.library_name = library_name

    async def work(self) -> None:
        """Execute GHDL import"""
        sources = []
        p_flags = []
        ghdl_executable = self.dispatcher._get_ghdl_executable()
        analyze_args = list(self.dispatcher.get_tool_option("analyze_args", []))

        cf_out, = self.outputs_of_type("ghdl-cf")
        workdir = cf_out.path.parent

        for i in self.inputs:
            if i.file_type == "vhdl":
                sources.append(i.path.resolve())
            elif i.file_type == "ghdl-cf":
                i.path.parent.mkdir(parents=True, exist_ok=True)
                p_flags.append(f"-P{i.path.parent.resolve()}")
            else:
                raise ValueError(f"Unknown input type {i}")
        workdir.mkdir(parents=True, exist_ok=True)

        for cmd in ["-i", "-a"]:
            import_process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
                ghdl_executable, cmd,
                f"--workdir={workdir.resolve()}",
                f"--std={self.dispatcher.ghdl_vhdl_version}",
                f"--work={self.library_name}",
            ] + analyze_args + p_flags + sources)

            async for msg in import_process:
                await self.add_message_obj(msg)

            if import_process.returncode != 0:
                raise BuildError(f"ghdl {cmd} failed for {self.library_name}: {import_process.returncode}")

class VHPIDirectCompile(Task):
    """GHDL VHPIDIRECT C compilation task"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        compiler: str = "gcc",
        inputs: list = None,
        outputs: list = None,
    ):
        src = inputs[0].path
        stem = src.stem
        super().__init__(
            dispatcher=dispatcher,
            name=f"vhpidirect_compile_{stem}",
            inputs=inputs,
            outputs=outputs,
            description=f"Compile VHPIDIRECT {stem}"
        )
        self.compiler = compiler

    async def work(self) -> None:
        """Compile C source to shared library for VHPIDIRECT"""
        # Ensure output directory exists
        c, = self.inputs_of_type("ghdl-vhpidirect-c")
        so, = self.outputs_of_type("ghdl-vhpidirect-lib")
        ghdl_executable, _ = self.dispatcher._get_ghdl_config()

        so.path.parent.mkdir(parents=True, exist_ok=True)

        # Compile C source to object file using GHDL's wrapper
        obj_path = so.path.with_suffix('.o')

        compile_process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv=[
            ghdl_executable, "--vpi-compile",
            self.compiler, "-c",
            "-fPIC",  # Position-independent code for shared library
            str(c.path.resolve()),
            "-o", str(obj_path)
        ])

        async for msg in compile_process:
            await self.add_message_obj(msg)

        if compile_process.returncode != 0:
            raise BuildError(f"C compilation failed for {c.path.name}: {compile_process.returncode}")

        # Link to shared library using GHDL's wrapper
        link_process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv=[
            ghdl_executable, "--vpi-link",
            self.compiler, "-shared",
            str(obj_path),
            "-o", str(so.path)
        ])

        async for msg in link_process:
            await self.add_message_obj(msg)

        if link_process.returncode != 0:
            raise BuildError(f"Linking failed for {c.path.name}: {link_process.returncode}")

        # Clean up object file
        obj_path.unlink(missing_ok=True)

class CompileLink(Task):
    """GHDL compile and link task for compiled backends (ghdl -c -e)"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        topcell: str,
        root_library: str,
        inputs: list = None,
        outputs: list = None,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"ghdl_link_simulator",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"GHDL link {topcell}"
        )
        self.topcell = topcell
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        assert len(list(self.outputs)) == 1
        ghdl_executable, _ = self.dispatcher._get_ghdl_config()
        elaborate_args = list(self.dispatcher.get_tool_option("elaborate_args", []))

        output, = self.outputs
        out_path = output.path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Build -P flags for all libraries
        flags = []
        for res in self.inputs:
            if res.file_type == "ghdl-cf":
                flags.append(f"-P{res.path.parent.resolve()}")
                if res.library == self.root_library:
                    flags.append(f"--workdir={res.path.parent.resolve()}")

        # Build linker flags for VHPIDIRECT libraries
        for lib_res in self.inputs_of_type("ghdl-vhpidirect-lib"):
            # Link the library directly (absolute path)
            lib_path = lib_res.path.resolve()
            flags.append(f"-Wl,{lib_path}")

        process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
            ghdl_executable, "-c", "-O2",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + elaborate_args + flags + [
            f"--work={self.root_library}",
            "-o", str(out_path),
            "-e", self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise BuildError(f"ghdl -c failed: {process.returncode}")

class MakeElab(Task):
    """GHDL make task for mcode/jit backends (ghdl -m / -e + wrapper generator)"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        topcell: str,
        root_library: str,
        inputs: list = None,
        outputs: list = None,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"ghdl_make_{topcell}",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"GHDL make {topcell}"
        )
        self.topcell = topcell
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        ghdl_executable, _ = self.dispatcher._get_ghdl_config()
        elaborate_args = list(self.dispatcher.get_tool_option("elaborate_args", []))
        run_args = list(self.dispatcher.get_tool_option("run_args", []))

        assert len(list(self.outputs)) == 1
        output, = self.outputs
        out_path = output.path
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.file_type == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
            ghdl_executable, "-m",
            f"--workdir={self.dispatcher.library_workdir(self.root_library).resolve()}",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + elaborate_args + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise BuildError(f"ghdl -m failed: {process.returncode}")

        process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
            ghdl_executable, "-e",
            f"--workdir={self.dispatcher.library_workdir(self.root_library).resolve()}",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + elaborate_args + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ], cwd = out_path.parent)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise BuildError(f"ghdl -e failed: {process.returncode}")

        # Build load flags for VHPIDIRECT libraries
        load_flags = []
        for lib_res in self.inputs_of_type("ghdl-vhpidirect-lib"):
            load_flags.append(f"--load={lib_res.path.resolve()}")

        run_cmd = [
            ghdl_executable, "-r",
            f"--workdir={self.dispatcher.library_workdir(self.root_library).resolve()}",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell,
        ] + load_flags + run_args

        if sys.platform == "win32":
            # %% escapes a literal % in batch files; %* forwards all caller args.
            cmd_str = subprocess.list2cmdline(run_cmd).replace("%", "%%")
            script_content = f"@echo off\r\n{cmd_str} %*\r\n"
            out_path.write_text(script_content)
        else:
            cmd_str = " ".join(shlex.quote(arg) for arg in run_cmd)
            script_content = f'#!/bin/sh\n\nexec {cmd_str} "$@"\n'
            out_path.write_text(script_content)
            out_path.chmod(0o755)


class SimulatorInvocation(MessageSubprocess):
    ghdl_assert_re = re.compile(r"^(?P<path>.*):(?P<line>[0-9]+):(?P<col>[0-9]+):@(?P<time>[0-9]+.s):\(assertion (?P<level>[^\)]+)\): (?P<msg>.*)$")

    name_map = dict(NOTE = "NOTICE", FAILURE = "ERROR")
    
    def line_transform(self, line, default_severity):
        m = self.ghdl_assert_re.match(line)
        if m:
            sev = self.name_map.get(m.group("level").upper(),
                                    m.group("level").upper())
            return ToolMessage(
                severity = MessageSeverity[sev],
                origin = "simulator",
                message = m.group("msg"),
                file_path = m.group("path"),
                line = int(m.group("line")),
                column = int(m.group("col")))

        return ToolMessage(severity = default_severity,
                           origin = "simulator",
                           message = line)

    async def stderr_transform(self, lines):
        async for line in lines:
            yield self.line_transform(line, MessageSeverity.ERROR)

    async def stdout_transform(self, lines):
        async for line in lines:
            yield self.line_transform(line, MessageSeverity.INFO)

class RunSimulation(Task):
    """GHDL simulation run task that executes simulator and captures outputs"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list = [],
        outputs: list = [],
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="ghdl_run_simulation",
            inputs=inputs,
            outputs=outputs,
            description="GHDL run simulation"
        )

    async def work(self) -> None:
        """Execute GHDL simulator and capture outputs"""

        simulator, = self.inputs_of_type("ghdl-simulator")

        max_simulation_time = self.dispatcher.config.get("max_simulation_time")
        success_regex = self.dispatcher.config.get("success_regex")
        success_pattern = re.compile(success_regex) if success_regex else None
        run_args = list(self.dispatcher.get_tool_option("run_args", []))

        # Build command line arguments
        argv = [str(simulator.path.resolve())]

        # Add stop-time if specified
        if max_simulation_time:
            argv.append(f"--stop-time={max_simulation_time}")

        argv += run_args

        for output in self.outputs:
            output.path.parent.mkdir(parents=True, exist_ok=True)
            file_type = output.file_type

            if file_type == "waveform-vcd":
                argv.append(f"--vcd={output.path.resolve()}")
            elif file_type == "waveform-ghw":
                argv.append(f"--ghw={output.path.resolve()}")
            elif file_type == "waveform-fst":
                argv.append(f"--fst={output.path.resolve()}")
            elif file_type == "simulation-log":
                log_path = output.path
            elif file_type == "simulation-success":
                success_path = output.path

        # Collect log output and messages in parallel
        success_pattern_found = False
        process_success = True
        success_path.unlink(missing_ok = True)
        
        with open(log_path, "w") as log:
            # Run simulator
            process = SimulatorInvocation(argv=argv, env=self.dispatcher.tool_env or None)

            async for msg in process:
                # Add message to task
                await self.add_message_obj(msg)

                # Collect for log file if requested
                log.write(msg.message + "\n")

                # Check for success pattern if specified
                if success_pattern and success_pattern.search(msg.message):
                    success_pattern_found = True

            # Check return code
            process_success = process.returncode == 0

        if success_pattern:
            if success_pattern_found:
                self.info("Success pattern found")
            else:
                self.warning("Expected success pattern not found")
                raise BuildError(f"Simulation failed")

        if process_success:
            self.info("Simulation done")
        else:
            self.info(f"Simulation return code {process.returncode}")
            if not success_pattern:
                raise BuildError(f"Simulation failed")

        success_path.touch(exist_ok = True)
