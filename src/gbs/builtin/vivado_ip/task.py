"""Vivado IP Packaging Task

Creates an IP-XACT package from HDL sources using Vivado's
ipx::package_project flow.
"""

from __future__ import annotations
import shutil
import zipfile
from pathlib import Path
from typing import Any
from collections import defaultdict

from ...build.task import Task, Resource
from ...build import tcl
from ..vivado.vivado_tcl import Session, VivadoCommand


class VivadoIpPackageTask(VivadoCommand):
    """Package HDL sources into a Vivado IP-XACT package.

    Flow:
    1. Create Vivado project with HDL sources
    2. Package project with ipx::package_project
    3. Re-open IP for editing, set metadata
    4. Source customization TCL scripts
    5. Generate XGUI, update checksums, save
    6. Zip the IP directory (if vivado-ip-zip requested)
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        part: str,
        ip_config: dict[str, Any],
        inputs: list,
        outputs: list,
    ):
        super().__init__(dispatcher,
            name="vivado_ip_package",
            session=session,
            inputs=inputs,
            outputs=outputs,
            description="Vivado IP Packaging",
        )
        self.part = part
        self.ip_config = ip_config

    async def work(self) -> None:
        topcell = self.dispatcher.context.get_topcell()
        output_dir = self.dispatcher.context.output_path.resolve()
        ip_dir = output_dir / "ip"
        proj_dir = output_dir / "proj"

        ip_dir.mkdir(parents=True, exist_ok=True)
        proj_dir.mkdir(parents=True, exist_ok=True)

        # IP metadata from backend config
        vendor = self.ip_config.get("vendor", "user")
        library = self.ip_config.get("library", "ip")
        ip_name = self.ip_config.get("name", topcell)
        version = self.ip_config.get("version", "1.0")
        taxonomy = self.ip_config.get("taxonomy", "/UserIP")
        display_name = self.ip_config.get("display_name", ip_name)
        description = self.ip_config.get("description", "")
        vendor_display_name = self.ip_config.get("vendor_display_name", vendor)
        company_url = self.ip_config.get("company_url", "")
        revision = self.ip_config.get("revision", "1")
        supported_families = self.ip_config.get("supported_families", [])

        # Step 1: Create project
        self.info("Creating Vivado project for IP packaging")
        await self.command_run(tcl.Command([
            "create_project", "-force", ip_name, ".",
            "-part", self.part,
        ]))

        await self.command_run(tcl.Command([
            "set_property", "-name", "target_language",
            "-value", "VHDL", "-objects",
            tcl.Expansion(["current_project"]),
        ]))

        # Step 2: Copy bus definitions to local repository
        bus_defs = self.inputs_of_type("vivado-bus-definition")
        if bus_defs:
            bus_repo_dir = output_dir / "bus_repo"
            bus_repo_dir.mkdir(parents=True, exist_ok=True)
            for bus_rsrc in bus_defs:
                shutil.copy2(bus_rsrc.path, bus_repo_dir / bus_rsrc.path.name)
            await self.command_run(tcl.Command([
                "set_property", "ip_repo_paths",
                tcl.String(str(bus_repo_dir)),
                tcl.Expansion(["current_project"]),
            ]))

        await self.command_run(tcl.Command(["update_ip_catalog", "-rebuild"]))

        await self.update_progress(0.1, "Adding sources")

        # Step 3: Add HDL sources
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("srcset_obj"),
            tcl.Expansion(["get_filesets", "sources_1"]),
        ]))
        await self.command_run(tcl.Command([
            "set", tcl.BareWord("cstrset_obj"),
            tcl.Expansion(["get_filesets", "constrs_1"]),
        ]))

        hdl_inputs = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type in ("vhdl", "verilog")]
        xdc_inputs = self.inputs_of_type("xilinx-xdc")

        total = len(hdl_inputs) + len(xdc_inputs)
        for i, resource in enumerate(hdl_inputs):
            file_type = resource.file_type
            lib_name = resource.library or "work"
            file_path = str(resource.path)

            await self.command_run(tcl.Command([
                "set", tcl.BareWord("fname"),
                tcl.Expansion(["file", "normalize", tcl.String(file_path)]),
            ]))
            await self.command_run(tcl.Command([
                "add_files", "-norecurse", "-fileset", tcl.BareWord("$srcset_obj"),
                tcl.Expansion(["list", tcl.BareWord("$fname")]),
            ]))
            await self.command_run(tcl.Command([
                "set", tcl.BareWord("fobj"),
                tcl.Expansion(["get_files", "-of_object", tcl.BareWord("$srcset_obj"),
                              tcl.Expansion(["list", tcl.String(f"*{resource.path.name}")])]),
            ]))
            await self.command_run(tcl.Command([
                "set_property", "file_type", file_type, tcl.BareWord("$fobj"),
            ]))
            await self.command_run(tcl.Command([
                "set_property", "library", lib_name, tcl.BareWord("$fobj"),
            ]))

            if total > 0:
                await self.update_progress(0.1 + 0.2 * i / total)

        # Add constraint files
        for resource in xdc_inputs:
            file_path = str(resource.path)
            await self.command_run(tcl.Command([
                "set", tcl.BareWord("fname"),
                tcl.Expansion(["file", "normalize", tcl.String(file_path)]),
            ]))
            await self.command_run(tcl.Command([
                "add_files", "-norecurse", "-fileset", tcl.BareWord("$cstrset_obj"),
                tcl.Expansion(["list", tcl.BareWord("$fname")]),
            ]))

        # Set top
        await self.command_run(tcl.Command([
            "set_property", "-name", "top", "-value", topcell,
            "-objects", tcl.BareWord("$srcset_obj"),
        ]))

        await self.update_progress(0.3, "Packaging IP")

        # Step 4: Package project
        self.info("Running ipx::package_project")
        await self.command_run(tcl.Command([
            "ipx::package_project", "-force",
            "-root_dir", str(ip_dir),
            "-vendor", vendor,
            "-library", library,
            "-taxonomy", taxonomy,
            "-import_files",
            "-set_current", "true",
        ]))

        # Unload and re-open for editing
        await self.command_run(tcl.Command([
            "ipx::unload_core", str(ip_dir / "component.xml"),
        ]))
        await self.command_run(tcl.Command([
            "ipx::edit_ip_in_project",
            "-upgrade", "true",
            "-name", "tmp_edit_project",
            "-directory", str(ip_dir),
            str(ip_dir / "component.xml"),
        ]))

        await self.update_progress(0.5, "Setting metadata")

        # Step 5: Set IP metadata
        self.info("Setting IP metadata")
        core = tcl.Expansion(["ipx::current_core"])

        await self.command_run(tcl.Command([
            "set_property", "display_name", tcl.String(display_name), core,
        ]))
        await self.command_run(tcl.Command([
            "set_property", "name", tcl.String(ip_name), core,
        ]))
        await self.command_run(tcl.Command([
            "set_property", "core_revision", tcl.String(revision), core,
        ]))
        if description:
            await self.command_run(tcl.Command([
                "set_property", "description", tcl.String(description), core,
            ]))
        if vendor_display_name:
            await self.command_run(tcl.Command([
                "set_property", "vendor_display_name",
                tcl.String(vendor_display_name), core,
            ]))
        if company_url:
            await self.command_run(tcl.Command([
                "set_property", "company_url", tcl.String(company_url), core,
            ]))

        # Set supported families (pairs of family-name and lifecycle)
        if supported_families:
            family_pairs = []
            for fam in supported_families:
                family_pairs.extend([fam, "Production"])
            await self.command_run(tcl.Command([
                "set_property", "supported_families",
                tcl.Expansion(["list"] + [tcl.String(f) for f in family_pairs]),
                core,
            ]))

        await self.update_progress(0.6, "Customization scripts")

        # Step 6: Source customization TCL scripts
        custom_scripts = self.inputs_of_type("vivado-ip-customization-tcl")
        for script_rsrc in custom_scripts:
            self.info(f"Sourcing customization script: {script_rsrc.path.name}")
            await self.command_run(tcl.Command([
                "source", str(script_rsrc.path),
            ]))

        await self.update_progress(0.7, "Finalizing")

        # Step 7: Create XGUI files, add implementation group, finalize
        self.info("Finalizing IP package")
        await self.command_run(tcl.Command([
            "ipx::create_xgui_files", core,
        ]))

        # Add implementation file group for constraint files
        await self.command_run(tcl.Command([
            "ipx::add_file_group", "-type", "implementation",
            "xilinx_implementation", core,
        ]))

        # Move constraint files to implementation group
        for resource in xdc_inputs:
            fname = f"src/{resource.path.name}"
            impl_group = tcl.Expansion([
                "ipx::get_file_groups", "xilinx_implementation",
                "-of_objects", core,
            ])
            await self.command_run(tcl.Command([
                "ipx::add_file", fname, impl_group,
            ]))

        await self.command_run(tcl.Command([
            "ipx::update_checksums", core,
        ]))
        await self.command_run(tcl.Command([
            "ipx::save_core", core,
        ]))
        await self.command_run(tcl.Command([
            "close_project", "-delete",
        ]))

        await self.update_progress(0.9, "Creating output")

        # Step 8: Create zip if requested
        for output in self.outputs:
            if output.file_type == "vivado-ip-zip":
                self.info(f"Creating IP zip: {output.path}")
                output.path.parent.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(output.path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for file in ip_dir.rglob('*'):
                        if file.is_file():
                            zf.write(file, file.relative_to(ip_dir))
            elif output.file_type == "vivado-ip-dir":
                # IP directory is already at ip_dir — if output path differs, copy
                if output.path.resolve() != ip_dir.resolve():
                    self.info(f"Copying IP directory to {output.path}")
                    output.path.parent.mkdir(parents=True, exist_ok=True)
                    if output.path.exists():
                        shutil.rmtree(output.path)
                    shutil.copytree(ip_dir, output.path)

        self.info("IP packaging complete")
