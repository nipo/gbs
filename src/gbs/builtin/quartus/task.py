"""Altera Quartus Task implementations"""

from __future__ import annotations
import re
import shutil
from pathlib import Path

from ...build.task import Task, Resource
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
from ...report_aggregator import TextReport, aggregate_text


class QuartusSubprocess(MessageSubprocess):
    """Message parser for Quartus tool output.

    Quartus messages format:
        Info: message
        Info (12345): message
        Warning (12345): message
        Error (12345): message
        Critical Warning (12345): message

    quartus_map/fit/sta/asm/pfg all write messages in this format to
    stdout. Platform Designer's qsys-generate/qsys-script are different in
    two ways (confirmed against the real tools): they write almost all of
    their output to stderr instead, and each line carries their own
    leading timestamp (e.g. "2026.07.01.17:44:13 Warning: ..."). Without
    handling both, every qsys-generate/qsys-script message — Info and
    Warning alike — ends up misclassified as an error: MessageSubprocess's
    base stderr_transform tags every stderr line ERROR unconditionally,
    regardless of content, and this class previously only overrode
    stdout_transform.
    """

    message_format = re.compile(
        r'^(?:\d{4}\.\d{2}\.\d{2}\.\d{2}:\d{2}:\d{2}\s+)?'
        r'(?P<level>Info|Warning|Error|Critical Warning)'
        r'(?:\s+\((?P<code>\d+)\))?'
        r':\s+(?P<message>.*)$'
    )

    level_map = {
        "Info": MessageSeverity.INFO,
        "Warning": MessageSeverity.WARNING,
        "Critical Warning": MessageSeverity.WARNING,
        "Error": MessageSeverity.ERROR,
    }

    def _classify(self, line: str) -> ToolMessage:
        m = self.message_format.match(line)
        if not m:
            return ToolMessage(severity=MessageSeverity.DEBUG, message=line)

        severity = self.level_map.get(m.group("level"), MessageSeverity.INFO)
        return ToolMessage(
            severity=severity,
            identifier=m.group("code"),
            message=m.group("message"),
        )

    async def stdout_transform(self, lines):
        async for line in lines:
            yield self._classify(line)

    async def stderr_transform(self, lines):
        async for line in lines:
            yield self._classify(line)


class QuartusTask(Task):
    """Base class for Quartus tasks that run standalone executables."""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        quartus_bin: Path,
        project_name: str,
        name: str,
        executable: str,
        inputs: list,
        outputs: list,
        description: str,
        extra_args: list[str] = None,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=name,
            inputs=inputs,
            outputs=outputs,
            description=description,
        )
        self.quartus_bin = quartus_bin
        self.project_name = project_name
        self.executable = executable
        self.extra_args = extra_args or []

    async def work(self) -> None:
        from ...utils import resolve_tool_exe

        cmd = [
            str(resolve_tool_exe(self.quartus_bin / self.executable)),
            *self.extra_args,
            self.project_name,
        ]

        self.info(f"Running {self.executable}")

        process = QuartusSubprocess(
            argv=cmd,
            cwd=self.dispatcher.context.output_path,
            env=self.dispatcher.tool_env or None,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.executable} failed with return code {process.returncode}"
            )

        self.info(f"{self.executable} complete")


class QsysGenerate(Task):
    """Run qsys-generate (Intel Platform Designer system generation)

    Expands a Qsys/Platform Designer .qsys system file into synthesizable
    HDL and a .qip file. Quartus resolves the referenced HDL itself once
    the .qip is listed in the .qsf via a QIP_FILE assignment, so this task
    only needs to track the .qip as an output.

    qsys-generate has no flag to redirect its output location: it always
    writes <system_name>/<system_name>.qip as a sibling directory of
    whatever .qsys file it's given. To keep that output under gbs-build
    instead of next to the (possibly source-tree) input .qsys, this task
    stages a copy of the input next to the expected .qip output first,
    then runs qsys-generate on the copy — the same pattern
    vivado/task.py uses to stage .bd files before generate_target.

    "Generic Component" (IP implementation type) instances in a system
    store their configuration in a per-instance .ip file (IP-XACT) rather
    than in the .qsys itself — qsys-generate looks for these at
    ip/<system_name>/<system_name>_<instance>.ip, resolved relative to
    the .qsys file it's generating, and silently skips generating an
    implementation for any instance whose .ip file it can't find. So an
    ip/<system_name>/ directory next to the source .qsys, if present, is
    staged alongside the copy too.

    Without an associated project, qsys-generate warns "Quartus project
    not specified" on every run — left as-is (not worth the coordination
    cost of pointing it at a real or dummy project; see git history for
    the version that tried).
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        qsys_bin: Path,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="qsys_generate",
            inputs=inputs,
            outputs=outputs,
            description="Qsys system generation",
        )
        self.qsys_bin = qsys_bin

    async def work(self) -> None:
        from ...utils import resolve_tool_exe

        qsys_input, = [r for r in self.inputs
                       if isinstance(r, Resource) and r.file_type == 'quartus-qsys']
        qip_output, = [r for r in self.outputs
                       if isinstance(r, Resource) and r.file_type == 'quartus-qip']

        # qip_output.path is <staging_dir>/<system_name>/<system_name>.qip;
        # stage the .qsys copy as its sibling so qsys-generate's own
        # sibling-directory output lands at qip_output.path.parent.
        staged_qsys = qip_output.path.parent.parent / qsys_input.path.name
        staged_qsys.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(qsys_input.path, staged_qsys)

        # Stage any Generic Component .ip files (ip/<system_name>/) that
        # sit next to the source .qsys, so qsys-generate can find them
        # relative to the staged copy in the same layout.
        system_name = qsys_input.path.stem
        source_ip_dir = qsys_input.path.parent / "ip" / system_name
        if source_ip_dir.is_dir():
            staged_ip_dir = staged_qsys.parent / "ip" / system_name
            shutil.copytree(source_ip_dir, staged_ip_dir, dirs_exist_ok=True)
            self.info(f"Staged Generic Component .ip files from {source_ip_dir}")

        cmd = [
            str(resolve_tool_exe(self.qsys_bin / "qsys-generate")),
            str(staged_qsys),
            "--synthesis=VERILOG",
        ]

        self.info(f"Running qsys-generate on {qsys_input.path.name}")

        process = QuartusSubprocess(
            argv=cmd,
            cwd=self.dispatcher.context.output_path,
            env=self.dispatcher.tool_env or None,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(
                f"qsys-generate failed with return code {process.returncode}"
            )

        self.info("qsys-generate complete")


class QsysScript(Task):
    """Run qsys-script (Intel Platform Designer scripted system creation)

    Runs a Tcl script written against Platform Designer's system scripting
    API to produce a .qsys system file.

    Real-world scripts (e.g. anything exported via Platform Designer's own
    "Export System as Platform Designer script" / qsys-generate
    --export-qsys-script) end with a hardcoded `save_system <name>` using
    the same name as the script file itself — they have no notion of an
    injected output path. And without --quartus-project/
    --new-quartus-project, qsys-script auto-creates a companion Quartus
    project named after the script file, next to it, refusing to run
    again if one already exists (and warns "Quartus project not
    specified" every run either way, left as-is).

    So this task stages a fresh copy of the script under its own
    per-system directory (dispatcher scopes outputs/<system>/<system>.qsys
    per instance, specifically so this can be wiped and rebuilt from
    scratch every run without touching a sibling system's output),
    expects the resulting .qsys to appear as <script_stem>.qsys next to
    the staged copy — same staging pattern QsysGenerate uses for .qsys
    files.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        qsys_bin: Path,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="qsys_script",
            inputs=inputs,
            outputs=outputs,
            description="Qsys scripted system generation",
        )
        self.qsys_bin = qsys_bin

    async def work(self) -> None:
        from ...utils import resolve_tool_exe

        script_input, = [r for r in self.inputs
                         if isinstance(r, Resource) and r.file_type == 'quartus-qsys-script']
        qsys_output, = [r for r in self.outputs
                        if isinstance(r, Resource) and r.file_type == 'quartus-qsys']

        # Wipe and recreate the staging directory: qsys-script's
        # auto-created companion project refuses to run again if a stale
        # .qpf/.qsf from a previous run is still sitting here.
        staging_dir = qsys_output.path.parent
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        staging_dir.mkdir(parents=True)
        staged_script = staging_dir / script_input.path.name
        shutil.copy2(script_input.path, staged_script)

        # Scripts commonly reference Generic Component .ip files by
        # relative path (e.g. add_component ... ip/other_system/foo.ip),
        # resolved relative to the script file itself, and may reach into
        # any system's ip/ subfolder, not just their own — so stage the
        # whole ip/ tree next to the source script, not just one system's
        # slice of it.
        source_ip_dir = script_input.path.parent / "ip"
        if source_ip_dir.is_dir():
            shutil.copytree(source_ip_dir, staging_dir / "ip")

        cmd = [
            str(resolve_tool_exe(self.qsys_bin / "qsys-script")),
            f"--script={staged_script}",
        ]

        self.info(f"Running qsys-script on {script_input.path.name}")

        # The auto-created companion project resolves relative to the
        # --script path regardless of cwd, but a bare `save_system <name>`
        # inside the script itself resolves relative to cwd (confirmed
        # against a real script: it landed in context.output_path, not
        # next to the staged script, until cwd was pointed here) — so cwd
        # has to be the staging dir for the expected qsys_output path to
        # actually be where the .qsys shows up.
        process = QuartusSubprocess(
            argv=cmd,
            cwd=staging_dir,
            env=self.dispatcher.tool_env or None,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(
                f"qsys-script failed with return code {process.returncode}"
            )

        self.info("qsys-script complete")


class ProjectSetup(Task):
    """Generate Quartus project files (.qpf and .qsf)

    Produces minimal .qpf and .qsf files listing:
    - Device part number
    - Top-level entity
    - HDL source files
    - SDC constraint files
    - Pin assignment fragments (appended verbatim)
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        device: str,
        vhdl_std: str,
        project_name: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="quartus_project_setup",
            inputs=inputs,
            outputs=outputs,
            description="Generate Quartus project files",
        )
        self.device = device
        self.vhdl_std = vhdl_std
        self.project_name = project_name

    async def work(self) -> None:
        topcell = self.dispatcher.context.get_topcell()
        output_dir = self.dispatcher.context.output_path
        output_dir.mkdir(parents=True, exist_ok=True)

        # Generate .qpf
        qpf_path = output_dir / f"{self.project_name}.qpf"
        qpf_path.write_text(
            f'PROJECT_REVISION = "{self.project_name}"\n'
        )

        # Generate .qsf
        lines = [
            f'set_global_assignment -name DEVICE {self.device}',
            f'set_global_assignment -name TOP_LEVEL_ENTITY {topcell}',
            f'set_global_assignment -name PROJECT_OUTPUT_DIRECTORY output_files',
        ]

        # VHDL version
        vhdl_version_map = {
            "1993": "VHDL_1993",
            "2008": "VHDL_2008",
        }
        vhdl_version = vhdl_version_map.get(self.vhdl_std, "VHDL_1993")
        lines.append(f'set_global_assignment -name VHDL_INPUT_VERSION {vhdl_version}')

        # Add source files. QIP_FILE must be emitted before SDC_FILE: Quartus
        # sources each IP core's own embedded SDC (which creates that core's
        # internal clocks) while resolving its QIP_FILE assignment, so a
        # user SDC_FILE listed earlier in the .qsf gets evaluated before
        # those clocks exist — confirmed empirically via Quartus warning
        # (22198) "constraints reference clocks before they are created",
        # and a user SDC referencing an IP-internal clock (e.g. a
        # set_false_path/set_clock_groups naming a transceiver's recovered
        # clock) silently matching zero objects despite a correct pattern.
        # Iterating file types in a fixed order (rather than a single pass
        # over self.inputs in whatever order they were added) guarantees
        # this regardless of dispatcher wiring order.
        type_to_assignment = {
            "quartus-qip": "QIP_FILE",
            "vhdl": "VHDL_FILE",
            "verilog": "VERILOG_FILE",
            "quartus-sdc": "SDC_FILE",
        }

        for file_type, assignment in type_to_assignment.items():
            for rsrc in self.inputs:
                if rsrc.file_type != file_type:
                    continue
                if rsrc.file_type == "vhdl" and rsrc.library and rsrc.library != "work":
                    lines.append(
                        f'set_global_assignment -name {assignment} {rsrc.path} '
                        f'-library {rsrc.library}'
                    )
                else:
                    lines.append(
                        f'set_global_assignment -name {assignment} {rsrc.path}'
                    )

                if rsrc.file_type == "quartus-qip":
                    # qsys-generate breaks hardened/catalog IP cores (PLLs,
                    # HPS, EMIF, Generic Components, ...) out into their own
                    # nested .qip files under ip/<system_name>/, rather than
                    # folding them into the top-level .qip. It only
                    # registers those with a Quartus project automatically
                    # when given one via --quartus-project, which GBS
                    # doesn't do, so they need their own QIP_FILE
                    # assignments here too.
                    nested_ip_dir = rsrc.path.parent.parent / "ip" / rsrc.path.parent.name
                    if nested_ip_dir.is_dir():
                        for nested_qip in sorted(nested_ip_dir.rglob("*.qip")):
                            lines.append(
                                f'set_global_assignment -name QIP_FILE {nested_qip}'
                            )

        # Append pin assignment fragments verbatim
        for rsrc in self.inputs:
            if rsrc.file_type == "quartus-pin-assignment":
                fragment = rsrc.path.read_text(errors="replace")
                lines.append(f'# From {rsrc.path.name}')
                lines.append(fragment)

        qsf_path = output_dir / f"{self.project_name}.qsf"
        qsf_path.write_text('\n'.join(lines) + '\n')

        self.info(f"Generated {qpf_path.name} and {qsf_path.name}")


class QuartusMap(QuartusTask):
    """Run quartus_map or quartus_syn (Analysis & Synthesis)

    Pro edition uses quartus_syn; Lite/Standard use quartus_map.
    """

    def __init__(self, dispatcher, quartus_bin, project_name, inputs, outputs,
                 executable="quartus_map"):
        super().__init__(
            dispatcher=dispatcher,
            quartus_bin=quartus_bin,
            project_name=project_name,
            name="quartus_map",
            executable=executable,
            inputs=inputs,
            outputs=outputs,
            description="Quartus Analysis & Synthesis",
        )


class QuartusFit(QuartusTask):
    """Run quartus_fit (Fitter / Place & Route)"""

    def __init__(self, dispatcher, quartus_bin, project_name, inputs, outputs):
        super().__init__(
            dispatcher=dispatcher,
            quartus_bin=quartus_bin,
            project_name=project_name,
            name="quartus_fit",
            executable="quartus_fit",
            inputs=inputs,
            outputs=outputs,
            description="Quartus Fitter",
        )


class QuartusSta(QuartusTask):
    """Run quartus_sta (Timing Analysis)"""

    def __init__(self, dispatcher, quartus_bin, project_name, inputs, outputs):
        super().__init__(
            dispatcher=dispatcher,
            quartus_bin=quartus_bin,
            project_name=project_name,
            name="quartus_sta",
            executable="quartus_sta",
            inputs=inputs,
            outputs=outputs,
            description="Quartus Timing Analysis",
        )


class QuartusAsm(QuartusTask):
    """Run quartus_asm (Assembler / Bitstream generation)"""

    def __init__(self, dispatcher, quartus_bin, project_name, inputs, outputs):
        super().__init__(
            dispatcher=dispatcher,
            quartus_bin=quartus_bin,
            project_name=project_name,
            name="quartus_asm",
            executable="quartus_asm",
            inputs=inputs,
            outputs=outputs,
            description="Quartus Assembler",
        )

class QuartusPfgConvert(Task):
    """Run quartus_pfg (Programming file generator) to convert a .sof to another format

    Used for both quartus-jam (STAPL) and quartus-rbf (Raw Binary File)
    outputs — the command is identical either way (quartus_pfg -c <sof>
    <output>); the output file's extension alone tells quartus_pfg what
    to produce.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        title: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name=name,
            inputs=inputs,
            outputs=outputs,
            description=title,
        )
        self.quartus_bin = dispatcher._get_quartus_bin()
        self.executable = "quartus_pfg" if dispatcher.is_pro else "quartus_cpf"

    async def work(self) -> None:
        sof_input, = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type == 'quartus-sof']
        output, = self.outputs

        output.path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            str(self.quartus_bin / self.executable),
            "-c", str(sof_input.path), str(output.path)
        ]

        self.info(f"Running {self.executable}")

        process = QuartusSubprocess(
            argv=cmd,
            cwd=self.dispatcher.context.output_path,
            env=self.dispatcher.tool_env or None,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.executable} failed with return code {process.returncode}"
            )

        self.info(f"{self.executable} complete")

        self.info(f"{self.executable} complete")


class QuartusProjectExport(Task):
    """Export the generated Quartus project to a requested .qpf file path

    Pure file copy, no subprocess — the quartus-project output type exists
    specifically so a project can be produced without running any of the
    synthesis pipeline.

    The requested output IS the .qpf file (e.g. path: adc_bringup.qpf
    means you get exactly that file, not a directory containing one named
    differently). The .qpf is regenerated with PROJECT_REVISION set to
    the requested filename's stem rather than copied verbatim — a bare
    copy would leave it pointing at the internal "project" revision name
    gbs-build/ uses internally, which Quartus uses to locate the matching
    .qsf, silently breaking the pairing under the new name. The .qsf
    itself needs no changes (it never references the project name), so
    it's copied as-is to a sibling file with the same stem.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="quartus_project_export",
            inputs=inputs,
            outputs=outputs,
            description="Export Quartus project",
        )

    async def work(self) -> None:
        qsf_input, = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type == 'quartus-qsf']
        qpf_output, = self.outputs

        qpf_output.path.parent.mkdir(parents=True, exist_ok=True)

        project_name = qpf_output.path.stem
        qpf_output.path.write_text(f'PROJECT_REVISION = "{project_name}"\n')

        qsf_dest = qpf_output.path.with_suffix(".qsf")
        shutil.copy2(qsf_input.path, qsf_dest)

        self.info(f"Exported Quartus project to {qpf_output.path} (+ {qsf_dest.name})")


class AggregateReport(Task):
    """Aggregate Quartus report files into a single tabbed HTML file."""

    TAB_TITLES = {
        "quartus-map-report": "Synthesis",
        "quartus-map-summary": "Synthesis Summary",
        "quartus-fit-report": "Fitter",
        "quartus-fit-summary": "Fitter Summary",
        "quartus-pin-report": "Pin Assignment",
        "quartus-sta-report": "Timing",
        "quartus-sta-summary": "Timing Summary",
        "quartus-flow-report": "Flow",
        "quartus-asm-report": "Assembler",
    }

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        title: str,
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name=name,
            inputs=inputs,
            outputs=outputs,
            description=f"Aggregate {title}",
        )
        self.title = title

    async def work(self) -> None:
        tabs = []
        for rsrc in self.inputs:
            tab_title = self.TAB_TITLES.get(rsrc.file_type, rsrc.path.stem)
            tabs.append(TextReport.from_file(rsrc.path, title=tab_title))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(tabs, title=self.title))
        self.info(f"Aggregated {len(tabs)} reports to {output.path}")
