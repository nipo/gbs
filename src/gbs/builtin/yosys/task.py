"""Yosys synthesis tasks"""

from __future__ import annotations
from pathlib import Path

from ...build.task import Task, Resource, BuildError
from ...build import shell
from ...report_aggregator import TextReport, aggregate_text
from .yosys_interactive import Session
import shlex

class CommandTask(shell.CommandTask):
    def __init__(self,
                 dispatcher: "Dispatcher",
                 name: str,
                 inputs: list = [],
                 outputs: list = [],
                 description: str = ""):
        super().__init__(dispatcher,
                         name = name,
                         session = dispatcher.get_session(),
                         inputs = inputs,
                         outputs = outputs,
                         description = description)

class SimpleCommandTask(shell.SimpleCommandTask):
    def __init__(self,
                 dispatcher,
                 inputs = [],
                 outputs = []):
        super().__init__(dispatcher,
                         session = dispatcher.get_session(),
                         inputs = inputs,
                         outputs = outputs)

class Synthesize(SimpleCommandTask):
    name = "yosys_synth"
    description = "Yosys Synthesis"
    
    def command_get(self) -> list[str]:
        argv = [
            self.dispatcher.synth_target,
            "-top",
            str(self.dispatcher.context.get_topcell()),
            ]
        argv.extend(self.dispatcher.synth_args)
        return argv

class VhdlAnalyze(SimpleCommandTask):
    name = "yosys_ghdl"
    description = "Yosys GHDL Input"

    def command_get(self) -> list[str]:
        argv = ["ghdl"]

        work = None
        for rsrc in self.inputs_of_type("vhdl"):
            lib = rsrc.library

            if work is None or lib != work:
                work = lib
                argv.append(f"--work={lib}")
            argv.append(str(rsrc.path.resolve()))

        argv.append("-e")
        argv.append(str(self.dispatcher.context.get_topcell()))

        return argv

class WriteNetlist(SimpleCommandTask):
    name = "yosys_netlist"
    description = "Yosys Netlist Dump"

    def command_get(self) -> list[str]:
        output, = self.outputs
        return [
            "write_json", "-noscopeinfo",
            str(output.path.resolve()),
            ]

class CaptureCommandTask(CommandTask):
    """Run a Yosys command and capture all output to a file.

    Unlike 'tee', this captures messages from the session's message
    stream which reliably includes all log output.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        name: str,
        command: list[str],
        inputs: list = [],
        outputs: list = [],
        description: str = "",
    ):
        super().__init__(dispatcher,
                         name=name,
                         inputs=inputs,
                         outputs=outputs,
                         description=description)
        self.command = command

    def command_get(self) -> list[str]:
        return self.command

    async def work(self) -> None:
        lines = []
        cmd = self.command_get()
        async for msg in self.session.interact(cmd):
            lines.append(msg.message)
            await self.add_message_obj(msg)

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text('\n'.join(lines) + '\n')


class AggregateSynthesisReport(Task):
    """Aggregate Yosys synthesis reports into a single tabbed HTML file."""

    TAB_TITLES = {
        "yosys-stat": "Statistics",
        "yosys-ltp": "Longest Path",
    }

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="yosys_synthesis_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate Yosys synthesis reports",
        )

    async def work(self) -> None:
        tabs = []
        for rsrc in self.inputs:
            title = self.TAB_TITLES.get(rsrc.file_type, rsrc.path.stem)
            tabs.append(TextReport.from_file(rsrc.path, title=title))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(tabs, title="Yosys Synthesis Report"))
        self.info(f"Aggregated {len(tabs)} reports to {output.path}")


class RawCommand(CommandTask):
    def __init__(
            self,
            dispatcher: "Dispatcher",
            name: str,
            command: str,
            inputs: list = [],
            outputs: list = [],
            description: str = "",
    ):
        super().__init__(dispatcher = dispatcher,
                         name = name,
                         inputs = inputs,
                         outputs = outputs,
                         description = description)
        self.command = command

    def command_get(self) -> list[str]:
        return shlex.split(self.command)
