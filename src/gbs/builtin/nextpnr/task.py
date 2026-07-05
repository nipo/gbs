"""nextpnr place-and-route tasks"""

from __future__ import annotations
from pathlib import Path
import re

from ...build.task import Task, Resource, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
from ...report_aggregator import TextReport, aggregate_text


class NextpnrInvocation(MessageSubprocess):
    """Message parser for nextpnr output"""

    # nextpnr message format: "Level: message"
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$', re.I)

    level_map = {
        "info": MessageSeverity.INFO,
        "warning": MessageSeverity.WARNING,
        "error": MessageSeverity.ERROR,
        "fatal": MessageSeverity.FATAL,
    }

    async def stderr_transform(self, lines):
        """Parse nextpnr stderr output into ToolMessage objects"""
        async for line in lines:
            match = self.msg_pattern.match(line)
            if match:
                level_str = match.group('level').lower()
                message = match.group('message')
                severity = self.level_map.get(level_str, MessageSeverity.INFO)
                yield ToolMessage(
                    severity=severity,
                    message=message,
                )
            else:
                # Unstructured output
                yield ToolMessage(
                    severity=MessageSeverity.DEBUG,
                    message=line,
                )


class PlaceAndRoute(Task):
    """nextpnr place-and-route task"""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"nextpnr_pnr_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"nextpnr place-and-route {topcell}",
        )

    async def work(self) -> None:
        """Execute nextpnr place-and-route"""
        # Ensure output directory exists
        pnr_outputs = self.outputs_of_type(self.dispatcher.target_config.output_type)
        output = pnr_outputs[0]
        output_path = output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        tc = self.dispatcher.target_config

        # Get input netlist
        netlist_rsrc, = self.inputs_of_type(tc.netlist_type)

        # Build nextpnr command
        topcell = self.dispatcher.context.get_topcell()
        cmd = [
            self.dispatcher._get_nextpnr_executable(),
        ]
        if tc.use_chipdb:
            # Xilinx: chipdb encodes both part and package; top module
            # is read from the JSON netlist so no --top flag is passed.
            cmd += ["--chipdb", str(self.dispatcher.get_chipdb_path())]
            cmd += ["--json", str(netlist_rsrc.path),
                    tc.output_flag, str(output_path)]
        else:
            cmd += [f"--{self.dispatcher.part}",
                    "--package", self.dispatcher.package,
                    "--json", str(netlist_rsrc.path),
                    tc.output_flag, str(output_path),
                    "--top", topcell]

        # Always generate log file
        log_outputs = self.outputs_of_type("nextpnr-log")
        if log_outputs:
            cmd.extend(["--log", str(log_outputs[0].path)])

        # Add constraint files if provided
        constraints = self.inputs_of_type(tc.constraint_type)
        for constraint_res in constraints:
            cmd.extend([tc.constraint_flag, str(constraint_res.path)])
        if not constraints and tc.name == "ice40":
            # Only ice40 supports this permissive flag; xilinx and ecp5
            # will hard-error if no constraint file is provided.
            cmd.append("--pcf-allow-unconstrained")

        # Run nextpnr
        process = NextpnrInvocation(argv=cmd, env=self.dispatcher.tool_env or None)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            log_path = log_outputs[0].path if log_outputs else None
            raise process.failure(
                tool="nextpnr",
                message=f"nextpnr failed with exit code {process.returncode}",
                log_path=log_path,
            )

        self.info("Place-and-route complete")


class AggregatePnrReport(Task):
    """Aggregate nextpnr PnR log into a single tabbed HTML file."""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list[Resource],
        outputs: list[Resource],
    ):
        super().__init__(dispatcher,
            name="nextpnr_pnr_report",
            inputs=inputs,
            outputs=outputs,
            description="Aggregate nextpnr PnR report",
        )

    async def work(self) -> None:
        tabs = []
        for rsrc in self.inputs:
            tabs.append(TextReport.from_file(rsrc.path, title="Place & Route"))

        output, = self.outputs
        output.path.parent.mkdir(parents=True, exist_ok=True)
        output.path.write_text(aggregate_text(tabs, title="nextpnr PnR Report"))
        self.info(f"Aggregated PnR report to {output.path}")
