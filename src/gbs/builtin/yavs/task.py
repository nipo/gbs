from __future__ import annotations
import re
import shlex

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage


class YavsInvocation(MessageSubprocess):
    """yavs analyze/elab invocation with ``--message-format=short``

    Diagnostics are ``FILE:LINE:COL: severity[CODE]: message``, or
    ``severity[CODE]: message`` when they have no source location.
    """
    # Accept what other simulators accept (e.g. an ``others`` aggregate
    # for an unconstrained target). elab and run read the sources again,
    # so they need it as well as analyze.
    RELAXED = "--relaxed"

    located = re.compile(
        r'^(?P<file>.+?):(?P<line>[0-9]+):(?P<column>[0-9]+): '
        r'(?P<level>[a-z]+)(?:\[(?P<code>[A-Z][0-9]+)\])?: (?P<message>.*)$')
    unlocated = re.compile(
        r'^(?P<level>error|warning|note|help)(?:\[(?P<code>[A-Z][0-9]+)\])?: (?P<message>.*)$')

    level_map = {
        "error": MessageSeverity.ERROR,
        "warning": MessageSeverity.WARNING,
        "note": MessageSeverity.INFO,
        "help": MessageSeverity.INFO,
    }

    @classmethod
    def diagnostic(cls, line: str, default: MessageSeverity) -> ToolMessage:
        m = cls.located.match(line)
        if m:
            return ToolMessage(
                severity=cls.level_map.get(m.group("level"), default),
                origin="yavs",
                identifier=m.group("code"),
                message=m.group("message"),
                file_path=m.group("file"),
                line=int(m.group("line")),
                column=int(m.group("column")))
        m = cls.unlocated.match(line)
        if m:
            return ToolMessage(
                severity=cls.level_map[m.group("level")],
                origin="yavs",
                identifier=m.group("code"),
                message=m.group("message"))
        return ToolMessage(severity=default, origin="yavs", message=line)

    async def stderr_transform(self, lines):
        async for line in lines:
            yield self.diagnostic(line, MessageSeverity.INFO)


class SimulatorInvocation(YavsInvocation):
    """yavs run invocation

    Reports go to standard output as ``** Severity: TIME: message``,
    followed by an indented line locating the process that issued them.
    Runtime errors go to standard error as short diagnostics.
    """
    report = re.compile(r'^\*\* (?P<level>[A-Z][a-z]+): (?P<time>[^ ]+): (?P<message>.*)$')
    process_line = re.compile(
        r'^ +Process (?P<process>[^ ]+)(?: \((?P<file>.+):(?P<line>[0-9]+)\))?$')

    report_level_map = {
        "Note": MessageSeverity.NOTICE,
        "Warning": MessageSeverity.WARNING,
        "Error": MessageSeverity.ERROR,
        "Failure": MessageSeverity.ERROR,
    }

    async def stdout_transform(self, lines):
        pending = None
        async for line in lines:
            if pending is not None:
                m = self.process_line.match(line)
                if m:
                    pending.extended_message = f"Process {m.group('process')}"
                    if m.group("file"):
                        pending.file_path = m.group("file")
                        pending.line = int(m.group("line"))
                    yield pending
                    pending = None
                    continue
                yield pending
                pending = None

            m = self.report.match(line)
            if m:
                pending = ToolMessage(
                    severity=self.report_level_map.get(m.group("level"), MessageSeverity.INFO),
                    origin="simulator",
                    identifier=m.group("time"),
                    message=m.group("message"))
                continue

            yield ToolMessage(severity=MessageSeverity.INFO,
                              origin="simulator",
                              message=line)

        if pending is not None:
            yield pending

    async def stderr_transform(self, lines):
        async for line in lines:
            yield self.diagnostic(line, MessageSeverity.ERROR)


class LibraryFlags:
    """``--work`` and ``--map`` options from library manifests"""

    @staticmethod
    def spec(resource) -> str:
        return f"{resource.library}:{resource.path.resolve().parent}"

    @classmethod
    def maps(cls, resources, exclude: str | None = None) -> list[str]:
        return sorted(f"--map={cls.spec(r)}" for r in resources
                      if r.library != exclude)


class Analyze(Task):
    """yavs analyze of all the sources of a library"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        library_name: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=f"yavs_analyze_{library_name}",
            inputs=inputs,
            outputs=outputs,
            description=f"yavs analyze {library_name}"
        )
        self.library_name = library_name

    async def work(self) -> None:
        manifest, = self.outputs_of_type("yavs-lib")
        workdir = manifest.path.parent
        workdir.mkdir(parents=True, exist_ok=True)

        sources = [str(i.path.resolve()) for i in self.inputs_of_type("vhdl")]
        deps = self.inputs_of_type("yavs-lib")
        analyze_args = list(self.dispatcher.get_tool_option("analyze_args", []))

        process = YavsInvocation(env=self.dispatcher.tool_env or None, argv=[
            self.dispatcher.executable(),
            "--message-format=short",
            "analyze",
            f"--std={self.dispatcher.std_flag}",
            YavsInvocation.RELAXED,
            f"--work={self.library_name}:{workdir.resolve()}",
        ] + LibraryFlags.maps(deps) + analyze_args + sources,
            cwd=workdir)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise process.failure(
                tool="yavs",
                message=f"yavs analyze failed for {self.library_name}: "
                        f"exit code {process.returncode}",
            )

        # A batch whose files are all unchanged leaves the manifest as is.
        manifest.path.touch()


class Elaborate(Task):
    """yavs elab of the top level, then a ``yavs run`` wrapper script"""

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
            name=f"yavs_elaborate_{topcell}",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"yavs elaborate {topcell}"
        )
        self.topcell = topcell
        self.root_library = root_library

    async def work(self) -> None:
        output, = self.outputs
        libs = self.inputs_of_type("yavs-lib")
        roots = [r for r in libs if r.library == self.root_library]
        if not roots:
            raise BuildError(f"Root library {self.root_library} not found in inputs")
        root, = roots

        lib_flags = [f"--work={LibraryFlags.spec(root)}"] \
            + LibraryFlags.maps(libs, exclude=self.root_library)
        std_flags = [f"--std={self.dispatcher.std_flag}", YavsInvocation.RELAXED]
        executable = self.dispatcher.executable()

        process = YavsInvocation(env=self.dispatcher.tool_env or None, argv=[
            executable, "--message-format=short", "elab",
        ] + std_flags + lib_flags + [self.topcell],
            cwd=root.path.parent)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise process.failure(
                tool="yavs",
                message=f"yavs elab failed for {self.topcell}: "
                        f"exit code {process.returncode}",
            )

        run_cmd = [executable, "--message-format=short", "run"] \
            + std_flags + lib_flags + [self.topcell]
        output.path.write_text(
            "#!/bin/sh\n"
            f"exec {' '.join(shlex.quote(a) for a in run_cmd)} \"$@\"\n")
        output.path.chmod(0o755)


class RunSimulation(Task):
    """Run the yavs simulator wrapper, capture its log and status"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list = [],
        outputs: list = [],
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="yavs_run_simulation",
            inputs=inputs,
            outputs=outputs,
            description=f"run {dispatcher.context.project.name}"
        )

    async def work(self) -> None:
        simulator, = self.inputs_of_type("yavs-simulator")

        max_simulation_time = self.dispatcher.config.get("max_simulation_time")
        success_regex = self.dispatcher.config.get("success_regex")
        success_pattern = re.compile(success_regex) if success_regex else None
        run_args = list(self.dispatcher.get_tool_option("run_args", []))

        argv = [str(simulator.path.resolve())]
        if max_simulation_time:
            argv.append(f"--stop-time={max_simulation_time}")
        argv += run_args

        log_path = None
        success_path = None
        for output in self.outputs:
            output.path.parent.mkdir(parents=True, exist_ok=True)
            if output.file_type == "waveform-fst":
                argv.append(f"--wave={output.path.resolve()}")
            elif output.file_type == "simulation-log":
                log_path = output.path
            elif output.file_type == "simulation-success":
                success_path = output.path
            else:
                raise ValueError(f"Unhandled yavs run output {output}")

        success_pattern_found = False
        success_path.unlink(missing_ok=True)

        with open(log_path, "w") as log:
            process = SimulatorInvocation(argv=argv, env=self.dispatcher.tool_env or None)

            async for msg in process:
                await self.add_message_obj(msg)
                log.write(str(msg) + "\n")
                if success_pattern and success_pattern.search(msg.message):
                    success_pattern_found = True

        if success_pattern:
            if success_pattern_found:
                self.info("Success pattern found")
            else:
                self.warning("Expected success pattern not found")
                raise process.failure(
                    tool="yavs",
                    message="Simulation did not match success pattern",
                    log_path=log_path,
                )

        if process.returncode == 0:
            self.info("Simulation done")
        else:
            self.info(f"Simulation return code {process.returncode}")
            if not success_pattern:
                raise process.failure(
                    tool="yavs",
                    message="Simulation failed",
                    log_path=log_path,
                )

        success_path.touch(exist_ok=True)
