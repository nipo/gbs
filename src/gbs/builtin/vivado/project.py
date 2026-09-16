"""Project-level Vivado TCL helpers

Vivado flows all start by setting up a project — in memory or on disk —
and driving it through the same handful of properties. The commands that
do not depend on the flow live here.
"""

from __future__ import annotations
import shutil
from pathlib import Path

from ...build import tcl
from .vivado_tcl import VivadoCommand

__all__ = ["ProjectCommand"]


class ProjectCommand(VivadoCommand):
    """Task driving a Vivado project through a TCL session

    The fileset objects captured by filesets_capture() are named
    `$source_fileset_obj` and `$constraints_fileset_obj` in the session,
    and the other helpers refer to them.
    """

    async def ip_repos_setup(self, ip_repo_paths: list[str]) -> None:
        """Append directories to the project IP repository path list"""
        if not ip_repo_paths:
            return

        await self.command_run(tcl.Command([
            "set_property", "ip_repo_paths",
            tcl.Expansion([
                "concat",
                tcl.Expansion(["get_property", "ip_repo_paths",
                               tcl.Expansion(["current_project"])]),
                tcl.Expansion(["list"] + [tcl.String(p) for p in ip_repo_paths]),
            ]),
            tcl.Expansion(["current_project"]),
        ]))
        await self.command_run(tcl.Command(["update_ip_catalog", "-rebuild"]))

    @staticmethod
    def bus_repo_fill(bus_repo_dir: Path, bus_defs: list) -> None:
        """Gather bus definition files in a directory usable as an IP repository"""
        bus_repo_dir.mkdir(parents=True, exist_ok=True)
        for resource in bus_defs:
            shutil.copy2(resource.path, bus_repo_dir / resource.path.name)

    async def filesets_capture(self) -> None:
        """Bind the source and constraint filesets to session variables"""
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("source_fileset_obj"),
            tcl.Expansion(["get_filesets", "sources_1"]),
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("constraints_fileset_obj"),
            tcl.Expansion(["get_filesets", "constrs_1"]),
        ]))

    async def source_mgmt_display_only(self) -> None:
        """Keep Vivado from reordering or re-deriving the source set"""
        await self.command_run(tcl.Command([
            "set_property", "source_mgmt_mode", "DisplayOnly",
            tcl.Expansion(["current_project"]),
        ]))

    async def top_set(self, topcell: str, top_lib: str) -> None:
        """Declare the top cell of the source fileset

        The library matters as soon as the topcell is not in work -- a
        wrapper generated into a library of its own, say -- and Vivado
        looks it up in the default library otherwise.
        """
        self.debug(f"Setting top: {topcell} (lib={top_lib})")
        await self.command_run(tcl.Command([
            "set_property", "top_lib", top_lib,
            tcl.BareWord("$source_fileset_obj"),
        ]))
        await self.command_run(tcl.Command([
            "set_property", "top", topcell,
            tcl.BareWord("$source_fileset_obj"),
        ]))

    @staticmethod
    def vhdl_file_type(resource) -> str:
        """Vivado file type for a VHDL source, from its language version"""
        variant = resource.file_type_version or ''
        if variant == '2008':
            return "VHDL 2008"
        return "VHDL"
