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

1. **Project Setup**: Generate ``.qpf`` and ``.qsf`` project files with HDL sources, device, pin assignments, and SDC constraints.
2. **Analysis & Synthesis** (``quartus_map`` / ``quartus_syn``): Compile HDL to netlist.
3. **Fitter** (``quartus_fit``): Place and route the design.
4. **Timing Analysis** (``quartus_sta``): Analyze timing paths against constraints.
5. **Assembler** (``quartus_asm``): Generate the ``.sof`` bitstream file.

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
