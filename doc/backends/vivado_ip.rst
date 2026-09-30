Vivado IP Packaging Backend
===========================

The Vivado IP packaging backend creates IP-XACT packages from HDL sources using Vivado's ``ipx::package_project`` flow, and checks that a packaged IP synthesizes out of context.

Overview
--------

Plugin name: ``gbs.builtin.vivado-ip``

This backend packages HDL designs as reusable Vivado IP components. The packaged IP can be used in Vivado block designs or distributed as a zip archive for integration into other projects.

Supported Inputs
----------------

- ``vhdl``: VHDL source files
- ``verilog``: Verilog source files
- ``vivado-bus-definition``: Custom bus interface XML definitions
- ``vivado-bus-zip``: Archive of custom bus interface XML definitions (see :doc:`vivado_bus`)
- ``vivado-ip-repository``: Directory of IP the packaged core refers to
- ``vivado-ip-customization-tcl``: Post-packaging TCL scripts for IP customization
- ``vivado-bd-tcl``: Block design parameter propagation scripts
- ``vivado-xgui-tcl``: Customization GUI script replacing the generated one
- ``xilinx-xdc``: Constraint files to include in the IP package
- ``vivado-ip-zip``, ``vivado-ip-dir``: Packaged IP to check, see `Synthesis Check`_

Supported Outputs
-----------------

- ``vivado-ip-zip``: Packaged IP as a zip archive
- ``vivado-ip-dir``: Packaged IP as a directory
- ``vivado-ip-synthesis-report``: Utilization report of the packaged IP
  synthesized out of context, see `Synthesis Check`_

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

The Vivado IP backend uses the same Vivado tool as the synthesis backend.
In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: vivado
       config:
         path: /opt/Xilinx/Vivado/2023.1

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

IP metadata is specified in the backend configuration:

.. code-block:: yaml

   backend_config:
     gbs.builtin.vivado-ip:
       tool: vivado               # Tool identifier for lookup
       vhdl_standard: "2008"
       vendor: com.example
       library: ip
       name: my_ip_core
       version: "1.0"
       taxonomy: /UserIP
       display_name: My IP Core
       description: A custom IP core

Output Group Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Target device is specified in the output group:

.. code-block:: yaml

   output_groups:
     - name: package
       topcell: my_ip_top
       target:
         part: xc7a35tcsg324-1  # Xilinx part number
       outputs:
         - type: vivado-ip-zip
           path: build/my_ip_core_1.0.zip

Example Project
---------------

.. code-block:: yaml

   name: my_ip_core
   root_library_name: work

   output_groups:
     - name: package
       topcell: ip_top
       target:
         part: xc7a35tcsg324-1
       outputs:
         - type: vivado-ip-zip
           path: my_ip_core_1.0.zip
         - type: vivado-ip-dir
           path: ip_repo/my_ip_core_1.0

   backend_config:
     gbs.builtin.vivado-ip:
       vendor: com.example
       library: ip
       name: my_ip_core
       version: "1.0"
       taxonomy: /UserIP
       display_name: My IP Core
       description: A reusable IP core

   root_partition_template:
     dependencies:
       - library.partition

Build:

.. code-block:: bash

   gbs project build

The packaged IP zip can then be added to a Vivado project's IP repository
or used as a ``vivado-ip-zip`` input in another GBS project (which triggers
project mode in the Vivado synthesis backend).

Synthesis Check
---------------

The ``vivado-ip-synthesis-report`` output checks that a packaged IP
synthesizes on its own, without instantiating it in a design. It checks
the package rather than the HDL sources it was made from, so it also
catches packaging mistakes:

- sources missing from a file group, or given the wrong library,
- broken customization, XGUI or block design scripts,
- a target part outside ``supported_families``,
- parameter values the IP does not accept or does not synthesize with.

Flow
~~~~

In a scratch in-memory project for the output group's part, the check:

1. adds the IP, and any IP repository and bus definition sources, to
   the IP repository paths,
2. instantiates the IP by the VLNV read from its ``component.xml``
   with ``create_ip``, as ``<name>_0``,
3. applies the parameters from ``synthesis_check_config``,
4. generates the IP targets and synthesizes the IP out of context with
   ``synth_ip``,
5. opens the resulting checkpoint, fails if a cell is left as a black
   box, and writes the utilization report with ``report_utilization``.

Out-of-context synthesis only warns about a module it cannot find and
leaves a black box in its place; a source missing from the package would
go unnoticed without the black box check.

Everything the check extracts and generates lives under
``gbs-build/<output group>/ip-check/``, which is wiped before each run:
leftovers from a previous run could stand in for files the package fails
to provide.

Configuration
~~~~~~~~~~~~~

IP parameters are given in the ``synthesis_check_config`` mapping of
the backend configuration. Names are those of the IP parameters, without
the ``CONFIG.`` prefix; values may be booleans, numbers or strings.

.. code-block:: yaml

   output:
     - name: ip_package
       topcell: pwm_generator
       target:
         part: xc7z020clg400-1
       backend_config:
         gbs.builtin.vivado-ip:
           vendor: gbs
           library: example
           name: pwm_generator
           version: "1.0"
           taxonomy: /UserIP
           synthesis_check_config:
             counter_width_c: 16
       outputs:
         - type: vivado-ip-zip
           path: pwm_generator_1.0.zip
         - type: vivado-ip-synthesis-report
           path: gbs-build/ip_package/ip-utilization.rpt

With HDL sources, the group packages the IP and checks the package in
the same build. A group asking for the report alone packages the IP
to an intermediate zip in its build directory.

The IP under check can also be a source of type ``vivado-ip-zip`` or
``vivado-ip-dir``, in which case nothing is packaged. There must be a
single such IP: the IPs the checked one depends on are given as
``vivado-ip-repository`` directories, and custom bus definitions as
``vivado-bus-definition`` or ``vivado-bus-zip`` sources.

The same check is available from the command line, on an IP file or on
the groups of a project, with :ref:`gbs vivado ip-check <cli-vivado-ip-check>`:

.. code-block:: bash

   gbs vivado ip-check pwm_generator_1.0.zip --part xc7z020clg400-1
   gbs vivado ip-check --project

Filter Variables
----------------

The Vivado IP packaging backend contributes the following filter variables:

- ``target-usage``: Set to ``synthesis``
- ``vendor``: Set to ``xilinx``
- ``hwdep``: Set to ``xilinx``
- ``vhdl-version``: VHDL standard from configuration

Error Reporting
---------------

Vivado reports most failures as ``ERROR:`` messages and keeps running its TCL
script: a source that does not parse, an IP that does not generate or a design
that does not synthesize all leave the interpreter at its prompt, ready for the
next command.

GBS collects those messages and checks them after each step of the flow. The
first step that reported an error fails the build right there, with a message
naming the step and quoting what Vivado said, so the failure is attributed to
the command that caused it instead of to a missing output file at the end.

Stalled ``srcscanner`` Watchdog
-------------------------------

Vivado spawns a headless ``srcscanner`` helper when sources are added or the
top cell is set. That helper occasionally enters an infinite loop with growing
memory usage, and the ``Vivado%`` prompt never comes back, so the build hangs
forever.

GBS watches the Vivado process it launched and kills any ``srcscanner`` of that
Vivado that has been running for more than 20 seconds. Ownership is decided on
the Unix session id, so a Vivado started outside GBS is never touched. The
build then proceeds normally: GBS passes sources in dependency order and does
not use the scanner's result.

When this happens, a ``GBS-SRCSCANNER`` warning is reported; it does not fail
the build. The watchdog is Linux-only and has no configuration knob; on other
platforms it stays off.

Requirements
------------

- Vivado Design Suite installed
- Valid Vivado license
- A 7-series or later Xilinx part in the output group
- HDL source files for the IP

See Also
--------

- :doc:`vivado` - Vivado synthesis backend (consumes ``vivado-ip-zip``)
- Vivado IP packaging documentation: https://docs.amd.com/r/en-US/ug1118-vivado-creating-packaging-custom-ip
