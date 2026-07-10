"""XDC transpile task.

Evaluates the Vivado XDC constraint files through yosys' embedded TCL
interpreter and writes the reduced nextpnr-xilinx constraint file. The
interpreter is hosted by yosys because it is guaranteed to be present in
the yosys -> nextpnr flow that produced the netlist; the transpiler
logic itself lives in :mod:`.transpiler`.
"""

from __future__ import annotations
import json
from pathlib import Path

from ...build.task import Task, BuildError
from ...build.subprocess import MessageSubprocess
from ...ui.messages import MessageSeverity, ToolMessage
from . import transpiler


class YosysTclInvocation(MessageSubprocess):
    """yosys run whose stdout/stderr are only kept for diagnostics.

    The transpiler exchanges data with the interpreter through a record
    file, so yosys' own console output is demoted to debug level and
    surfaces only when the run fails.
    """

    async def stdout_transform(self, lines):
        async for line in lines:
            yield ToolMessage(severity=MessageSeverity.DEBUG, message=line)

    async def stderr_transform(self, lines):
        async for line in lines:
            yield ToolMessage(severity=MessageSeverity.DEBUG, message=line)


class Transpile(Task):
    """Translate Vivado XDC files into a nextpnr-xilinx constraint file."""

    def __init__(
        self,
        dispatcher: "Dispatcher",
        netlist,
        xdc_inputs: list,
        output,
        port_properties: frozenset[str],
    ):
        topcell = dispatcher.context.get_topcell()
        super().__init__(
            dispatcher=dispatcher,
            name=f"xdc_transpile_{topcell}",
            inputs=[],
            outputs=[output],
            description=f"Transpile XDC for {topcell}",
        )
        # The netlist supplies the port universe but is also consumed by
        # place-and-route, so it must stay in the pending queue.
        self.add_input(netlist, consume=False)
        for xdc in xdc_inputs:
            self.add_input(xdc)
        self.netlist = netlist
        self.xdc_inputs = list(xdc_inputs)
        self.output = output
        self.port_properties = port_properties

    async def work(self) -> None:
        output_path = self.output.path
        output_path.parent.mkdir(parents=True, exist_ok=True)

        topcell = self.dispatcher.context.get_topcell()
        data = json.loads(self.netlist.path.read_text())
        ports = transpiler.NetlistPorts.from_json(data, top_hint=topcell)

        xdc_paths = [x.path for x in self.xdc_inputs]
        preamble_path = output_path.parent / "xdc_transpile.tcl"
        records_path = output_path.parent / "xdc_transpile_records.txt"
        preamble_path.write_text(
            transpiler.build_preamble(ports, xdc_paths, records_path)
        )

        self.info(
            f"Transpiling {len(xdc_paths)} XDC file(s) to nextpnr "
            f"constraints via yosys TCL"
        )

        yosys = self.dispatcher.get_yosys_executable()
        process = YosysTclInvocation(
            argv=[yosys, "-q", "-p", f"tcl {preamble_path}"],
            env=self.dispatcher.tool_env or None,
        )
        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise process.failure(
                tool="yosys",
                message=(
                    f"yosys failed to evaluate the XDC (exit code "
                    f"{process.returncode}); the constraint transpiler needs "
                    f"a yosys built with TCL support (the 'tcl' command)."
                ),
            )

        if not records_path.exists():
            raise BuildError(
                f"XDC transpile produced no record file at {records_path}; "
                f"yosys did not run the transpile script."
            )

        records = transpiler.parse_records(records_path.read_text())
        try:
            text, diagnostics = transpiler.emit_nextpnr_xdc(
                records, self.port_properties
            )
        except transpiler.TranspileError as exc:
            raise BuildError(f"XDC evaluation failed: {exc}") from exc

        for diag in diagnostics:
            if diag.level == "warning":
                self.warning(diag.message)
            else:
                self.info(diag.message)

        output_path.write_text(text)
        self.info(f"Wrote nextpnr constraints: {output_path.name}")
