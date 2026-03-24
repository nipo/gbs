Project File Reference
======================

Project files (``*.gbs.yaml``) define what to build and how. This document
covers all project file options in detail.

Basic Structure
---------------

.. code-block:: yaml

   name: project_name

   # Root partition definition (single)
   root:
     name: partition_name
     deps:
       - library.partition
     sources:
       - file_type: vhdl
         files:
           - file1.vhd
           - file2.vhd

   # Build outputs
   output:
     - name: output_group_name
       topcell: entity_name
       filter_vars:
         variable: value
       target:
         part: device_part_number
       backend_config:
         backend.module:
           option: value
       outputs:
         - type: output_type
           path: output/path

Project Metadata
----------------

name
~~~~

Required. Project name used for identification.

.. code-block:: yaml

   name: my_fpga_project

Root Partition
--------------

The ``root`` section defines the project's root partition(s), placed in the
"work" library. It can be either a single dict (backward compatible) or a
list of named partitions.

**Single partition** (dict form):

.. code-block:: yaml

   root:
     name: top
     sources:
       - file_type: vhdl
         files:
           - top.vhd

**Multiple partitions** (list form):

.. code-block:: yaml

   root:
     - name: partition_a
       sources:
         - file_type: vhdl
           files:
             - design_a.vhd

     - name: partition_b
       sources:
         - file_type: vhdl
           files:
             - design_b.vhd

When multiple root partitions are defined, each output group must specify
which partition it builds from using the ``partition:`` field (see
`output[].partition`_).

root.name
~~~~~~~~~

Partition name:

.. code-block:: yaml

   root:
     name: top

root.deps
~~~~~~~~~

Dependencies on other partitions (format: ``library.partition``):

.. code-block:: yaml

   root:
     name: top
     deps:
       - nsl_data.text
       - nsl_simulation.assertions
       - mylib.utils

root.sources
~~~~~~~~~~~~

Source file definitions:

.. code-block:: yaml

   root:
     name: top
     sources:
       # Simple file list
       - file_type: vhdl
         files:
           - top.vhd
           - utils.vhd

       # With variant (e.g., VHDL-2008)
       - file_type: vhdl
         variant: "2008"
         files:
           - modern_code.vhd

       # Constraint files
       - file_type: xilinx-xdc
         files:
           - constraints.xdc

       # Gowin constraints
       - file_type: gowin-cst
         files:
           - pinout.cst

Supported file types:

- ``vhdl`` - VHDL source files
- ``verilog`` - Verilog source files
- ``systemverilog`` - SystemVerilog source files
- ``gowin-cst`` - Gowin physical constraint files
- ``gowin-serdes-init`` - Gowin SerDes initialization files
- ``xilinx-ucf`` - Xilinx UCF constraint files (ISE)
- ``xilinx-xdc`` - Xilinx XDC constraint files (Vivado)
- ``xilinx-xci`` - Xilinx IP core files
- ``xilinx-constraints-tcl`` - TCL-based constraint scripts
- ``vivado-block-design`` - Vivado block design files
- ``vivado-init-tcl`` - TCL scripts to run at Vivado project init
- ``vivado-bus-definition`` - Bus interface XML definitions (for IP packaging)
- ``vivado-ip-customization-tcl`` - Post-packaging TCL scripts (for IP packaging)
- ``quartus-sdc`` - Quartus timing constraints (SDC format)
- ``quartus-pin-assignment`` - QSF pin assignment fragments

Output Groups
-------------

The ``output`` section defines build targets. Each output group can have
different configuration and produce different outputs.

output[].name
~~~~~~~~~~~~~

Output group name (for identification):

.. code-block:: yaml

   output:
     - name: simulation
       # ...

     - name: synthesis
       # ...

output[].topcell
~~~~~~~~~~~~~~~~

Top-level entity/module name:

.. code-block:: yaml

   output:
     - name: simulation
       topcell: testbench

     - name: synthesis
       topcell: top

output[].partition
~~~~~~~~~~~~~~~~~~

When multiple root partitions are defined, selects which partition this
output group builds from. Required when multiple partitions exist, optional
when only one.

.. code-block:: yaml

   root:
     - name: partition_a
       sources:
         - file_type: vhdl
           files: [design_a.vhd]

     - name: partition_b
       sources:
         - file_type: vhdl
           files: [design_b.vhd]

   output:
     - name: build_a
       topcell: design_a
       partition: partition_a
       # ...

     - name: build_b
       topcell: design_b
       partition: partition_b
       # ...

output[].filter_vars
~~~~~~~~~~~~~~~~~~~~

Filter variables for conditional source selection:

.. code-block:: yaml

   output:
     - name: synthesis
       filter_vars:
         vendor: gowin
         target-usage: synthesis

output[].target
~~~~~~~~~~~~~~~

Target device configuration (optional). Device-specific settings used by
synthesis backends. The planner injects ``output_group.target`` into each
backend's config automatically.

.. code-block:: yaml

   output:
     - name: synthesis
       target:
         part: GW5AT-LV60PG484AC1/I0   # Gowin
         use_as_gpio:                  # Gowin-specific option
           - done
           - jtag

     - name: xilinx_synthesis
       target:
         part: xc6slx9-2tqg144          # Xilinx

**Common options:**

- ``part``: Target device part number (required for synthesis)

**Gowin-specific options:**

- ``use_as_gpio``: List of special pins to use as GPIO (e.g., ``done``, ``jtag``)

These variables are used to evaluate conditional groups in partitions.

output[].backend_config
~~~~~~~~~~~~~~~~~~~~~~~

Backend-specific configuration:

.. code-block:: yaml

   output:
     - name: simulation
       backend_config:
         gbs.builtin.ghdl:
           vhdl_standard: "2008"
           tool: ghdl:llvm

     - name: synthesis
       target:
         part: GW5AT-LV60PG484AC1/I0
       backend_config:
         gbs.builtin.gowin: {}

The ``tool:`` key inside each backend's config selects which tool
variant to use (looked up in the GBS config's tool definitions).

See backend documentation for available options:

- :doc:`backends/ghdl`
- :doc:`backends/gowin`
- :doc:`backends/ise`

output[].outputs
~~~~~~~~~~~~~~~~

List of desired output files to extract from the build:

.. code-block:: yaml

   output:
     - name: simulation
       outputs:
         - type: ghdl-simulator
           path: sim/testbench

     - name: synthesis
       outputs:
         - type: gowin-fs
           path: release/design.fs
         - type: gowin-bin
           path: release/design.bin

Each output entry specifies:

- ``type``: The file type to look for in the build output
- ``path``: Where to copy the file (relative to project directory)

**Build Directory Structure**

GBS uses a stable internal build directory structure:

.. code-block:: text

   gbs-build/
   └── <output_group_name>/
       └── <intermediate and final build artifacts>

For example, with an output group named ``synthesis``, all build artifacts
are placed in ``gbs-build/synthesis/``.

The ``outputs`` entries specify which files to copy from the build directory
to user-specified locations. This is handled by the output-copy pass which
runs after all other build steps complete.

**Compression**

Output types can include compression suffixes to automatically compress files:

.. code-block:: yaml

   outputs:
     - type: ise-bitstream+gzip
       path: firmware.bit.gz

Supported compression suffixes:

- ``+gzip`` - Standard gzip compression

**Output types** depend on the backend:

**GHDL:**

- ``ghdl-simulator`` - Simulator executable
- ``simulator`` - Generic simulator (GHDL will match)
- ``waveform-vcd`` - VCD waveform dump
- ``waveform-ghw`` - GHW waveform dump
- ``waveform-fst`` - FST waveform dump
- ``simulation-log`` - Simulation log output
- ``simulation-success`` - Simulation success marker

**NVC:**

- ``nvc-simulator`` - NVC simulator executable

**QuestaSim:**

- ``questa-project`` - MPF project file for QuestaSim GUI
- ``questa-gui-launcher`` - Shell script to launch QuestaSim GUI

**Gowin:**

- ``gowin-fs`` - FS bitstream file
- ``gowin-bin`` - Binary bitstream for flash
- ``gowin-netlist`` - Synthesized netlist

**Xilinx ISE:**

- ``ise-bitstream`` - BIT file
- ``ise-timing-report`` - Timing report
- ``ise-netlist`` - Post-synthesis netlist

**Xilinx Vivado:**

- ``vivado-bitstream`` - Final bitstream file
- ``vivado-routing-report`` - Route status report
- ``vivado-timing-report`` - Timing summary report
- ``vivado-power-report`` - Power estimation report
- ``vivado-usage-report`` - Resource utilization report
- ``vivado-netlist-edif`` - Post-implementation EDIF netlist
- ``vivado-drc-report`` - Design Rule Check report

**Vivado IP Packaging:**

- ``vivado-ip-zip`` - Packaged IP as a zip archive
- ``vivado-ip-dir`` - Packaged IP as a directory

**Yosys + nextpnr (iCE40):**

- ``ice40-netlist-json`` - Yosys JSON netlist for iCE40
- ``ice40-asc`` - nextpnr ASCII bitstream
- ``ice40-bin`` - icepack binary bitstream
- ``ice40-bitstream`` - icepack bitstream (alias)

**Yosys + nextpnr (ECP5):**

- ``ecp5-netlist-json`` - Yosys JSON netlist for ECP5
- ``ecp5-config`` - nextpnr configuration
- ``ecp5-bit`` - ecppack bitstream
- ``ecp5-bitstream`` - ecppack bitstream (alias)

**Quartus:**

- ``quartus-sof`` - SRAM Object File (bitstream)

**Report output types:**

All synthesis and place-and-route backends support report aggregation.
These can be requested as output types:

- ``gowin-synthesis-report``, ``gowin-pnr-report``
- ``vivado-synthesis-report``, ``vivado-pnr-report``
- ``ise-synthesis-report``, ``ise-pnr-report``
- ``yosys-synthesis-report``
- ``nextpnr-pnr-report``
- ``quartus-synthesis-report``, ``quartus-pnr-report``

Complete Examples
-----------------

Simulation Project
~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: uart_test

   root:
     name: testbench
     deps:
       - nsl_data.bytestream
       - nsl_io.uart
     sources:
       - file_type: vhdl
         files:
           - tb_uart.vhd

   output:
     - name: simulation
       topcell: tb_uart
       filter_vars:
         target-usage: simulation
       backend_config:
         gbs.builtin.ghdl:
           vhdl_standard: "1993"
           tool: ghdl:system
       outputs:
         - type: ghdl-simulator
           path: tb_uart

This produces:

- Build artifacts in ``gbs-build/simulation/``
- Simulator executable copied to ``tb_uart``

Gowin Synthesis Project
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: blink

   root:
     name: top
     deps:
       - nsl_hwdep.clock
     sources:
       - file_type: vhdl
         files:
           - clock.pkg.vhd
           - boundary.vhd
       - file_type: gowin-cst
         files:
           - pinout.cst

   output:
     - name: synthesis
       topcell: boundary
       target:
         part: GW5AT-LV60PG484AC1/I0
         use_as_gpio:
           - done
       filter_vars:
         vendor: gowin
         target-usage: synthesis
       backend_config:
         gbs.builtin.gowin: {}
       outputs:
         - type: gowin-fs
           path: blink.fs

This produces:

- Build artifacts in ``gbs-build/synthesis/``
- Bitstream copied to ``blink.fs``

Xilinx ISE Project
~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: blink

   root:
     name: top
     deps:
       - nsl_hwdep.clock
     sources:
       - file_type: vhdl
         files:
           - boundary.vhd
       - file_type: xilinx-ucf
         files:
           - led.ucf

   output:
     - name: synthesis
       topcell: boundary
       target:
         part: xc6slx9-2tqg144
       backend_config:
         gbs.builtin.ise: {}
       outputs:
         - type: ise-bitstream
           path: blink.bit

This produces:

- Build artifacts in ``gbs-build/synthesis/``
- Bitstream copied to ``blink.bit``

Multiple Output Groups
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: design

   root:
     name: top
     deps:
       - mylib.core
     sources:
       - file_type: vhdl
         files:
           - top.vhd

   output:
     # Simulation
     - name: simulation
       topcell: tb_top
       filter_vars:
         target-usage: simulation
       backend_config:
         gbs.builtin.ghdl:
           vhdl_standard: "2008"
       outputs:
         - type: simulator
           path: sim/tb_top

     # Gowin synthesis
     - name: gowin
       topcell: top
       target:
         part: GW5AT-LV60PG484AC1/I0
       filter_vars:
         vendor: gowin
       backend_config:
         gbs.builtin.gowin: {}
       outputs:
         - type: gowin-fs
           path: release/gowin.fs

     # Xilinx ISE synthesis
     - name: ise
       topcell: top
       target:
         part: xc6slx9-2tqg144
       filter_vars:
         vendor: xilinx
       backend_config:
         gbs.builtin.ise: {}
       outputs:
         - type: ise-bitstream
           path: release/ise.bit

This produces:

- ``gbs-build/simulation/`` - GHDL work library and intermediate files
- ``gbs-build/gowin/`` - Gowin synthesis artifacts
- ``gbs-build/ise/`` - Xilinx ISE project files and reports
- ``sim/tb_top`` - Simulator executable (copied from build)
- ``release/gowin.fs`` - Gowin bitstream (copied from build)
- ``release/ise.bit`` - ISE bitstream (copied from build)

Multiple Root Partitions
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: multi_design

   root:
     - name: fpga_a
       deps:
         - mylib.core
       sources:
         - file_type: vhdl
           files:
             - fpga_a_top.vhd
         - file_type: xilinx-xdc
           files:
             - fpga_a.xdc

     - name: fpga_b
       deps:
         - mylib.core
       sources:
         - file_type: vhdl
           files:
             - fpga_b_top.vhd
         - file_type: xilinx-xdc
           files:
             - fpga_b.xdc

   output:
     - name: build_a
       topcell: fpga_a_top
       partition: fpga_a
       target:
         part: xc7a35tcpg236-1
       backend_config:
         gbs.builtin.vivado: {}
       outputs:
         - type: vivado-bitstream
           path: release/fpga_a.bit

     - name: build_b
       topcell: fpga_b_top
       partition: fpga_b
       target:
         part: xc7a100tcsg324-1
       backend_config:
         gbs.builtin.vivado: {}
       outputs:
         - type: vivado-bitstream
           path: release/fpga_b.bit
