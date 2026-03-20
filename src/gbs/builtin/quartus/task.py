"""Altera Quartus Task implementations"""

from __future__ import annotations
import re
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
        cmd = [
            str(self.quartus_bin / self.executable),
            *self.extra_args,
            self.project_name,
        ]

        self.info(f"Running {self.executable}")

        process = QuartusSubprocess(
            argv=cmd,
            cwd=self.dispatcher.context.output_path,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(
                f"{self.executable} failed with return code {process.returncode}"
            )

        self.info(f"{self.executable} complete")


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
