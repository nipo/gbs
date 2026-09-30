"""Vivado IP packaging and synthesis check dispatchers"""

from __future__ import annotations
from pathlib import Path
from typing import Any

from ...build.context import BuildContext
from ...build.task import ConfigurationError, Resource, ResourceTypology
from .task import VivadoIpCheckTask, VivadoIpPackageTask
from ..vivado.base import VivadoDispatcherBase


# Source types the IP packaging dispatcher consumes.
#
# vivado-ip-zip is deliberately absent: it is what this backend produces,
# and accepting it as an input would let the planner chain one packaging
# pass into the next. An already-packaged IP is handed over as a
# vivado-ip-repository instead.
PACKAGE_INPUT_TYPES = {
    "vhdl",
    "verilog",
    "vivado-ip-customization-tcl",
    "vivado-bd-tcl",
    "vivado-xgui-tcl",
    "xilinx-xdc",
}

# Repository types both the packaging and the check dispatchers read,
# attached without consuming them so that the other dispatcher still
# sees them.
REPOSITORY_INPUT_TYPES = {
    "vivado-bus-definition",
    "vivado-bus-zip",
    "vivado-ip-repository",
}

# Forms a packaged IP comes in
IP_TYPES = ("vivado-ip-zip", "vivado-ip-dir")


class VivadoIpDispatcher(VivadoDispatcherBase):
    """Vivado IP packaging dispatcher

    Workflow:
      First process() call:
        - Creates IP packaging task with output resources
        - Registers all output types (zip and/or dir), or an
          intermediate zip for the synthesis check when none was
          requested

      Subsequent process() calls:
        - Attaches any pending files of accepted types to the task
    """

    def __init__(
        self,
        context: BuildContext,
        vhdl_std: str = "2008",
        vivado_tool: str = "vivado",
        target: dict[str, str] | None = None,
        ip_config: dict[str, Any] | None = None,
    ):
        super().__init__(
            context,
            "vivado-ip",
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
        )
        self.ip_config = ip_config or {}
        self._package_task: VivadoIpPackageTask | None = None

    async def process(self) -> None:
        """Process input files"""
        if self._package_task is None:
            await self._create_package_task()

        self.inputs_attach(self._package_task, PACKAGE_INPUT_TYPES)
        self.inputs_attach(self._package_task, REPOSITORY_INPUT_TYPES,
                           consume=False)

    async def _create_package_task(self) -> None:
        """Create the IP packaging task with output resources"""
        session = self.session_get()
        part = self.target.get("part")

        if not part:
            raise RuntimeError("Vivado IP backend requires 'part' in target configuration")

        # A packaged IP given as a source is the synthesis check's
        # business; claiming it would overwrite it.
        outputs = self.context.filter_pending(
            file_type=list(IP_TYPES), typology=ResourceTypology.OUTPUT)
        for output in outputs:
            if output.file_type == "vivado-ip-dir":
                self.context.get_resource(output.path, directory=True)

        if not outputs:
            outputs = [self.context.get_resource(
                self.context.output_path / "ip.zip",
                file_type="vivado-ip-zip",
                typology=ResourceTypology.INTERMEDIATE,
                generated_by=self.name,
            )]

        self._package_task = VivadoIpPackageTask(
            dispatcher=self,
            session=session,
            part=part,
            ip_config=self.ip_config,
            inputs=[],
            outputs=outputs,
        )
        self.attach_definition_dependencies(self._package_task)

        self.info(f"Created Vivado IP packaging task for part {part}")


class VivadoIpCheckDispatcher(VivadoDispatcherBase):
    """Vivado IP synthesis check dispatcher

    Creates the check task once a report is requested and the IP under
    check is known. That IP is either a packaged IP given as a source,
    or the one the packaging dispatcher produces; the latter only
    becomes known once the packager has claimed its outputs.
    """

    def __init__(
        self,
        context: BuildContext,
        vhdl_std: str = "2008",
        vivado_tool: str = "vivado",
        target: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ):
        super().__init__(
            context,
            "vivado-ip-check",
            vhdl_std=vhdl_std,
            vivado_tool=vivado_tool,
            target=target,
        )
        self.params = params or {}
        self._check_task: VivadoIpCheckTask | None = None

    async def process(self) -> None:
        if self._check_task is None:
            self._create_check_task()

        if self._check_task is not None:
            self.inputs_attach(self._check_task, REPOSITORY_INPUT_TYPES,
                               consume=False)

    def ip_select(self) -> Resource | None:
        """Pick the packaged IP under check, None while it is not known

        Raises:
            ConfigurationError: More than one IP could be the one under
                check.
        """
        candidates = self.context.filter_pending(file_type=list(IP_TYPES))
        sources = [r for r in candidates
                   if r.typology == ResourceTypology.SOURCE]
        built = [r for r in candidates
                 if r.typology != ResourceTypology.SOURCE]
        produced = [r for r in built if r.depends_on]

        if sources and produced:
            raise ConfigurationError(
                "Vivado IP synthesis check: cannot tell the IP under check "
                "among the source IP(s) "
                + ", ".join(str(r.path) for r in sources)
                + " and the packaged one "
                + ", ".join(str(r.path) for r in produced)
                + "; give the IPs it depends on as vivado-ip-repository")

        if len(produced) != len(built):
            return None

        if sources:
            if len(sources) > 1:
                raise ConfigurationError(
                    "Vivado IP synthesis check needs a single IP, got "
                    + ", ".join(str(r.path) for r in sources)
                    + "; give the IPs it depends on as vivado-ip-repository")
            return sources[0]

        if not produced:
            return None

        # The packager writes the same IP to all its outputs
        zips = [r for r in produced if r.file_type == "vivado-ip-zip"]
        return (zips or produced)[0]

    def _create_check_task(self) -> None:
        part = self.target.get("part")
        if not part:
            raise RuntimeError("Vivado IP backend requires 'part' in target configuration")

        reports = [r for r in self.context.filter_pending(
                       file_type="vivado-ip-synthesis-report",
                       typology=ResourceTypology.OUTPUT)
                   if not r.depends_on]
        if not reports:
            return

        ip = self.ip_select()
        if ip is None:
            return

        self._check_task = VivadoIpCheckTask(
            dispatcher=self,
            session=self.session_get(),
            part=part,
            ip=ip,
            params=self.params,
            outputs=reports,
        )
        self.attach_definition_dependencies(self._check_task)

        self.info(f"Created Vivado IP synthesis check task of {ip.path} "
                  f"for part {part}")
