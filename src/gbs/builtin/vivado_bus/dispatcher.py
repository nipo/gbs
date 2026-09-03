"""Vivado bus definition dispatchers.

The transpile dispatcher turns each pending ``vivado-bus-yaml`` source
into a pair of ``vivado-bus-definition`` resources. Output names derive
from the YAML file stem, so the file system is not touched at process()
time; the task enforces that the stem matches the bus name declared
inside the file.

The package dispatcher mirrors the Vivado IP one: it grabs the
requested ``vivado-bus-zip``/``vivado-bus-dir`` outputs on the first
iteration and keeps attaching pending bus definition files on
subsequent ones.
"""

from __future__ import annotations

from pathlib import Path

from ...base import BaseDispatcher
from ...build.context import BuildContext
from ...build.task import ResourceTypology
from . import task


class VivadoBusTranspileDispatcher(BaseDispatcher):
    """Emit bus definition XML pairs from YAML descriptions."""

    def __init__(self, context: BuildContext):
        super().__init__(context, "vivado-bus-transpile",
                         tool_name="vivado-bus-transpile")
        self._tasks: dict[Path, task.BusDefTranspile] = {}

    async def process(self) -> None:
        for source in list(self.context.filter_pending(
                file_type=["vivado-bus-yaml"])):
            if source.path in self._tasks:
                continue

            bus_name = source.path.stem
            out_dir = self.context.output_path / "bus_definitions"
            outputs = []
            for file_name in (f"{bus_name}.xml", f"{bus_name}_rtl.xml"):
                resource = self.context.get_resource(
                    out_dir / file_name,
                    file_type="vivado-bus-definition",
                    typology=ResourceTypology.INTERMEDIATE,
                    generated_by=self.name,
                )
                self.context.add_pending(resource)
                outputs.append(resource)

            transpile = task.BusDefTranspile(
                dispatcher=self,
                source=source,
                outputs=outputs,
            )
            self.attach_definition_dependencies(transpile)
            self._tasks[source.path] = transpile


class VivadoBusPackageDispatcher(BaseDispatcher):
    """Bundle bus definition files into a repository zip or directory."""

    def __init__(self, context: BuildContext):
        super().__init__(context, "vivado-bus-package",
                         tool_name="vivado-bus-package")
        self._package_task: task.BusDefPackage | None = None

    async def process(self) -> None:
        if self._package_task is None:
            outputs = []
            for file_type in ("vivado-bus-zip", "vivado-bus-dir"):
                outputs.extend(self.context.filter_pending(
                    file_type=file_type))
            if not outputs:
                return

            self._package_task = task.BusDefPackage(
                dispatcher=self,
                outputs=outputs,
            )
            self.attach_definition_dependencies(self._package_task)

        existing_paths = {r.path for r in self._package_task.inputs}
        for source in list(self.context.filter_pending(
                file_type="vivado-bus-definition")):
            if source.path in existing_paths:
                continue
            self.debug(f"Attaching bus definition: {source.path}")
            self._package_task.add_input(source)
