"""Project-level Vivado TCL helpers

Vivado flows all start by setting up a project — in memory or on disk —
and driving it through the same handful of properties. The commands that
do not depend on the flow live here.
"""

from __future__ import annotations
import shutil
import zipfile
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

    # Whether sources_add() chains each HDL file after the previous one
    sources_reorder = False

    def ip_repo_paths_collect(self, output_dir: Path) -> list[str]:
        """Materialize the IP repositories of the inputs, list their paths

        Packaged IP arrives as a zip and bus definitions as loose XML
        files or as a zip; both have to sit in a directory before Vivado
        can be pointed at them.
        """
        ip_repo_paths = []

        for resource in self.inputs_of_type("vivado-ip-zip"):
            ip_unzip_dir = output_dir / "ip_repo" / resource.path.stem
            ip_unzip_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(resource.path) as zf:
                zf.extractall(ip_unzip_dir)
            ip_repo_paths.append(str(ip_unzip_dir))
            self.info(f"Extracted IP: {resource.path.name} -> {ip_unzip_dir}")

        for resource in self.inputs_of_type("vivado-ip-repository"):
            ip_repo_paths.append(str(resource.path))

        bus_defs = self.inputs_of_type("vivado-bus-definition")
        bus_zips = self.inputs_of_type("vivado-bus-zip")
        if bus_defs or bus_zips:
            bus_repo_dir = output_dir / "bus_repo"
            self.bus_repo_fill(bus_repo_dir, bus_defs)
            for resource in bus_zips:
                with zipfile.ZipFile(resource.path) as zf:
                    zf.extractall(bus_repo_dir)
            ip_repo_paths.append(str(bus_repo_dir))

        if ip_repo_paths:
            self.info(f"Adding repo paths {ip_repo_paths}")
        else:
            self.info("No repo paths to add")

        return ip_repo_paths

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

    async def project_configure(self) -> None:
        """Set the project properties every Vivado flow relies on

        Draft mode keeps the source scanner from elaborating the design
        on every source it is handed; sources come in compilation order
        and the flow declares the top cell itself.
        """
        await self.source_mgmt_display_only()
        await self.command_run(tcl.Command([
            "set_param", "project.hsv.draftModeDefault", "only",
        ]))
        await self.filesets_capture()

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

    async def sources_add(self, resources, progress_start: float,
                          progress_span: float) -> None:
        """Declare source files to the project, in the order given

        Resources of a type this method does not know about are left
        alone; the caller deals with them.
        """
        resources = list(resources)
        total = len(resources)
        chained = False

        for i, resource in enumerate(resources):
            if total:
                await self.update_progress(
                    progress_start + progress_span * i / total, "Sources")

            library = resource.library or "work"
            file_type = resource.file_type

            if file_type in ("vhdl", "verilog"):
                vivado_type = ("Verilog" if file_type == "verilog"
                               else self.vhdl_file_type(resource))
                self.debug(f"Adding {vivado_type}: {resource.path} "
                           f"(lib={library})")
                await self.file_add("$source_fileset_obj", resource.path)
                await self.file_properties_set(
                    f"file_type {{{vivado_type}}} library {{{library}}}")
                if self.sources_reorder:
                    await self.source_chain(chained)
                    chained = True

            elif file_type == "xilinx-xci":
                self.debug(f"Adding XCI: {resource.path} (lib={library})")
                await self.command_run(tcl.Command([
                    "set", "f",
                    tcl.Expansion(["read_ip", tcl.String(str(resource.path))]),
                ]))
                await self.file_properties_set(
                    f"library {{{library}}} used_in {{synthesis implementation}}")

            elif file_type == "xilinx-xdc":
                self.debug(f"Adding XDC: {resource.path}")
                await self.file_add("$constraints_fileset_obj", resource.path)
                await self.file_properties_set(
                    "file_type {XDC} used_in {synthesis implementation}")

            elif file_type == "xilinx-constraints-tcl":
                self.debug(f"Adding constraints TCL: {resource.path}")
                await self.file_add("$constraints_fileset_obj", resource.path)
                await self.file_properties_set(
                    "file_type {TCL} used_in {synthesis implementation}")

    async def source_chain(self, after_previous: bool) -> None:
        """Pin the file object in `$f` after the previously chained one

        Vivado orders the fileset on its own otherwise, and a project
        whose sources are compiled in the order GBS hands them over does
        not need that.
        """
        if after_previous:
            await self.command_run(tcl.Command([
                "reorder_files", "-after",
                tcl.Expansion(["get_property", "name",
                               tcl.BareWord("$last_source")]),
                tcl.Expansion(["get_property", "name", tcl.BareWord("$f")]),
            ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("last_source"), tcl.BareWord("$f"),
        ]))

    async def file_add(self, fileset: str, path: Path) -> None:
        """Add one file to a fileset, leaving the file object in `$f`"""
        await self.command_run(tcl.Command([
            "set", "f",
            tcl.Expansion([
                "add_files", "-norecurse", "-fileset",
                tcl.BareWord(fileset),
                tcl.Expansion(["file", "normalize", tcl.String(str(path))]),
            ]),
        ]))

    async def file_properties_set(self, properties: str) -> None:
        """Apply a property dictionary to the file object in `$f`"""
        await self.command_run(tcl.Command([
            "set_property", "-dict", tcl.String(properties),
            tcl.BareWord("$f"),
        ]))

    @staticmethod
    def vhdl_file_type(resource) -> str:
        """Vivado file type for a VHDL source, from its language version"""
        variant = resource.file_type_version or ''
        if variant == '2008':
            return "VHDL 2008"
        return "VHDL"
