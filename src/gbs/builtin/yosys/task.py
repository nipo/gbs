"""Yosys synthesis tasks"""

from __future__ import annotations
from pathlib import Path

from ...build.task import Task, BuildError
from ...build import shell
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
        return [
            self.dispatcher.synth_target,
            "-top",
            str(self.dispatcher.context.get_topcell()),
            ]

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
            "write_json",
            str(output.path.resolve()),
            ]

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
