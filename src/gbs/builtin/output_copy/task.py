"""Xilinx ISE Task implementations"""

from __future__ import annotations
import asyncio
import random
from pathlib import Path

from ...build.context import BuildContext
from ...build.task import Task
from ...build.message import MessageSeverity, ToolMessage
from ...build.subprocess import MessageSubprocess


class IseSubprocess(MessageSubprocess):
    pass

class IseTask(Task):
    """Base class for ISE tasks

    Provides common functionality for running ISE command-line tools.
    """

    async def run_command(self, cmd: list[str], cwd: Path) -> int:
        """Run an ISE command and capture output

        Args:
            cmd: Command and arguments
            cwd: Working directory

        Returns:
            Return code from the command
        """
        self.logger.info(f"Running: {' '.join(cmd)}")

        process = IseSubprocess(
            argv = cmd,
            cwd = cwd,
        )

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"failed with return code {process.returncode}")

        self.logger.info("complete")


class XstSourceList(Task):
    """Generate XST source file list

    Creates a file listing all HDL sources with their language and library.
    Format: <language> <library> <path>
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_generate_prj",
            inputs=inputs,
            outputs=outputs,
            description="Generate XST project file",
        )

    async def work(self) -> None:
        """Generate the .prj file from HDL sources"""
        prj_file = self.outputs[0].path

        lines = []
        for resource in self.inputs:
            language = resource.metadata.get('language', 'vhdl')
            library = resource.metadata.get('library', 'work')
            file_path = resource.path

            # XST expects language names in lowercase
            lang = language.lower()

            if lang not in ["vhdl", "verilog"]:
                raise ValueError(f"Unsupported language for XST: {lang}")

            lines.append(f"{lang} {library} {file_path}")

        prj_file.parent.mkdir(parents=True, exist_ok=True)
        prj_file.write_text('\n'.join(lines) + '\n')

        self.logger.info(f"Generated {prj_file} with {len(lines)} sources")


class XstRun(IseTask):
    """Run XST from a source file list
    """

    def __init__(
        self,
        context: BuildContext,
        device: str,
        topcell: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_generate_xst",
            inputs=inputs,
            outputs=outputs,
            description="Generate XST command file",
        )
        self.device = device
        self.topcell = topcell

    async def work(self) -> None:
        """Generate the .xst command file"""
        srclist_rsrc, = self.inputs_of_type("ise-xst-sourcelist")
        netlist_rsrc, = self.outputs_of_type("ise-netlist")
        output_dir = netlist_rsrc.path.parent
        xst_file = output_dir / "command.xst"

        # Build XST command content
        lines = [
            f"set -tmpdir {output_dir}",
            f"set -xsthdpdir {output_dir}",
            "run",
            f"-p {self.device}",
            f"-top {self.topcell}",
            f"-ifn {srclist_rsrc.path}",
            f"-ofn {netlist_rsrc.path}",
            "-max_fanout 15",
            "-keep_hierarchy soft",
            "-read_cores yes",
            "-equivalent_register_removal no",
        ]

        xst_file.parent.mkdir(parents=True, exist_ok=True)
        xst_file.write_text('\n'.join(lines) + '\n')

        self.logger.info(f"Generated {xst_file}")

        log_file = output_dir / "xst_run.log"

        cmd = [
            "xst",
            "-ifn", str(xst_file),
            "-ofn", str(log_file),
        ]

        await self.run_command(cmd, xst_file.parent)

class BmmGenerate(Task):
    """Generate placeholder BMM file

    Creates a semantically-empty .bmm file containing only "//".
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_generate_bmm",
            inputs=inputs,
            outputs=outputs,
            description="Generate placeholder BMM file",
        )

    async def work(self) -> None:
        """Generate the placeholder .bmm file"""
        bmm_file = self.outputs[0].path

        bmm_file.parent.mkdir(parents=True, exist_ok=True)
        bmm_file.write_text("//\n")

        self.logger.info(f"Generated placeholder {bmm_file}")


class NetlistConvert(IseTask):
    """Run NGDBUILD to convert XST output netlist to functional netlist for MAP.

    Executes: ngdbuild -quiet -dd <output_dir> <ngc> -uc <ucf>... -bm <bmm> <ngd>
    Produces: .ngd file
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_ngdbuild",
            inputs=inputs,
            outputs=outputs,
            description="Netlist Conversion",
        )

    async def work(self) -> None:
        """Run NGDBUILD"""

        ngc_rsrc, = self.inputs_of_type("ise-netlist")
        ucf_rsrcs = self.inputs_of_type("xilinx-ucf")
        bmm_rsrc, = self.inputs_of_type("ise-bmm")

        ngd_rsrc, = self.outputs_of_type("ise-netlist-functional")

        cmd = [
            "ngdbuild",
            "-quiet",
            "-dd", str(ngd_rsrc.path.parent),
            str(ngc_rsrc.path),
        ]

        # Add UCF files
        for ucf in ucf_rsrcs:
            cmd.extend(["-uc", str(ucf.path)])

        # Add BMM file
        cmd.extend(["-bm", str(bmm_rsrc.path)])

        # Add output file
        cmd.append(str(ngd_rsrc.path))

        ngd_rsrc.path.parent.mkdir(parents=True, exist_ok=True)
        await self.run_command(cmd, ngd_rsrc.path.parent)


class Map(IseTask):
    """Run MAP

    Executes: map -p <device> -ol high ... -w <ngd> -o <map.ncd>
    Produces: .map.ncd and .pcf files
    """

    def __init__(
        self,
        context: BuildContext,
        device: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_map",
            inputs=inputs,
            outputs=outputs,
            description="MAP",
        )
        self.device = device

    async def work(self) -> None:
        """Run MAP"""
        in_ngd, = self.inputs_of_type("ise-netlist-functional")
        out_ncd, = self.outputs_of_type("ise-netlist-partial")
        out_pcf, = self.outputs_of_type("ise-physical-constraints")

        cmd = [
            "map",
            "-p", self.device,
            "-ol", "high",
            "-xe", "c",
            "-mt", "on",
            "-global_opt", "speed",
            "-retiming", "on",
            "-register_duplication", "on",
            "-equivalent_register_removal", "off",
            "-lc", "area",
            "-w",
            str(in_ngd.path),
            "-o", str(out_ncd.path),
        ]

        output_dir = out_ncd.path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        await self.run_command(cmd, output_dir)


class Par(IseTask):
    """Run PAR (Place and Route)

    Executes: par -ol high -xe c -w <map.ncd> -o <par.ncd>
    Produces: .par.ncd file
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_par",
            inputs=inputs,
            outputs=outputs,
            description="PAR (Place and Route)",
        )

    async def work(self) -> None:
        """Run PAR"""
        in_ncd, = self.inputs_of_type("ise-netlist-partial")
        out_ncd, = self.outputs_of_type("ise-netlist-full")

        cmd = [
            "par",
            "-ol", "high",
            "-xe", "c",
            "-w",
            str(in_ncd.path),
            str(out_ncd.path),
        ]

        output_dir = out_ncd.path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        await self.run_command(cmd, output_dir)

class Trce(IseTask):
    """Run TRCE (Timing Report)

    Executes: trce -v 10 <par.ncd> <pcf> -o <twr>
    Produces: .twr timing report
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_trce",
            inputs=inputs,
            outputs=outputs,
            description="TRCE (Timing Analysis)",
        )

    async def work(self) -> None:
        """Run TRCE"""
        in_ncd, = self.inputs_of_type("ise-netlist-full")
        in_pcf, = self.inputs_of_type("ise-physical-constraints")
        out_twr, = self.outputs_of_type("ise-timing-report")

        cmd = [
            "trce",
            "-v", "10",
            str(in_ncd.path),
            str(in_pcf.path),
            "-o", str(out_twr.path),
        ]

        output_dir = out_twr.path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        await self.run_command(cmd, output_dir)

class Bitgen(IseTask):
    """Run BITGEN

    Executes: bitgen -g DriveDone:yes ... -w <par.ncd> <bit>
    Produces: .bit bitstream
    """

    def __init__(
        self,
        context: BuildContext,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name="ise_bitgen",
            inputs=inputs,
            outputs=outputs,
            description="BITGEN",
        )

    async def work(self) -> None:
        """Run BITGEN"""
        in_ncd, = self.inputs_of_type("ise-netlist-full")
        out_bit, = self.outputs_of_type("ise-bitstream")

        # Generate random 32-bit user ID
        user_id = random.getrandbits(32)

        cmd = [
            "bitgen",
            "-g", "DriveDone:yes",
            "-g", "unusedpin:pullnone",
            "-g", "compress",
            "-g", f"UserID:0x{user_id:08x}",
            "-g", "StartupClk:Cclk",
            "-w",
            str(in_ncd.path),
            str(out_bit.path),
        ]

        output_dir = out_bit.path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        await self.run_command(cmd, output_dir)
