"""QuestaSim/ModelSim build tasks

Task implementations for QuestaSim/ModelSim GUI project generation.
Generates MPF project files and GUI launcher scripts.
"""

from __future__ import annotations
from pathlib import Path

from ...build.task import Task, BuildError


# Base MPF template with common QuestaSim settings
BASE_MPF_TEMPLATE = """[Version]
INIVersion = "QA Baseline: 2021.1 Beta - 4536908"

[Library]
others = modelsim.ini
work = work

[DefineOptionset]
UVMDEBUG = -uvmcontrol=all -msgmode both -displaymsgmode both -classdebug -onfinish stop
VOPTDEBUG = +acc -debugdb

[encryption]
Stats = cmd,msg

[vcom]
VHDL93 = 2002
Explicit = 1

[vlog]
LibrarySearchPath = mtiAvm mtiRnm mtiOvm mtiUvm mtiUPF infact

[sccom]

[vopt]

[vsim]
VoptFlow = 1
Resolution = ns
UserTimeUnit = default
RunLength = 100
IterationLimit = 10000000
BreakOnAssertion = 3
ShowFunctions = 1
DefaultRadix = hexadecimal
DefaultRadixFlags = showbase
TranscriptFile = transcript
PathSeparator = /
DatasetSeparator = :
UnbufferedOutput = 0
ConcurrentFileLimit = 40
WildcardFilter = Variable Constant Generic Parameter SpecParam Memory Assertion Cover Endpoint ScVariable CellInternal ImmediateAssert VHDLFile
WildcardSizeThreshold = 8192
WildcardSizeThresholdVerbose = 0
ScTimeUnit = ns
ScMainStackSize = 10 Mb
ScMainFinishOnQuit = 1
ScvPhaseRelationName = mti_phase
OnFinish = ask
DumpportsCollapse = 1
MvcHome = $QUESTA_MVC_HOME

[lmc]
libsm = $MODEL_TECH/libsm.sl
libhm = $MODEL_TECH/libhm.sl

[msg_system]
suppress = 8780

[utils]

[Project]
Project_Version = 6
Project_SortMethod = unused
Project_Sim_Count = 0
Project_Folder_Count = 0
Echo_Compile_Output = 0
Save_Compile_Report = 1
Project_Opt_Count = 0
ForceSoftPaths = 0
ProjectStatusDelay = 5000
VERILOG_DoubleClick = Edit
VERILOG_CustomDoubleClick =
SYSTEMVERILOG_DoubleClick = Edit
SYSTEMVERILOG_CustomDoubleClick =
VHDL_DoubleClick = Edit
VHDL_CustomDoubleClick =
PSL_DoubleClick = Edit
PSL_CustomDoubleClick =
TEXT_DoubleClick = Edit
TEXT_CustomDoubleClick =
SYSTEMC_DoubleClick = Edit
SYSTEMC_CustomDoubleClick =
TCL_DoubleClick = Edit
TCL_CustomDoubleClick =
MACRO_DoubleClick = Edit
MACRO_CustomDoubleClick =
VCD_DoubleClick = Edit
VCD_CustomDoubleClick =
SDF_DoubleClick = Edit
SDF_CustomDoubleClick =
XML_DoubleClick = Edit
XML_CustomDoubleClick =
LOGFILE_DoubleClick = Edit
LOGFILE_CustomDoubleClick =
UCDB_DoubleClick = Edit
UCDB_CustomDoubleClick =
TDB_DoubleClick = Edit
TDB_CustomDoubleClick =
UPF_DoubleClick = Edit
UPF_CustomDoubleClick =
PCF_DoubleClick = Edit
PCF_CustomDoubleClick =
PROJECT_DoubleClick = Edit
PROJECT_CustomDoubleClick =
VRM_DoubleClick = Edit
VRM_CustomDoubleClick =
DEBUGDATABASE_DoubleClick = Edit
DEBUGDATABASE_CustomDoubleClick =
DEBUGARCHIVE_DoubleClick = Edit
DEBUGARCHIVE_CustomDoubleClick =
Project_Major_Version = 2024
Project_Minor_Version = 1
"""


class GenerateQuestaProject(Task):
    """Generate QuestaSim MPF project file

    Creates a .mpf file that:
    1. Includes base QuestaSim configuration
    2. Sets project default library
    3. Adds all source files with metadata (library, VHDL version, etc.)
    """

    # VHDL version argument mapping
    VHDL_VERSION_MAP = {
        "1987": "87",
        "87": "87",
        "1993": "93",
        "93": "93",
        "2002": "2002",
        "02": "2002",
        "2008": "2008",
        "08": "2008",
        "2019": "2019",
        "19": "2019",
    }

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="questa_project",
            inputs=inputs,
            outputs=outputs,
            description="Generate QuestaSim project file"
        )

    def _get_vhdl_version_arg(self, variant: str | None) -> str:
        """Get VHDL version argument for project file

        Args:
            variant: VHDL variant from source metadata

        Returns:
            Version argument (e.g., "93", "2008")
        """
        if variant:
            return self.VHDL_VERSION_MAP.get(variant, self.dispatcher.vhdl_std)
        return self.VHDL_VERSION_MAP.get(self.dispatcher.vhdl_std, "93")

    def _generate_file_entry(self, index: int, resource, library: str) -> str:
        """Generate MPF file entry for a source file

        Args:
            index: File index (compile order)
            resource: Source file resource
            library: Target library name

        Returns:
            Two-line MPF entry for the file
        """
        file_path = resource.path.resolve()
        file_type = resource.file_type

        # Use placeholder timestamp (0 means "not compiled yet")
        timestamp = 0

        if file_type == 'vhdl':
            variant = resource.file_type_version
            vhdl_ver = self._get_vhdl_version_arg(variant)

            # Generate VHDL file entry
            entry = f"Project_File_{index} = {file_path}\n"
            entry += f"Project_File_P_{index} = vhdl_novitalcheck 0 file_type vhdl group_id 0 cover_nofec 0 vhdl_nodebug 0 vhdl_1164 1 vhdl_noload 0 vhdl_synth 0 vhdl_enable0In 0 folder {{Top Level}} last_compile {timestamp} vhdl_disableopt 0 vhdl_vital 0 cover_excludedefault 0 vhdl_warn1 1 vhdl_warn2 1 vhdl_explicit 1 vhdl_showsource 0 vhdl_warn3 1 cover_covercells 0 vhdl_0InOptions {{}} vhdl_warn4 1 voptflow 1 cover_optlevel 3 vhdl_options {{}} vhdl_warn5 1 toggle - ood 0 cover_noshort 0 compile_to {library} compile_order {index} cover_nosub 0 dont_compile 0 vhdl_use93 {vhdl_ver}"
        elif file_type == 'verilog':
            # Generate Verilog file entry
            entry = f"Project_File_{index} = {file_path}\n"
            entry += f"Project_File_P_{index} = file_type verilog group_id 0 cover_nofec 0 folder {{Top Level}} last_compile {timestamp} cover_excludedefault 0 cover_covercells 0 voptflow 1 cover_optlevel 3 toggle - ood 0 cover_noshort 0 compile_to {library} compile_order {index} cover_nosub 0 dont_compile 0"
        else:
            raise BuildError(f"Unsupported file type for QuestaSim: {file_type}")

        return entry

    async def work(self) -> None:
        """Generate the MPF project file"""
        top_lib = self.dispatcher.context.get_topcell_library() or "work"

        # Build MPF content
        lines = []

        # Add base configuration
        lines.append(BASE_MPF_TEMPLATE.strip())
        lines.append("")

        # Set project default library
        lines.append(f"Project_DefaultLib = {top_lib}")

        inputs = list(self.inputs)
        
        # Count total files
        lines.append(f"Project_Files_Count = {len(inputs)}")
        lines.append("")

        # Add file entries in original input order (preserves dependency order)
        for file_index, resource in enumerate(inputs):
            lib = resource.library or 'work'
            file_entry = self._generate_file_entry(file_index, resource, lib)
            lines.append(file_entry)
            file_index += 1

        # Write MPF file
        output, = self.outputs
        mpf_path = output.path
        mpf_path.parent.mkdir(parents=True, exist_ok=True)
        mpf_path.write_text("\n".join(lines) + "\n")

        self.info(f"Generated QuestaSim project: {mpf_path}")


class GenerateGuiScript(Task):
    """Generate QuestaSim GUI launcher script

    Creates a shell script that opens the QuestaSim GUI with the project loaded.
    Uses absolute paths so the script can be copied and run from elsewhere.
    """

    def __init__(
        self,
        dispatcher: "Dispatcher",
        inputs: list,
        outputs: list,
    ):
        super().__init__(
            dispatcher=dispatcher,
            name="questa_gui_script",
            inputs=inputs,
            outputs=outputs,
            description="Generate QuestaSim GUI launcher"
        )

    @property
    def vsim_executable(self):
        return self.dispatcher._get_vsim_executable()
        
    async def work(self) -> None:
        """Generate the GUI launcher shell script"""
        # Get MPF project file from inputs
        mpf_file = None
        for resource in self.inputs:
            if resource.file_type == 'questa-project':
                mpf_file = resource.path.resolve()
                break

        if mpf_file is None:
            raise BuildError("No MPF project file found in inputs")

        topcell = self.dispatcher.context.get_topcell()
        top_lib = self.dispatcher.context.get_topcell_library() or "work"

        # Build shell script content
        script_content = f"""#!/bin/sh
# QuestaSim GUI launcher generated by GBS
# Opens the project in QuestaSim GUI and loads the top-level entity

cd "{mpf_file.parent}" || exit 1
exec {self.vsim_executable} -gui "{mpf_file}" -do "vsim -gui {top_lib}.{topcell}; onfinish stop; run -all"
"""

        # Write script
        output, = self.outputs
        script_path = output.path
        script_path.parent.mkdir(parents=True, exist_ok=True)
        script_path.write_text(script_content)
        script_path.chmod(0o755)

        self.info(f"Generated GUI launcher: {script_path}")
