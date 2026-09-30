"""Output groups for the packaged IP synthesis check

The check is an ordinary build output, `vivado-ip-synthesis-report`,
so a project can ask for it. This module assembles the output groups
asking for it on behalf of a command line: a synthetic project around
a packaged IP given as a file, or extra report outputs on the groups
of an existing project that package an IP.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from ...project.model import OutputFile, OutputGroup, ProjectModel
from ...project.partition import (
    ConditionalGroup,
    FilterCondition,
    PartitionTemplate,
)
from ...project.project import Project
from ...repository.model import SourceFile
from .component import IpComponent
from .task import VivadoIpCheckTask

__all__ = ["IpCheck", "IpCheckError"]


class IpCheckError(Exception):
    """The check was asked for in a way that cannot be carried out"""
    pass


class IpCheck:
    """Assembles the output groups of a packaged IP synthesis check"""

    BACKEND = "gbs.builtin.vivado-ip"
    OUTPUT_GROUP_NAME = "ip-check"
    REPORT_TYPE = "vivado-ip-synthesis-report"
    IP_TYPES = ("vivado-ip-zip", "vivado-ip-dir")
    CONFIG_KEY = "synthesis_check_config"

    @staticmethod
    def default_report_path(group_name: str) -> Path:
        return Path("gbs-build") / group_name / "ip-utilization.rpt"

    @staticmethod
    def params_parse(specs: tuple[str, ...]) -> dict[str, str]:
        """Parse NAME=VALUE IP parameter assignments

        Values stay strings: Vivado converts CONFIG properties to the
        parameter type itself.

        Raises:
            IpCheckError: An assignment is malformed or would not make a
                valid IP property.
        """
        from ...build.task import BuildError

        params = {}
        for spec in specs:
            name, sep, value = spec.partition("=")
            if not sep:
                raise IpCheckError(
                    f"Invalid IP parameter '{spec}', expected NAME=VALUE")
            params[name] = value

        try:
            VivadoIpCheckTask.params_tcl(params)
        except BuildError as e:
            raise IpCheckError(str(e))

        return params

    @classmethod
    def zip_project(
        cls,
        ip: Path,
        part: str,
        params: dict[str, Any],
        repos: list[Path],
        bus_zips: list[Path],
        report: Optional[Path],
        gbs_config: Optional[Any],
    ) -> Project:
        """Project checking a packaged IP given as a zip or a directory

        The package is read here, so that a broken one fails before
        Vivado is started.

        Raises:
            BuildError: The package does not hold a readable
                component.xml.
        """
        ip = ip.resolve()
        if ip.is_dir():
            ip_type = "vivado-ip-dir"
            component = IpComponent.from_dir(ip)
        else:
            ip_type = "vivado-ip-zip"
            component = IpComponent.from_zip(ip)

        sources = [SourceFile(path=ip, file_type=ip_type)]
        sources += [SourceFile(path=r.resolve(), file_type="vivado-ip-repository")
                    for r in repos]
        sources += [SourceFile(path=z.resolve(), file_type="vivado-bus-zip")
                    for z in bus_zips]

        template = PartitionTemplate(
            name="ip_check",
            groups=[ConditionalGroup(
                name="root",
                conditions=[FilterCondition(
                    expression="default", sources=sources)],
            )],
        )

        group = OutputGroup(
            name=cls.OUTPUT_GROUP_NAME,
            topcell=component.name,
            target={"part": part},
            backend_config={cls.BACKEND: {cls.CONFIG_KEY: dict(params)}},
            outputs=[OutputFile(
                type=cls.REPORT_TYPE,
                path=report or cls.default_report_path(cls.OUTPUT_GROUP_NAME),
            )],
            require_backends=[cls.BACKEND],
        )

        model = ProjectModel(
            name=f"ip-check({component.vlnv})",
            root_partition_templates={template.name: template},
            output_groups=[group],
        )

        return Project(
            model=model,
            repositories=[],
            path=None,
            gbs_config=gbs_config,
        )

    @classmethod
    def project_extend(
        cls,
        project: Project,
        group_names: list[str],
        params: dict[str, Any],
        report: Optional[Path],
    ) -> list[tuple[str, Path]]:
        """Add a synthesis check report to output groups packaging an IP

        Args:
            project: Loaded project, modified in place
            group_names: Output groups to check; when empty, every group
                producing a packaged IP
            params: IP parameters, overriding the ones of the project
            report: Report path, only valid for a single group

        Returns:
            Name and report path of each extended group

        Raises:
            IpCheckError: A group is unknown or packages no IP, no group
                packages an IP, or one report path was given for several
                groups.
        """
        groups = project.model.output_groups

        def packages_ip(group: OutputGroup) -> bool:
            return any(o.type in cls.IP_TYPES for o in group.outputs)

        if group_names:
            by_name = {g.name: g for g in groups}
            unknown = [n for n in group_names if n not in by_name]
            if unknown:
                known = ", ".join(sorted(by_name)) or "(none)"
                raise IpCheckError(
                    f"Unknown output group(s): {', '.join(unknown)}. "
                    f"Known: {known}")
            selected = [by_name[n] for n in dict.fromkeys(group_names)]
            no_ip = [g.name for g in selected if not packages_ip(g)]
            if no_ip:
                raise IpCheckError(
                    f"Output group(s) {', '.join(no_ip)} produce no "
                    f"packaged IP ({' or '.join(cls.IP_TYPES)})")
        else:
            selected = [g for g in groups if packages_ip(g)]
            if not selected:
                raise IpCheckError(
                    f"No output group produces a packaged IP "
                    f"({' or '.join(cls.IP_TYPES)})")

        if report is not None and len(selected) > 1:
            raise IpCheckError(
                f"A report path can only be given for a single output "
                f"group, {len(selected)} are checked: "
                + ", ".join(g.name for g in selected))

        selected_names = {g.name for g in selected}
        extended = []
        for index, group in enumerate(groups):
            if group.name not in selected_names:
                continue

            path = report or cls.default_report_path(group.name)

            # The project's configuration dictionaries are shared with its
            # raw configuration; replace rather than update them.
            backend_config = dict(group.backend_config.get(cls.BACKEND, {}))
            backend_config[cls.CONFIG_KEY] = {
                **(backend_config.get(cls.CONFIG_KEY) or {}),
                **params,
            }
            groups[index] = replace(
                group,
                backend_config={**group.backend_config,
                                cls.BACKEND: backend_config},
                outputs=group.outputs + [OutputFile(type=cls.REPORT_TYPE,
                                                    path=path)],
            )
            extended.append((group.name, path))

        return extended
