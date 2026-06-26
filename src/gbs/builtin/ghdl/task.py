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


def make_executable(path: Path) -> None:
    """Give the simulator output the execute permission on POSIX systems.

    Applies whether the output is a real binary (compiled backends) or a
    shell wrapper invoking ghdl -r (mcode/jit). On Windows a file's
    runnability is governed by its extension (and PATHEXT), not by a
    permission bit, and os.chmod cannot set an execute bit there, so this is
    a no-op. The execute bits are added to the file's existing mode so its
    read/write bits are left untouched.
    """
    if sys.platform == "win32":
        return
    mode = path.stat().st_mode
    path.chmod(mode | 0o111)


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
            description=f"import {library_name}"
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
                raise import_process.failure(
                    tool="ghdl",
                    message=f"ghdl {cmd} failed for library {self.library_name}",
                )

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
        """Compile a C source into a shared library for VHPIDIRECT.

        VHPIDIRECT C is plain C, called directly by the elaborated design and
        linked into the simulator; it is not a VPI plugin. It must be built
        with the C compiler alone. Routing it through ghdl --vpi-compile /
        --vpi-link pulls in -lghdlvpi, which is both unnecessary here and
        absent from some GHDL distributions (e.g. oss-cad-suite), so the link
        fails with "cannot find -lghdlvpi".
        """
        c, = self.inputs_of_type("ghdl-vhpidirect-c")
        so, = self.outputs_of_type("ghdl-vhpidirect-lib")

        so.path.parent.mkdir(parents=True, exist_ok=True)
        obj_path = so.path.with_suffix('.o')

        # Include directories the source's repository attached to it (e.g. the
        # VHPIDIRECT support headers a backend ships separately from GHDL).
        include_flags = [
            f"-I{Path(d).resolve()}" for d in c.metadata.get("include_dirs", [])
        ]

        compile_process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv=[
            self.compiler, "-c",
            "-fPIC",  # Position-independent code for shared library
        ] + include_flags + [
            str(c.path.resolve()),
            "-o", str(obj_path)
        ])

        async for msg in compile_process:
            await self.add_message_obj(msg)

        if compile_process.returncode != 0:
            raise compile_process.failure(
                tool=self.compiler,
                message=f"C compilation failed for {c.path.name}",
            )

        link_process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv=[
            self.compiler, "-shared",
            str(obj_path),
            "-o", str(so.path)
        ])

        async for msg in link_process:
            await self.add_message_obj(msg)

        if link_process.returncode != 0:
            raise link_process.failure(
                tool=self.compiler,
                message=f"Linking failed for {c.path.name}",
            )

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
            description=f"link {dispatcher.context.project.name}"
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

        # The cf input for the root library lives in the shared content
        # cache. Materialize a per-project elaboration workdir hardlinked
        # from it so this project's elaboration writes don't collide with
        # another project sharing the same cached analysis.
        root_cf = next(
            (r for r in self.inputs_of_type("ghdl-cf") if r.library == self.root_library),
            None,
        )
        if root_cf is None:
            raise BuildError(f"No cf input for root library {self.root_library}")
        elab_workdir = self.dispatcher.library_elaboration_workdir(root_cf.path.parent)
        self.dispatcher.materialize_library_workdir(root_cf.path.parent, elab_workdir)

        # -P for every non-root library points at its shared cache workdir;
        # --workdir for the root library points at the per-project copy.
        flags = []
        for res in self.inputs:
            if res.file_type != "ghdl-cf":
                continue
            if res.library == self.root_library:
                flags.append(f"--workdir={elab_workdir.resolve()}")
                flags.append(f"-P{elab_workdir.resolve()}")
            else:
                flags.append(f"-P{res.path.parent.resolve()}")

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
            raise process.failure(
                tool="ghdl",
                message=f"ghdl -c failed for topcell {self.topcell}",
            )

        make_executable(out_path)

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
            description=f"make {dispatcher.context.project.name}"
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

        # Per-project elaboration workdir for the root library, materialized
        # from the shared cache so that ghdl -m / -e / -r write here without
        # interfering with another project sharing the same cached analysis.
        root_cf = next(
            (r for r in self.inputs_of_type("ghdl-cf") if r.library == self.root_library),
            None,
        )
        if root_cf is None:
            raise BuildError(f"No cf input for root library {self.root_library}")
        elab_workdir = self.dispatcher.library_elaboration_workdir(root_cf.path.parent)
        self.dispatcher.materialize_library_workdir(root_cf.path.parent, elab_workdir)
        elab_workdir_str = str(elab_workdir.resolve())

        # -P points to shared cache for non-root libs; --workdir + an extra -P
        # gives ghdl access to the root library at its per-project location.
        p_flags = []
        for res in self.inputs:
            if res.file_type != "ghdl-cf":
                continue
            if res.library == self.root_library:
                p_flags.append(f"-P{elab_workdir_str}")
            else:
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
            ghdl_executable, "-m",
            f"--workdir={elab_workdir_str}",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + elaborate_args + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise process.failure(
                tool="ghdl",
                message=f"ghdl -m failed for topcell {self.topcell}",
            )

        process = GhdlInvocation(env=self.dispatcher.tool_env or None, argv = [
            ghdl_executable, "-e",
            f"--workdir={elab_workdir_str}",
            f"--std={self.dispatcher.ghdl_vhdl_version}",
        ] + elaborate_args + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ], cwd = out_path.parent)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise process.failure(
                tool="ghdl",
                message=f"ghdl -e failed for topcell {self.topcell}",
            )

        # Build load flags for VHPIDIRECT libraries
        load_flags = []
        for lib_res in self.inputs_of_type("ghdl-vhpidirect-lib"):
            load_flags.append(f"--load={lib_res.path.resolve()}")

        run_cmd = [
            ghdl_executable, "-r",
            f"--workdir={elab_workdir_str}",
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

        make_executable(out_path)


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
            description=f"run {dispatcher.context.project.name}"
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
                argv.append(f"--wave={output.path.resolve()}")
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
                raise process.failure(
                    tool="ghdl-sim",
                    message="Simulation did not match success pattern",
                    log_path=log_path,
                )

        if process_success:
            self.info("Simulation done")
        else:
            self.info(f"Simulation return code {process.returncode}")
            if not success_pattern:
                raise process.failure(
                    tool="ghdl-sim",
                    message="Simulation failed",
                    log_path=log_path,
                )

        success_path.touch(exist_ok = True)
