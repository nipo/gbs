"""VHPIDIRECT C sources compiled to shared libraries for simulators

VHPIDIRECT C is plain C called directly by the elaborated design. It
needs no simulator symbols, so it is built by the C compiler alone into
a shared library the simulator loads at elaboration or run time.

It is not a VPI plugin: building it through ``ghdl --vpi-compile`` /
``--vpi-link`` pulls in ``-lghdlvpi``, which is unnecessary and absent
from some GHDL distributions (e.g. oss-cad-suite).
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ..build.task import Task, Resource, ResourceTypology
from ..build.subprocess import MessageSubprocess
from ..ui.messages import MessageSeverity, ToolMessage

if TYPE_CHECKING:
    from ..base import BaseDispatcher


class CCompilerInvocation(MessageSubprocess):
    """gcc/clang invocation, diagnostics as ``FILE:LINE:COL: level: message``"""

    diagnostic_line = re.compile(
        r'^(?P<file>[^:]+):(?P<line>[0-9]+):(?P<column>[0-9]+): '
        r'(?P<level>fatal error|error|warning|note): (?P<message>.*)$')

    level_map = {
        "fatal error": MessageSeverity.ERROR,
        "error": MessageSeverity.ERROR,
        "warning": MessageSeverity.WARNING,
        "note": MessageSeverity.INFO,
    }

    async def stderr_transform(self, lines):
        async for line in lines:
            m = self.diagnostic_line.match(line)
            if not m:
                yield ToolMessage(severity=MessageSeverity.INFO, message=line)
                continue
            yield ToolMessage(
                severity=self.level_map[m.group("level")],
                origin="cc",
                message=m.group("message"),
                file_path=m.group("file"),
                line=int(m.group("line")),
                column=int(m.group("column")))


class VHPIDirectCompile(Task):
    """Compile one VHPIDIRECT C source into a shared library"""

    SOURCE_TYPE = "vhpidirect-c"
    LIBRARY_TYPE = "vhpidirect-lib"
    INCLUDE_DIR_TYPE = "c-include-dir"

    def __init__(
        self,
        dispatcher: "BaseDispatcher",
        compiler: str,
        inputs: list,
        outputs: list,
    ):
        stem = inputs[0].path.stem
        super().__init__(
            dispatcher=dispatcher,
            name=f"vhpidirect_compile_{stem}",
            inputs=inputs,
            outputs=outputs,
            description=f"Compile VHPIDIRECT {stem}"
        )
        self.compiler = compiler

    async def work(self) -> None:
        c, = self.inputs_of_type(self.SOURCE_TYPE)
        so, = self.outputs_of_type(self.LIBRARY_TYPE)

        so.path.parent.mkdir(parents=True, exist_ok=True)
        obj_path = so.path.with_suffix('.o')
        env = self.dispatcher.tool_env or None

        include_flags = [
            f"-I{d.path.resolve()}" for d in self.inputs_of_type(self.INCLUDE_DIR_TYPE)
        ]

        compile_process = CCompilerInvocation(env=env, argv=[
            self.compiler, "-c", "-fPIC",
        ] + include_flags + [
            str(c.path.resolve()),
            "-o", str(obj_path)
        ])

        async for msg in compile_process:
            await self.add_message_obj(msg)

        if compile_process.returncode != 0:
            raise compile_process.failure(
                tool=self.compiler,
                message=f"C compilation failed for {c.path.name}",
            )

        link_process = CCompilerInvocation(env=env, argv=[
            self.compiler, "-shared",
            str(obj_path),
            "-o", str(so.path)
        ])

        async for msg in link_process:
            await self.add_message_obj(msg)

        if link_process.returncode != 0:
            raise link_process.failure(
                tool=self.compiler,
                message=f"Linking failed for {c.path.name}",
            )


class VHPIDirectSources:
    """Declares the compilation of pending VHPIDIRECT C sources of a dispatcher"""

    COMPILER = "gcc"

    @classmethod
    def compile_pending(cls, dispatcher: "BaseDispatcher") -> list[Resource]:
        """Create a compile task for each pending VHPIDIRECT C source

        The C sources are consumed from the pending queue; the shared
        libraries are left pending for the elaboration and run tasks.
        """
        context = dispatcher.context
        libraries = []

        for resource in list(context.filter_pending(file_type=[VHPIDirectCompile.SOURCE_TYPE])):
            lib_resource = context.get_resource(
                context.output_path / "vhpidirect" / f"{resource.path.stem}.so",
                file_type=VHPIDirectCompile.LIBRARY_TYPE,
                library=resource.library,
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=dispatcher.name,
            )

            # Include directories the source's repository attached to it,
            # e.g. support headers a backend ships separately from the
            # simulator.
            include_dirs = [
                context.get_resource(
                    Path(d),
                    file_type=VHPIDirectCompile.INCLUDE_DIR_TYPE,
                    typology=ResourceTypology.SOURCE,
                    directory=True,
                )
                for d in resource.metadata.get("include_dirs", [])
            ]

            compile_task = VHPIDirectCompile(
                dispatcher,
                compiler=cls.COMPILER,
                inputs=[resource] + include_dirs,
                outputs=[lib_resource],
            )

            for dep in context.remove_pending(resource.path):
                compile_task.dependency_add(dep)

            libraries.append(lib_resource)

        if libraries:
            dispatcher.info(f"Compiling {len(libraries)} VHPIDIRECT libraries")

        return libraries
