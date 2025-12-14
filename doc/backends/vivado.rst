Vivado Backend
==============

The Vivado backend provides FPGA synthesis and implementation using Xilinx Vivado Design Suite.

Overview
--------

Vivado is Xilinx's industry-standard FPGA design suite supporting 7-series, UltraScale, UltraScale+, and Versal FPGA families. The GBS backend uses Vivado in non-project mode to run synthesis, optimization, placement, routing, and bitstream generation.

Supported Inputs
----------------

- ``vhdl``: VHDL source files
- ``verilog``: Verilog source files
- ``xilinx-xci``: Xilinx IP core files
- ``xilinx-xdc``: Xilinx Design Constraints files
- ``xilinx-constraints-tcl``: TCL-based constraint scripts
- ``vivado-block-design``: Vivado block design files
- ``vivado-init-tcl``: TCL scripts to run at project initialization

Supported Outputs
-----------------

- ``vivado-bitstream``: Final FPGA bitstream file
- ``vivado-routing-report``: Route status report
- ``vivado-timing-report``: Timing summary report
- ``vivado-power-report``: Power estimation report
- ``vivado-usage-report``: Resource utilization report
- ``vivado-netlist-edif``: Post-implementation EDIF netlist
- ``vivado-drc-report``: Design Rule Check report

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: vivado
       config:
         executable: /opt/Xilinx/Vivado/2023.1/bin/vivado

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In project file:

.. code-block:: yaml

   backend_config:
     vivado:
       vhdl_standard: "2008"  # VHDL standard: 1993, 2008, 2019
       vivado_tool: vivado    # Tool identifier for lookup

Output Group Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Target device is specified in the output group:

.. code-block:: yaml

   output_groups:
     - name: synthesis
       topcell: my_top
       target:
         part: xc7a35tcsg324-1  # Xilinx part number
       outputs:
         - type: vivado-bitstream
           path: build/design.bit

Example Project
---------------

.. code-block:: yaml

   name: fpga_design
   root_library_name: work

   output_groups:
     - name: synthesis
       topcell: top_module
       target:
         part: xc7a35tcsg324-1  # Artix-7 part
       outputs:
         - type: vivado-bitstream
           path: build/design.bit
         - type: vivado-timing-report
           path: build/timing.rpt
         - type: vivado-usage-report
           path: build/utilization.rpt

   root_partition_template:
     dependencies:
       - library.partition

Supported FPGA Families
-----------------------

The backend automatically extracts family information from part numbers:

**7-Series:**
  - Artix-7 (xc7a*)
  - Kintex-7 (xc7k*)
  - Virtex-7 (xc7v*)
  - Zynq-7000 (xc7z*)
  - Spartan-7 (xc7s*)

**UltraScale:**
  - Kintex UltraScale (xcku*)
  - Virtex UltraScale (xcvu*)

**UltraScale+:**
  - Zynq UltraScale+ (xczu*)
  - Artix UltraScale+ (xcau*)
  - Kintex UltraScale+ (xckup*)
  - Virtex UltraScale+ (xcvup*)

**Versal:**
  - Versal AI Core (xcvm*)
  - Versal Prime (xcvp*)
  - Versal AI Edge (xcve*)

Filter Variables
----------------

The Vivado backend contributes the following filter variables for conditional source selection:

- ``target-usage``: Set to ``synthesis``
- ``vendor``: Set to ``xilinx``
- ``hwdep``: Set to ``xilinx``
- ``vhdl-version``: VHDL standard from configuration
- ``target_part``: Part number from output group
- ``target_part_name``: Extracted family name (e.g., ``artix7``)

Requirements
------------

- Vivado Design Suite installed and in PATH
- Valid Vivado license
- HDL source files and constraint files

See Also
--------

- Vivado documentation: https://www.xilinx.com/support/documentation-navigation/design-hubs/dh0006-vivado-design-hub.html
- :doc:`ise` - Legacy Xilinx ISE backend
- :doc:`gowin` - Alternative FPGA vendor
