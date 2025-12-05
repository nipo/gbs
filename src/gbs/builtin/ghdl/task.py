from __future__ import annotations
from pathlib import Path
from ...build.task import Task
from ...build.context import BuildContext
from ...build.subprocess import MessageSubprocess

class GhdlInvocation(MessageSubprocess):
    pass

class Import(Task):
    """GHDL import task (ghdl -i + ghdl -a)"""

    def __init__(
        self,
        context: BuildContext,
        library_name: str,
        ghdl_executable: str,
        workdir: Path,
        vhdl_std: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_import_{library_name}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL import {library_name}"
        )
        self.library_name = library_name
        self.ghdl_executable = ghdl_executable
        self.workdir = workdir
        self.vhdl_std = vhdl_std

    async def work(self) -> None:
        """Execute GHDL import"""
        import subprocess

        sources = []
        p_flags = []

        for i in self.inputs:
            if i.metadata["file_type"] == "vhdl":
                sources.append(i.path.resolve())
            elif i.metadata["file_type"] == "ghdl-cf":
                p_flags.append(f"-P{i.path.parent.resolve()}")
            else:
                raise ValueError(f"Unknown input type {i}")

        import_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-i",
            f"--workdir={self.workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in import_process:
            await self.add_message_obj(msg)

        if import_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {import_process.returncode}")

        analyze_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-a",
            f"--workdir={self.workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in analyze_process:
            await self.add_message_obj(msg)

        if analyze_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {analyze_process.returncode}")

class CompileLink(Task):
    """GHDL compile and link task for compiled backends (ghdl -c -e)"""

    def __init__(
        self,
        context: BuildContext,
        topcell: str,
        ghdl_executable: str,
        root_workdir: Path,
        vhdl_std: str,
        root_library: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_link_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL link {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        import subprocess

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.metadata.get("file_type") == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-c", "-O2",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            "-e", self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -c failed: {process.returncode}")

class MakeElab(Task):
    """GHDL make task for mcode/jit backends (ghdl -m / -e + wrapper generator)"""

    def __init__(
        self,
        context: BuildContext,
        topcell: str,
        ghdl_executable: str,
        root_workdir: Path,
        vhdl_std: str,
        root_library: str,
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_make_{topcell}",
            inputs=inputs,
            outputs=outputs,
            description=f"GHDL make {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        import subprocess

        assert len(self.outputs) == 1

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.metadata.get("file_type") == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-m",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -m failed: {process.returncode}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-e",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell
        ])

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -e failed: {process.returncode}")

        # Create run script
        run_cmd = [
            self.ghdl_executable, "-r",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell,
            '"$@"'
        ]

        script_content = f"""#!/bin/sh

exec {' '.join(run_cmd)}
"""
        self.outputs[0].path.write_text(script_content)
        self.outputs[0].path.chmod(0o755)
