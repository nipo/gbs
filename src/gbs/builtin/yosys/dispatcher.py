"""Yosys Dispatcher - FPGA synthesis"""

from __future__ import annotations
from pathlib import Path

from ...utils import expand_path
from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from .yosys_interactive import Session
from . import task


class YosysDispatcher(BaseDispatcher):
    """Yosys synthesis dispatcher

    This dispatcher takes GHDL library intermediates (.cf files) and:
    1. Creates a persistent yosys interactive session
    2. Reads the design using the GHDL plugin
    3. Applies user-defined transformation steps
    4. Runs target-specific synthesis (e.g., synth_ice40)
    5. Writes output netlist
    """

    def __init__(
            self,
            context: BuildContext,
            synth_target: str,
            vhdl_std: str = "1993",
            yosys_tool: str = "yosys",
            steps: list[str] = None,
    ):
        super().__init__(context, f"yosys-{synth_target}", tool_name=yosys_tool)
        self.synth_target = synth_target
        self.yosys_tool = yosys_tool
        self.steps = steps or []
        self._yosys_executable: str | None = None
        self._session: Session | None = None

        self.vhdl_std = vhdl_std

        self.output_rsrc = None
        self.vhdl_ingress = None
        self.vhdl_ingress_stamp = None
        self.intermediate = []
        self.synthesize_stamp = None
        self.write_netlist = None
        
        self._session = Session(str(self._get_yosys_executable()), self.context.output_path)

    def _get_yosys_executable(self) -> str:
        """Get yosys executable path (cached)

        Returns:
            Executable path
        """
        if self._yosys_executable is None:
            tool_config = self.context.get_tool(self.yosys_tool, required=False)
            if tool_config:
                executable = tool_config.get("executable", "yosys")
            else:
                executable = "yosys"
            self._yosys_executable = expand_path(executable)
            self.debug(f"Using yosys executable: {self._yosys_executable}")

        return self._yosys_executable

    def get_session(self) -> Session:
        """Get the yosys interactive session

        Returns:
            Yosys session instance
        """
        return self._session

    async def process(self) -> None:
        """Synthesize design using yosys"""

        if self.output_rsrc is None:
            # Determine output file type based on synth_target
            output_type = f"{self.synth_target.replace('synth_', '')}-netlist-json"
            output_path = self.context.output_path / "netlist.json"
            self.output_rsrc = self.context.get_resource(
                output_path,
                file_type=output_type,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )

            # Add output to pending queue
            self.context.add_pending(self.output_rsrc)

        if not self.vhdl_ingress_stamp:
            self.vhdl_ingress_stamp = self.context.get_stamp("vhdl_ingress.stamp")
            self.vhdl_ingress = task.VhdlAnalyze(self, outputs = [self.vhdl_ingress_stamp])

        if self.synthesize_stamp is None:
            self.synthesize_stamp = self.context.get_stamp("synthesize.stamp")
            self.synthesize = task.Synthesize(self,
                                              inputs = [self.vhdl_ingress_stamp],
                                              outputs = [self.synthesize_stamp])

            # Always generate stat and ltp reports after synthesis
            stat_resource = self.context.get_resource(
                self.context.output_path / "stat.txt",
                file_type="yosys-stat",
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )
            task.CaptureCommandTask(self,
                name="yosys_stat",
                command=["stat"],
                inputs=[self.synthesize_stamp],
                outputs=[stat_resource],
                description="Yosys Statistics")

            ltp_resource = self.context.get_resource(
                self.context.output_path / "ltp.txt",
                file_type="yosys-ltp",
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )
            task.CaptureCommandTask(self,
                name="yosys_ltp",
                command=["ltp"],
                inputs=[self.synthesize_stamp],
                outputs=[ltp_resource],
                description="Yosys Longest Path")

            # Aggregate on demand
            for dest in self.context.filter_pending(file_type="yosys-synthesis-report"):
                task.AggregateSynthesisReport(
                    dispatcher=self,
                    inputs=[stat_resource, ltp_resource],
                    outputs=[dest],
                )

        if self.write_netlist is None:
            self.write_netlist = task.WriteNetlist(self,
                                                   inputs = [self.synthesize_stamp],
                                                   outputs = [self.output_rsrc])

        if not self.intermediate:
            prev = self.vhdl_ingress_stamp
            for i, step in enumerate(self.steps):
                output = self.context.get_stamp(f"yosys_intermediate_{i}.stamp")
                t = task.RawCommand(self,
                                    name = f"yosys_intermediate_{i}",
                                    description = f"Yosys Command '{step}'",
                                    command = step,
                                    inputs = [prev],
                                    outputs = [output])
                self.intermediate.append(output)
                prev = self.intermediate[-1]
        self.write_netlist.add_input(self.intermediate[-1])
        
        # Get libraries in dependency order and process VHDL sources
        for library_name, library_files in self.context.get_pending_by_library_ordered():
            if library_name is None:
                continue

            for resource in library_files:
                if resource.file_type != "vhdl":
                    continue

                self.vhdl_ingress.add_input(resource)
