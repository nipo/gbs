"""nextpnr place-and-route tasks"""

from __future__ import annotations
from pathlib import Path
import re

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage


class NextpnrInvocation(MessageSubprocess):
    """Message parser for nextpnr output"""

    # nextpnr message format: "Level: message"
    msg_pattern = re.compile(r'^(?P<level>Info|Warning|Error|Fatal):\s+(?P<message>.*)$')

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
        output, = self.outputs
        output_path = output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Get input netlist
        netlist_rsrc, = self.inputs_of_type("ice40-netlist-json")

        # Build nextpnr command
        topcell = self.dispatcher.context.get_topcell()
        cmd = [
            self.dispatcher._get_nextpnr_executable(),
            f"--{self.dispatcher.part}",
            "--package", self.dispatcher.package,
            "--json", str(netlist_rsrc.path),
            "--asc", str(output_path),
            "--top", topcell,
        ]

        # Add PCF if provided
        pcfs = self.inputs_of_type("ice40-pcf")
        for pcf_res in pcfs:
            cmd.extend(["--pcf", str(pcf_res.path)])
        if not pcfs:
            cmd.append("--pcf-allow-unconstrained")

        # Run nextpnr
        process = NextpnrInvocation(argv=cmd)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise BuildError(f"nextpnr failed with exit code {process.returncode}")

        self.info("Place-and-route complete")
