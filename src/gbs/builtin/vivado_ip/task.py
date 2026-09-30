"""Vivado IP packaging and synthesis check tasks

Creates an IP-XACT package from HDL sources using Vivado's
ipx::package_project flow, and checks that a packaged IP synthesizes
out of context.
"""

from __future__ import annotations
import re
import shutil
import zipfile
from pathlib import Path
from typing import Any
from collections import defaultdict

from ...build.task import BuildError, Task, Resource
from ...build import tcl
from .component import IpComponent
from ..vivado.project import ProjectCommand
from ..vivado.vivado_tcl import Session


class VivadoIpPackageTask(ProjectCommand):
    """Package HDL sources into a Vivado IP-XACT package.

    Flow:
    1. Create Vivado project with HDL sources
    2. Package project with ipx::package_project
    3. Re-open IP for editing, set metadata
    4. Source customization TCL scripts
    5. Generate XGUI, update checksums, save
    6. Zip the IP directory (if vivado-ip-zip requested)
    """

    # ipx::package_project keeps the fileset order, which is the
    # compilation order the sources have to be packaged in
    sources_reorder = True

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

    async def _get_vivado_version(self) -> tuple[int, int]:
        """Query Vivado version via 'version -short'.

        Returns (year, release) tuple, e.g. (2022, 2).
        """
        lines = []
        cmd = tcl.Command(["version", tcl.BareWord("-short")])
        async for msg in self.session.interact(cmd):
            if hasattr(msg, 'message'):
                lines.append(msg.message)

        for line in lines:
            line = line.strip()
            if '.' in line and line[0].isdigit():
                parts = line.split('.')
                try:
                    return (int(parts[0]), int(parts[1]))
                except (ValueError, IndexError):
                    pass

        self.warning("Could not determine Vivado version, assuming latest")
        return (9999, 0)

    async def work(self) -> None:
        topcell = self.dispatcher.context.get_topcell()
        top_lib = self.dispatcher.context.get_topcell_library() or "work"
        output_dir = self.dispatcher.context.output_path.resolve()
        ip_dir = output_dir / "ip"
        proj_dir = output_dir / "proj"

        ip_dir.mkdir(parents=True, exist_ok=True)
        proj_dir.mkdir(parents=True, exist_ok=True)

        # IP metadata from backend config
        vendor = self.ip_config.get("vendor")
        library = self.ip_config.get("library")
        ip_name = self.ip_config.get("name")
        version = self.ip_config.get("version", "1.0")
        taxonomy = self.ip_config.get("taxonomy")
        display_name = self.ip_config.get("display_name")
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

        await self.project_configure()

        # Step 2: Copy bus definitions to local repository
        await self.ip_repos_setup(self.ip_repo_paths_collect(output_dir))

        await self.update_progress(0.1, "Adding sources")

        hdl_inputs = [r for r in self.inputs
                      if isinstance(r, Resource) and r.file_type in ("vhdl", "verilog")]
        xdc_inputs = self.inputs_of_type("xilinx-xdc")

        await self.sources_add(hdl_inputs + xdc_inputs, 0.1, 0.2)

        await self.top_set(topcell, top_lib)

        await self.update_progress(0.3, "Packaging IP")

        # Step 4: Package project
        vivado_version = await self._get_vivado_version()
        self.info(f"Vivado version: {vivado_version[0]}.{vivado_version[1]}")

        package_cmd = [
            "ipx::package_project", "-force",
            "-root_dir", str(ip_dir),
            "-vendor", vendor,
            "-library", library,
        ]
        if vivado_version >= (2022, 1):
            package_cmd.extend(["-name", ip_name])
        package_cmd.extend([
            "-taxonomy", taxonomy,
            "-import_files",
            "-set_current", "true",
        ])

        self.info("Running ipx::package_project")
        await self.command_run(tcl.Command(package_cmd))
        self.error_check("package the project")

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
            "set_property", "core_revision", tcl.String(str(revision)), core,
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

        # Add bd files
        #
        # ipx::add_file records the path it is handed, so the script has to sit
        # under the IP root and be named relative to it, otherwise the reference
        # points outside the IP and does not survive export.
        bd_tcl_inputs = self.inputs_of_type("vivado-bd-tcl")
        if bd_tcl_inputs:
            bd_dir = ip_dir / "bd"
            bd_dir.mkdir(parents=True, exist_ok=True)

            staged = {}
            for resource in bd_tcl_inputs:
                name = resource.path.name
                if name in staged:
                    raise RuntimeError(
                        f"Conflicting vivado-bd-tcl inputs named {name}: "
                        f"{staged[name]} and {resource.path}"
                    )
                staged[name] = resource.path
                self.info(f"Staging BD script: {name}")
                shutil.copy2(resource.path, bd_dir / name)

            await self.command_run(tcl.Command([
                "set", tcl.BareWord("bd_group"),
                tcl.Expansion(["ipx::add_file_group", "xilinx_blockdiagram", core]),
            ]))
            for name in staged:
                await self.command_run(tcl.Command([
                    "set", tcl.BareWord("fobj"),
                    tcl.Expansion([
                        "ipx::add_file", f"bd/{name}", tcl.BareWord("$bd_group"),
                    ]),
                ]))
                await self.command_run(tcl.Command([
                    "set_property", tcl.BareWord("type"), tcl.BareWord("tclSource"), tcl.BareWord("$fobj"),
                ]))

        # Add xgui file by overwriting in-IP generated xgui
        xgui_tcl_inputs = self.inputs_of_type("vivado-xgui-tcl")
        if xgui_tcl_inputs:
            fset = tcl.Expansion(["ipx::get_file_groups", "xilinx_xpgui", tcl.BareWord("-of_objects"), tcl.Expansion(["ipx::current_core"])])
            existing_fname = tcl.Expansion(["get_property", "NAME", tcl.Expansion(["ipx::get_files", "*.tcl", "-of_objects", fset])])
            existing_abs_fname = tcl.Expansion(["file", "join", str(ip_dir), existing_fname])
            resource, = xgui_tcl_inputs

            await self.command_run(tcl.Command([
                "file", "copy", "-force", str(resource.path.resolve()), existing_abs_fname,
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
        self.error_check("save the packaged core")

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


class VivadoIpCheckTask(ProjectCommand):
    """Check that a packaged IP synthesizes out of context

    The IP is instantiated from the catalog by VLNV in an in-memory
    project, its targets are generated and synth_ip synthesizes it, as
    a design using the IP would. The utilization of the resulting
    checkpoint is the report.

    Everything is extracted and generated under a directory that is
    wiped first: files left from a previous run could stand in for
    ones the package fails to provide.
    """

    PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

    def __init__(
        self,
        dispatcher: "Dispatcher",
        session: Session,
        part: str,
        ip: Resource,
        params: dict[str, Any],
        outputs: list,
    ):
        super().__init__(dispatcher,
            name="vivado_ip_check",
            session=session,
            inputs=[ip],
            outputs=outputs,
            description="Vivado IP synthesis check",
        )
        self.part = part
        self.ip = ip
        self.params = params

    @property
    def work_dir(self) -> Path:
        return self.dispatcher.context.output_path.resolve() / "ip-check"

    async def work(self) -> None:
        work_dir = self.work_dir
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True)

        config = self.params_tcl(self.params)
        component = IpComponent.load(self.ip)
        instance = f"{component.name}_0"
        ip_dir = work_dir / "ip"
        ip_dir.mkdir()
        ip_obj = tcl.Expansion(["get_ips", instance])

        await self.update_progress(0.01, "Init")
        self.info(f"Creating in-memory project for part {self.part}")
        await self.command_run(tcl.Command([
            "create_project", "-in_memory", "-part", self.part,
        ]))
        await self.command_run(tcl.Command([
            "set_property", "target_language", "VHDL",
            tcl.Expansion(["current_project"]),
        ]))
        await self.project_configure()
        self.error_check("create the project")

        await self.ip_repos_setup(self.ip_repo_paths_collect(work_dir))
        self.error_check("set up the IP repositories")

        await self.update_progress(0.1, "Instantiate")
        self.info(f"Instantiating {component.vlnv} as {instance}")
        await self.command_run(tcl.Command([
            "create_ip", "-vlnv", component.vlnv,
            "-module_name", instance,
            "-dir", tcl.String(str(ip_dir)),
        ]))
        self.error_check(f"instantiate IP {component.vlnv}")

        if self.params:
            await self.command_run(tcl.Command([
                "set_property", "-dict", config, ip_obj,
            ]))
            self.error_check("set the IP parameters")

        await self.update_progress(0.2, "Generate")
        await self.command_run(tcl.Command([
            "generate_target", "all", ip_obj,
        ]))
        self.error_check("generate the IP targets")

        await self.update_progress(0.3, "Synth")
        self.info("Running out-of-context synthesis")
        await self.command_run(tcl.Command(["synth_ip", ip_obj]))
        self.error_check("synthesize the IP")

        checkpoint = ip_dir / instance / f"{instance}.dcp"
        if not checkpoint.exists():
            raise BuildError(
                f"Vivado synthesized IP {component.vlnv} without writing "
                f"its checkpoint {checkpoint}")

        await self.update_progress(0.9, "Reports")
        await self.command_run(tcl.Command([
            "open_checkpoint", tcl.String(str(checkpoint)),
        ]))
        self.error_check("open the IP checkpoint")

        # A module the package does not provide the sources of is left
        # as a black box, which out-of-context synthesis accepts with a
        # mere warning.
        await self.command_run(tcl.Command([
            "foreach", tcl.BareWord("cell"),
            tcl.Expansion(["get_cells", "-quiet", "-hierarchical",
                           "-filter", "IS_BLACKBOX"]),
            tcl.String(
                'catch {send_msg_id {GBS 1-1} ERROR "Black box cell $cell '
                'of unresolved module [get_property REF_NAME $cell]"}'),
        ]))
        self.error_check("resolve every module of the IP")

        for rsrc in self.outputs_of_type("vivado-ip-synthesis-report"):
            rsrc.path.parent.mkdir(parents=True, exist_ok=True)
            await self.command_run(tcl.Command([
                "report_utilization", "-file", tcl.String(str(rsrc.path)),
            ]))
        await self.command_run(tcl.Command(["close_design"]))
        self.error_check("write the reports")

        self.info("IP synthesis check complete")

    @classmethod
    def params_tcl(cls, params: dict[str, Any]) -> tcl.Expansion:
        """TCL list of IP CONFIG properties for set_property -dict

        Values are brace-quoted, so a value with a brace or a backslash
        could not be passed through verbatim and is rejected.
        """
        argv = ["list"]
        for name, value in params.items():
            if not isinstance(name, str) or not cls.PARAM_NAME.fullmatch(name):
                raise BuildError(f"Invalid IP parameter name {name!r}")

            if isinstance(value, bool):
                text = "true" if value else "false"
            elif isinstance(value, (int, float, str)):
                text = str(value)
            else:
                raise BuildError(
                    f"IP parameter {name}: unsupported value {value!r} "
                    f"of type {type(value).__name__}")

            if any(c in text for c in "{}\\"):
                raise BuildError(
                    f"IP parameter {name}: value {text!r} contains a brace "
                    f"or a backslash")

            argv += [tcl.BareWord(f"CONFIG.{name}"), tcl.String(text)]

        return tcl.Expansion(argv)
