from __future__ import annotations
from pathlib import Path
from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
import re

class NvcInvocation(MessageSubprocess):
    """Message parser for NVC output

    NVC error format: file:line:column: level: message
    Example: test.vhd:10:15: error: syntax error
    """
    line1 = re.compile(r'\*\* (?P<level>[A-Z][a-z]+): (?P<message>.+)$')
    line2 = re.compile(r' +> (?P<file>[^:]+):(?P<line>[0-9]+)')

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
        "note": MessageSeverity.INFO,
    }

    async def stderr_transform(self, lines):
        """Parse NVC stderr output into structured messages"""
        multiline = None
        async for line in lines:
            m = self.line1.match(line)
            if m:
                # Yield previous message if we have one
                if multiline:
                    yield multiline
                    multiline = None
                severity = self.level_map.get(m.group("level").lower(), MessageSeverity.INFO)
                multiline = ToolMessage(severity = severity,
                                        origin = "nvc",
                                        message = m.group("message"))
                continue

            m = self.line2.match(line)
            if m and multiline:
                multiline.file_path = m.group("file")
                multiline.line = int(m.group("line"))
                continue

            if multiline:
                if multiline.extended_message:
                    multiline.extended_message += '\n' + line
                else:
                    multiline.extended_message = line
                continue

            if multiline:
                yield multiline
            yield ToolMessage(severity = MessageSeverity.INFO,
                              origin="nvc",
                              message=line)

        # Yield final message if we have one
        if multiline:
            yield multiline


class Analyze(Task):
    """NVC analyze task (nvc -a)

    Analyzes VHDL sources into a library work directory.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        library_name: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"nvc_analyze_{library_name}",
            inputs=inputs,
            outputs=outputs,
            description=f"NVC analyze {library_name}"
        )
        self.library_name = library_name

    async def work(self) -> None:
        """Execute NVC analyze"""
        sources = []
        lib_flags = []
        nvc_executable = self.dispatcher._get_nvc_config()

        lib_marker, = self.outputs_of_type("nvc-lib")
        workdir = lib_marker.path.parent

        # Track library search paths (avoid duplicates)
        lib_search_paths = set()

        # Process inputs
        for i in self.inputs:
            if i.file_type == "vhdl":
                sources.append(i.path.resolve())
            elif i.file_type == "nvc-lib":
                # Dependency library - NVC needs parent directory in -L flag
                # The library is stored as parentdir/libname/, so we need parentdir
                dep_workdir = i.path.parent.resolve()
                lib_search_paths.add(dep_workdir.parent)
            else:
                raise ValueError(f"Unknown input type {i}")

        # Build -L flags from unique search paths
        for search_path in sorted(lib_search_paths):
            lib_flags.append(f"-L{search_path}")

        # Create work directory
        workdir.parent.mkdir(parents=True, exist_ok=True)

        # Build analyze command
        # nvc [GLOBAL_OPTIONS] -a [ANALYSIS_OPTIONS] FILE...
        # --std and --work are global options and must come before -a
        # --relaxed disables certain pedantic rule checks
        analyze_process = NvcInvocation(env=self.dispatcher.tool_env or None, argv=[
            nvc_executable,
            f"--std={self.dispatcher.vhdl_std}",
            f"--work={self.library_name}",
        ] + lib_flags + [
            "-a",
            "--relaxed",  # Disable pedantic checks for library compatibility
        ] + [str(s) for s in sources],
        cwd=workdir.parent  # Run from parent so library is created as ./libname
        )

        async for msg in analyze_process:
            await self.add_message_obj(msg)

        if analyze_process.returncode != 0:
            raise BuildError(f"nvc -a failed for {self.library_name}: {analyze_process.returncode}")


class Elaborate(Task):
    """NVC elaborate task (nvc -e)

    Elaborates a top-level design unit and creates an executable wrapper script.
    """

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
            name=f"nvc_elaborate_{topcell}",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"NVC elaborate {topcell}"
        )
        self.topcell = topcell
        self.root_library = root_library

    async def work(self) -> None:
        """Execute NVC elaborate and create run script"""
        assert len(list(self.outputs)) == 1
        nvc_executable = self.dispatcher._get_nvc_config()

        output, = self.outputs
        out_path = output.path
#        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Build library flags for all dependencies
        # Track library search paths (avoid duplicates)
        lib_search_paths = set()
        root_workdir = None

        for res in self.inputs:
            if res.file_type == "nvc-lib":
                lib = res.library
                workdir = res.path.parent.resolve()
                # NVC needs parent directory in -L flag
                lib_search_paths.add(workdir.parent)

                if lib == self.root_library:
                    root_workdir = workdir

        # Build -L flags from unique search paths
        lib_flags = []
        for search_path in sorted(lib_search_paths):
            lib_flags.append(f"-L{search_path}")

        if root_workdir is None:
            raise BuildError(f"Root library {self.root_library} not found in inputs")

        # Elaborate command
        # nvc [GLOBAL_OPTIONS] -e [ELAB_OPTIONS] topcell
        # --std and --work are global options
        elab_process = NvcInvocation(env=self.dispatcher.tool_env or None, argv=[
            nvc_executable,
            f"--std={self.dispatcher.vhdl_std}",
            f"--work={self.root_library}",
        ] + lib_flags + [
            "-e",
            self.topcell
        ],
        cwd=root_workdir.parent  # Run from parent directory
        )

        async for msg in elab_process:
            await self.add_message_obj(msg)

        if elab_process.returncode != 0:
            raise BuildError(f"nvc -e failed for {self.topcell}: {elab_process.returncode}")

        # Create run script wrapper
        # NVC creates an executable in the work directory, we create a wrapper script
        run_cmd = [
            nvc_executable,
            f"--std={self.dispatcher.vhdl_std}",
            f"--work={self.root_library}",
        ] + lib_flags + [
            "-r",
            self.topcell,
            '"$@"'  # Pass through command-line arguments
        ]

        script_content = f"""#!/bin/sh

cd {root_workdir.parent}
exec {' '.join(run_cmd)}
"""
        out_path.write_text(script_content)
        out_path.chmod(0o755)
