Project File Reference
======================

Project files (``*.gbs.yaml``) define what to build and how. This document
covers all project file options in detail.

Basic Structure
---------------

.. code-block:: yaml

   name: project_name

   # Root partition definition
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

The ``root`` section defines the project's root partition, placed in the
"work" library.

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
       - file_type: xilinx-ucf
         files:
           - constraints.ucf

       # Gowin constraints
       - file_type: gowin-cst
         files:
           - pinout.cst

Supported file types:

- ``vhdl`` - VHDL source files
- ``verilog`` - Verilog source files
- ``systemverilog`` - SystemVerilog source files
- ``gowin-cst`` - Gowin constraint files
- ``xilinx-ucf`` - Xilinx UCF constraint files

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
synthesis backends:

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
           ghdl_tool: ghdl:llvm

     - name: synthesis
       backend_config:
         gbs.builtin.gowin:
           part: GW5AT-LV60PG484AC1/I0

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

**Gowin:**

- ``gowin-fs`` - FS bitstream file
- ``gowin-bin`` - Binary bitstream for flash

**Xilinx ISE:**

- ``ise-bitstream`` - BIT file
- ``ise-timing`` - Timing report

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
           vhdl_standard: "93c"
           ghdl_tool: ghdl:system
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

   target:
     part: GW5AT-LV60PG484AC1/I0
     use_as_gpio:
       - done

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
       filter_vars:
         vendor: gowin
         target-usage: synthesis
       backend_config:
         gbs.builtin.gowin:
           part: GW5AT-LV60PG484AC1/I0
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
       backend_config:
         gbs.builtin.ise:
           target:
             part: xc6slx9-2tqg144
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
       filter_vars:
         vendor: gowin
       backend_config:
         gbs.builtin.gowin:
           part: GW5AT-LV60PG484AC1/I0
       outputs:
         - type: gowin-fs
           path: release/gowin.fs

     # Xilinx ISE synthesis
     - name: ise
       topcell: top
       filter_vars:
         vendor: xilinx
       backend_config:
         gbs.builtin.ise:
           target:
             part: xc6slx9-2tqg144
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
