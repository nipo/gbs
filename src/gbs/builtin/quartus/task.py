"""Altera Quartus Task implementations"""

from __future__ import annotations
import re
import shutil
from pathlib import Path

from ...build.task import Task, Resource
from ...build.subprocess import MessageSubprocess
from ...build import tcl
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
    """

    message_format = re.compile(
        r'^(?P<level>Info|Warning|Error|Critical Warning)'
        r'(?:\s+\((?P<code>\d+)\))?'
        r':\s+(?P<message>.*)$'
    )

    level_map = {
        "Info": MessageSeverity.INFO,
        "Warning": MessageSeverity.WARNING,
        "Critical Warning": MessageSeverity.WARNING,
        "Error": MessageSeverity.ERROR,
    }

    async def stdout_transform(self, lines):
        async for line in lines:
            m = self.message_format.match(line)
            if not m:
                yield ToolMessage(severity=MessageSeverity.DEBUG, message=line)
                continue

            severity = self.level_map.get(m.group("level"), MessageSeverity.INFO)
            yield ToolMessage(
                severity=severity,
                identifier=m.group("code"),
                message=m.group("message"),
            )


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
    API to produce a .qsys system file. qsys-script has no command-line
    flag to name the output file directly — the script itself must call
    save_system to write it. GBS injects the expected path via the
    gbs_qsys_output_file Tcl variable (using --cmd, which runs before
    --script in the same interpreter session), and the script must end
    with `save_system $gbs_qsys_output_file`.
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

        qsys_output.path.parent.mkdir(parents=True, exist_ok=True)

        set_output_cmd = tcl.Command([
            "set", "gbs_qsys_output_file", tcl.String(str(qsys_output.path)),
        ])

        cmd = [
            str(resolve_tool_exe(self.qsys_bin / "qsys-script")),
            f"--cmd={set_output_cmd}",
            f"--script={script_input.path}",
        ]

        self.info(f"Running qsys-script on {script_input.path.name}")

        process = QuartusSubprocess(
            argv=cmd,
            cwd=self.dispatcher.context.output_path,
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

        # Add source files
        type_to_assignment = {
            "vhdl": "VHDL_FILE",
            "verilog": "VERILOG_FILE",
            "quartus-sdc": "SDC_FILE",
            "quartus-qip": "QIP_FILE",
        }

        for rsrc in self.inputs:
            assignment = type_to_assignment.get(rsrc.file_type)
            if assignment:
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

class SofJamConvert(Task):
    """Run quartus_pfg (Programming file generator)"""

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
            description="Jam convert",
        )
        self.quartus_bin = dispatcher._get_quartus_bin()
        self.executable = "quartus_pfg" if dispatcher.is_pro else "quartus_cpf"

    async def work(self) -> None:
        sof_input, = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type in ('quartus-sof')]
        jam_output, = [r for r in self.outputs
                       if isinstance(r, Resource) and r.file_type == 'quartus-jam']

        jam_output.path.parent.mkdir(parents=True, exist_ok=True)
        
        cmd = [
            str(self.quartus_bin / self.executable),
            "-c", str(sof_input.path), str(jam_output.path)
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
