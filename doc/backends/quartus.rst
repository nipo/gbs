Quartus Backend
===============

The Quartus backend provides FPGA synthesis and implementation using Intel (Altera) Quartus Prime.

Overview
--------

Quartus Prime is Intel's FPGA design suite supporting Cyclone, Arria, Stratix, and MAX device families. The GBS backend generates Quartus project files and runs the full synthesis, fitting, timing analysis, and assembler flow.

The backend auto-detects whether Quartus Prime Pro or Standard edition is installed and adjusts the synthesis executable accordingly (``quartus_syn`` for Pro, ``quartus_map`` for Standard).

Supported Inputs
----------------

- ``vhdl``: VHDL source files
- ``verilog``: Verilog source files
- ``quartus-sdc``: SDC timing constraint files
- ``quartus-pin-assignment``: Pin assignment files
- ``quartus-qsys``: Intel Platform Designer (Qsys) system files
- ``quartus-qsys-script``: Platform Designer Tcl scripting API files (scripted/parameterized system generation)

Supported Outputs
-----------------

- ``quartus-sof``: SRAM Object File for FPGA programming
- ``quartus-synthesis-report``: Aggregated synthesis report
- ``quartus-pnr-report``: Aggregated place-and-route report

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: quartus
       config:
         path: /opt/intelFPGA/23.1

The ``path`` should point to the Quartus installation directory
containing the ``quartus/`` subdirectory.

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In project file:

.. code-block:: yaml

   backend_config:
     gbs.builtin.quartus:
       vhdl_standard: "2008"  # VHDL standard: 1993, 2008, 2019
       tool: quartus           # Tool identifier for lookup

Output Group Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Target device is specified in the output group:

.. code-block:: yaml

   output_groups:
     - name: synthesis
       topcell: my_top
       target:
         part: 10CL025YU256C8G  # Intel/Altera part number
       outputs:
         - type: quartus-sof
           path: build/design.sof

Example Project
---------------

.. code-block:: yaml

   name: fpga_design
   root_library_name: work

   output_groups:
     - name: synthesis
       topcell: top_module
       target:
         part: 10CL025YU256C8G  # Cyclone 10 LP
       outputs:
         - type: quartus-sof
           path: build/design.sof
         - type: quartus-synthesis-report
           path: build/synthesis.rpt
         - type: quartus-pnr-report
           path: build/pnr.rpt

   root_partition_template:
     dependencies:
       - library.partition

Build Process
-------------

The Quartus build process:

1. **Qsys Script Generation** (``qsys-script``, if ``quartus-qsys-script`` inputs are present): Run each Tcl scripting-API file to produce a ``.qsys`` system file, before Qsys generation.
2. **Qsys Generation** (``qsys-generate``, if ``quartus-qsys`` or ``quartus-qsys-script`` inputs are present): Expand each Platform Designer system into synthesizable HDL and a ``.qip`` file, before project setup.
3. **Project Setup**: Generate ``.qpf`` and ``.qsf`` project files with HDL sources, device, pin assignments, SDC constraints, and any generated ``.qip`` files.
4. **Analysis & Synthesis** (``quartus_map`` / ``quartus_syn``): Compile HDL to netlist.
5. **Fitter** (``quartus_fit``): Place and route the design.
6. **Timing Analysis** (``quartus_sta``): Analyze timing paths against constraints.
7. **Assembler** (``quartus_asm``): Generate the ``.sof`` bitstream file.

Qsys Systems
------------

A ``quartus-qsys`` input is a ``.qsys`` system file authored externally (typically via the Platform Designer GUI) and checked into the project like any other source file:

.. code-block:: yaml

   root:
     sources:
       - file_type: quartus-qsys
         files:
           - hdl/my_system.qsys

GBS runs ``qsys-generate`` on it to produce a ``.qip`` file, which is then referenced from the ``.qsf`` via a ``QIP_FILE`` assignment — Quartus resolves the actual generated HDL itself from there, the same way it does for SDC and pin-assignment files. The ``qsys-generate`` executable is expected at ``<path>/qsys/bin/qsys-generate``, alongside the ``quartus/bin`` directory used for the rest of the toolchain.

``qsys-generate`` also breaks any hardened or catalog IP core in the system (PLLs, HPS, EMIF, Generic Components, ...) out into its own nested ``.qip`` file under ``ip/<system_name>/<instance>/<instance>.qip``, rather than folding it into the top-level ``.qip``. It only registers those with a Quartus project automatically when invoked with ``--quartus-project``, which GBS doesn't do — so ``ProjectSetup`` discovers every nested ``.qip`` under that directory at generation time and gives each one its own ``QIP_FILE`` assignment too. Without this, Quartus reports the corresponding instances as undefined entities during elaboration, since the HDL that instantiates them exists but nothing tells Quartus where to find their implementation.

``qsys-generate`` has no flag to redirect where it writes its output: it always creates a ``<system_name>/`` directory as a sibling of whatever ``.qsys`` file it's given (containing ``<system_name>.qip`` plus the generated HDL), regardless of the current directory. To keep that output scoped to the build directory instead of landing next to your checked-in source ``.qsys``, GBS stages a copy of it under ``gbs-build/.../output_files/qsys/`` first and runs ``qsys-generate`` on the copy — the same pattern used for Vivado block designs. Generation output therefore stays entirely under ``gbs-build/``, cleaned by the normal ``gbs clean``.

**Generic Components**: if your system has instances added via Platform Designer's "Generic Component" mechanism with **Implementation Type: IP** (as opposed to a plain catalog component), that instance's actual IP core selection and parameters live in a per-instance ``.ip`` file (IP-XACT), not in the ``.qsys`` itself. ``qsys-generate`` looks for these at ``ip/<system_name>/<system_name>_<instance>.ip``, relative to the ``.qsys`` file, and silently skips generating an implementation for any instance it can't find one for — the resulting ``.v``/``.vhd`` will instantiate an entity that's never defined, which only surfaces later as a Quartus elaboration error. If you have such instances, check in the corresponding ``.ip`` files alongside your ``.qsys`` at ``ip/<system_name>/``:

.. code-block:: text

   hdl/
     my_system.qsys
     ip/
       my_system/
         my_system_some_instance.ip

GBS stages this ``ip/<system_name>/`` directory alongside the ``.qsys`` copy automatically, if present. Implementation Types **HDL** and **Blackbox** have no ``.ip`` file at all — those need their entity supplied as a regular ``vhdl``/``verilog`` source in your project instead (Blackbox components are never auto-generated; see Intel's *Quartus Prime Pro Edition User Guide: Platform Designer*, section "Creating Generic Components in a System").

Scripted Qsys Generation
-------------------------

A ``quartus-qsys-script`` input is a ``.tcl`` file written against Platform Designer's system scripting API (``add_instance``, ``add_connection``, ``set_instance_parameter_value``, ``save_system``, ...), letting a system's topology be described as a checked-in, diffable script instead of only a binary/XML ``.qsys`` saved from the GUI:

.. code-block:: yaml

   root:
     sources:
       - file_type: quartus-qsys-script
         files:
           - hdl/my_system.tcl

GBS runs ``qsys-script`` on it to produce a ``.qsys`` file, which then flows through the same ``qsys-generate`` step described above.

The script must end with a bare ``save_system <name>`` call, where ``<name>`` matches the ``.tcl`` file's own stem — exactly what Platform Designer's own "Export System as Platform Designer script" feature (or ``qsys-generate --export-qsys-script``) already produces, so a script exported straight from the GUI works as-is. GBS runs ``qsys-script`` against a staged copy of your script under ``gbs-build/``, not the original, and expects the resulting ``.qsys`` next to that copy.

That staging matters for another reason too: without ``--quartus-project``/``--new-quartus-project`` (which GBS doesn't pass), ``qsys-script`` auto-creates a companion Quartus project named after the script file, next to it, and refuses to run again if one already exists from a previous run. Staging under ``gbs-build/`` keeps that byproduct out of your source tree and lets GBS wipe it before every run.

Since GBS doesn't pass ``--package-version``, your script must also declare the scripting API version itself, e.g.:

.. code-block:: tcl

   package require -exact qsys 16.0

If your script's ``add_component`` calls reference Generic Component ``.ip`` files by relative path (as Platform-Designer-exported scripts typically do, e.g. ``ip/other_system/some_instance.ip``) — note these are resolved relative to the script file itself, and can reach into *any* system's ``ip/`` subfolder, not just the one being generated. GBS stages the entire ``ip/`` tree next to your source script alongside the staged copy, so these keep resolving correctly.

See Intel's *Quartus Prime Pro Edition User Guide: Platform Designer*, section "Generate a Platform Designer System with qsys-script", for the full scripting API.

Filter Variables
----------------

The Quartus backend contributes the following filter variables for conditional source selection:

- ``target-usage``: Set to ``synthesis``
- ``vendor``: Set to ``altera``
- ``hwdep``: Set to ``altera``
- ``vhdl-version``: VHDL standard from configuration
- ``target_part``: Part number from output group

Requirements
------------

- Quartus Prime installed (Lite, Standard, or Pro edition)
- Valid Quartus license (for Standard/Pro)
- HDL source files and constraint files

See Also
--------

- Quartus documentation: https://www.intel.com/content/www/us/en/programmable/downloads/download-center.html
- :doc:`vivado` - Xilinx Vivado backend
- :doc:`gowin` - Gowin FPGA backend
