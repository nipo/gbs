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
        self.vhdl_std = vhdl_std

    async def work(self) -> None:
        """Execute GHDL import"""
        sources = []
        p_flags = []

        cf_out, = self.outputs_of_type("ghdl-cf")
        workdir = cf_out.path.parent
        
        for i in self.inputs:
            if i.metadata["file_type"] == "vhdl":
                sources.append(i.path.resolve())
            elif i.metadata["file_type"] == "ghdl-cf":
                i.path.parent.mkdir(parents=True, exist_ok=True)
                p_flags.append(f"-P{i.path.parent.resolve()}")
            else:
                raise ValueError(f"Unknown input type {i}")
        workdir.mkdir(parents=True, exist_ok=True)

        import_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-i",
            f"--workdir={workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in import_process:
            await self.add_message_obj(msg)

        if import_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {import_process.returncode}")

        analyze_process = GhdlInvocation(argv = [
            self.ghdl_executable, "-a",
            f"--workdir={workdir.resolve()}",
            f"--std={self.vhdl_std}",
            f"--work={self.library_name}",
        ] + p_flags + sources)

        async for msg in analyze_process:
            await self.add_message_obj(msg)

        if analyze_process.returncode != 0:
            raise RuntimeError(f"ghdl -i failed for {self.library_name}: {analyze_process.returncode}")

class VHPIDirectCompile(Task):
    """GHDL VHPIDIRECT C compilation task"""

    def __init__(
        self,
        context: BuildContext,
        ghdl_executable: str,
        compiler: str = "gcc",
        inputs: list = None,
        outputs: list = None,
    ):
        src = inputs[0].path
        stem = src.stem
        super().__init__(
            context=context,
            name=f"vhpidirect_compile_{stem}",
            inputs=inputs,
            outputs=outputs,
            description=f"Compile VHPIDIRECT {stem}"
        )
        self.ghdl_executable = ghdl_executable
        self.compiler = compiler

    async def work(self) -> None:
        """Compile C source to shared library for VHPIDIRECT"""
        # Ensure output directory exists
        c, = self.inputs_of_type("ghdl-vhpidirect-c")
        so, = self.outputs_of_type("ghdl-vhpidirect-lib")

        so.path.parent.mkdir(parents=True, exist_ok=True)

        # Compile C source to object file using GHDL's wrapper
        obj_path = so.path.with_suffix('.o')

        compile_process = GhdlInvocation(argv=[
            self.ghdl_executable, "--vpi-compile",
            self.compiler, "-c",
            "-fPIC",  # Position-independent code for shared library
            str(c.path.resolve()),
            "-o", str(obj_path)
        ])

        async for msg in compile_process:
            await self.add_message_obj(msg)

        if compile_process.returncode != 0:
            raise RuntimeError(f"C compilation failed for {c.path.name}: {compile_process.returncode}")

        # Link to shared library using GHDL's wrapper
        link_process = GhdlInvocation(argv=[
            self.ghdl_executable, "--vpi-link",
            self.compiler, "-shared",
            str(obj_path),
            "-o", str(so.path)
        ])

        async for msg in link_process:
            await self.add_message_obj(msg)

        if link_process.returncode != 0:
            raise RuntimeError(f"Linking failed for {c.path.name}: {link_process.returncode}")

        # Clean up object file
        obj_path.unlink(missing_ok=True)

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
        inputs: list = None,
        outputs: list = None,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_link_simulator",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"GHDL link {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        assert len(self.outputs) == 1
        self.outputs[0].path.parent.mkdir(parents=True, exist_ok=True)

        # Build -P flags for all libraries
        p_flags = []
        for res in self.inputs:
            if res.metadata.get("file_type") == "ghdl-cf":
                p_flags.append(f"-P{res.path.parent.resolve()}")
        self.root_workdir.mkdir(parents=True, exist_ok=True)

        # Build linker flags for VHPIDIRECT libraries
        vhpidirect_ldflags = []
        for lib_res in self.inputs_of_type("ghdl-vhpidirect-lib"):
            # Link the library directly (absolute path)
            lib_path = lib_res.path.resolve()
            vhpidirect_ldflags.append(f"-Wl,{lib_path}")

        process = GhdlInvocation(argv = [
            self.ghdl_executable, "-c", "-O2",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + vhpidirect_ldflags + [
            f"--work={self.root_library}",
            "-o", str(self.outputs[0].path),
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
        inputs: list = None,
        outputs: list = None,
    ):
        super().__init__(
            context=context,
            name=f"ghdl_make_{topcell}",
            inputs=inputs or [],
            outputs=outputs or [],
            description=f"GHDL make {topcell}"
        )
        self.topcell = topcell
        self.ghdl_executable = ghdl_executable
        self.root_workdir = root_workdir
        self.vhdl_std = vhdl_std
        self.root_library = root_library

    async def work(self) -> None:
        """Execute GHDL compile and link"""
        assert len(self.outputs) == 1
        self.outputs[0].path.parent.mkdir(parents=True, exist_ok=True)

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
        ], cwd = self.outputs[0].path.parent)

        async for msg in process:
            await self.add_message_obj(msg)

        if process.returncode != 0:
            raise RuntimeError(f"ghdl -e failed: {process.returncode}")

        # Build load flags for VHPIDIRECT libraries
        load_flags = []
        for lib_res in self.inputs_of_type("ghdl-vhpidirect-lib"):
            load_flags.append(f"--load={lib_src.path.resolve()}")

        # Create run script
        run_cmd = [
            self.ghdl_executable, "-r",
            f"--workdir={self.root_workdir.resolve()}",
            f"--std={self.vhdl_std}",
        ] + p_flags + [
            f"--work={self.root_library}",
            self.topcell,
        ] + load_flags + [
            '"$@"'
        ]

        script_content = f"""#!/bin/sh

exec {' '.join(run_cmd)}
"""
        self.outputs[0].path.write_text(script_content)
        self.outputs[0].path.chmod(0o755)
