"""Vivado bus definition tasks."""

from __future__ import annotations

import shutil
import zipfile

from ...build.task import BuildError, Task
from . import generator


class BusDefTranspile(Task):
    """Generate the XML pair for one YAML bus description."""

    def __init__(self, dispatcher: "Dispatcher", source, outputs: list):
        super().__init__(
            dispatcher=dispatcher,
            name=f"vivado_bus_transpile_{source.path.stem}",
            inputs=[],
            outputs=outputs,
            description=f"Generate bus definition {source.path.stem}",
        )
        self.add_input(source)
        self.source = source

    async def work(self) -> None:
        try:
            bus = generator.Bus.load(self.source.path)
        except generator.BusDefError as exc:
            raise BuildError(f"{self.source.path}: {exc}") from exc

        # Output resource names were derived from the file stem before
        # the YAML could be read; a mismatching bus name would generate
        # files nothing waits for.
        if bus.name != self.source.path.stem:
            raise BuildError(
                f"{self.source.path}: bus name {bus.name!r} must match the "
                f"YAML file name"
            )

        files = bus.outputs()
        for output in self.outputs:
            output.path.parent.mkdir(parents=True, exist_ok=True)
            output.path.write_text(files[output.path.name])

        self.info(f"Generated bus definition {bus.name}")


class BusDefPackage(Task):
    """Bundle bus definition files into a repository zip or directory."""

    def __init__(self, dispatcher: "Dispatcher", outputs: list):
        super().__init__(
            dispatcher=dispatcher,
            name="vivado_bus_package",
            inputs=[],
            outputs=outputs,
            description="Package bus definitions",
        )

    async def work(self) -> None:
        bus_defs = self.inputs_of_type("vivado-bus-definition")
        if not bus_defs:
            raise BuildError("no bus definition files to package")

        for output in self.outputs:
            output.path.parent.mkdir(parents=True, exist_ok=True)
            if output.file_type == "vivado-bus-zip":
                self.info(f"Creating bus definition zip: {output.path}")
                with zipfile.ZipFile(output.path, "w",
                                     zipfile.ZIP_DEFLATED) as zf:
                    for resource in bus_defs:
                        zf.write(resource.path, resource.path.name)
            elif output.file_type == "vivado-bus-dir":
                self.info(f"Creating bus definition directory: {output.path}")
                if output.path.exists():
                    shutil.rmtree(output.path)
                output.path.mkdir(parents=True)
                for resource in bus_defs:
                    shutil.copy2(resource.path,
                                 output.path / resource.path.name)
