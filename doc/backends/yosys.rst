Yosys Backend
=============

The Yosys backend provides open-source FPGA synthesis using Yosys.

Overview
--------

Yosys is an open-source RTL synthesis framework supporting Verilog and VHDL (via GHDL plugin). GBS uses Yosys for synthesizing designs to various FPGA targets: Lattice iCE40, Lattice ECP5, and Xilinx Series-7 (targeting the openxc7 flow).

Supported Inputs
----------------

- ``vhdl``: VHDL source files (via GHDL plugin)
- ``verilog``: Verilog source files
- ``ghdl-cf``: GHDL compiled library files

Supported Outputs
-----------------

- ``ice40-netlist-json``: JSON netlist for Lattice iCE40 FPGAs
- ``ecp5-netlist-json``: JSON netlist for Lattice ECP5 FPGAs
- ``xilinx-netlist-json``: JSON netlist for Xilinx Series-7 FPGAs (for nextpnr-xilinx)
- ``yosys-synthesis-report``: Synthesis report (resource usage, warnings)

FPGA Targets
------------

**Lattice iCE40**
  - Pass: ``yosys-ice40``
  - Synthesis command: ``synth_ice40``
  - Output: JSON netlist for use with nextpnr-ice40

**Lattice ECP5**
  - Pass: ``yosys-ecp5``
  - Synthesis command: ``synth_ecp5``
  - Output: JSON netlist for use with nextpnr-ecp5

**Xilinx Series-7** (openxc7 flow)
  - Pass: ``yosys-xilinx``
  - Synthesis command: ``synth_xilinx``
  - Output: JSON netlist for use with nextpnr-xilinx
  - Sets vivado-style filter variables (``target_part``,
    ``target_part_name``, ``target_speed``, ``target_package``) parsed
    from the vivado-form part name, so a repository (nsl_hwdep, etc.)
    enumerates the same sources whether the project is built via
    vivado or the openxc7 flow.

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: yosys
       config:
         executable: yosys  # Path to yosys binary

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In project file:

.. code-block:: yaml

   backend_config:
     yosys:
       vhdl_standard: "2008"  # VHDL standard: 1993, 2008, 2019
       tool: yosys            # Tool identifier for lookup
       steps: []              # Optional: intermediate transformation commands

The ``steps`` option allows you to insert custom Yosys commands between reading the design and running synthesis. For example:

.. code-block:: yaml

   backend_config:
     yosys:
       steps:
         - "hierarchy -check"
         - "proc; opt"
         - "memory -nomap; opt"

Example Project (iCE40)
-----------------------

.. code-block:: yaml

   name: ice40_design
   root_library_name: work

   output_groups:
     - name: synthesis
       topcell: top_module
       outputs:
         - type: ice40-netlist-json
           path: build/design.json

   root_partition_template:
     dependencies:
       - library.partition

VHDL Support
------------

Yosys synthesizes VHDL designs using the GHDL plugin. The workflow is:

1. GHDL analyzes VHDL sources and produces ``.cf`` library files
2. Yosys reads the design using ``ghdl`` command
3. Yosys applies synthesis for the target FPGA
4. Output netlist is generated

Filter Variables
----------------

The Yosys backend contributes the following filter variables for conditional source selection:

- ``target-usage``: Set to ``synthesis``
- ``vhdl-version``: VHDL standard from configuration
- ``compiler``: Set to ``yosys``
- ``hwdep``: Device-specific (e.g., ``lattice-ice40`` for iCE40,
  ``lattice-ecp5`` for ECP5, ``xilinx`` for Xilinx Series-7)

For the Xilinx target only, the additional filter variables
``vendor=xilinx``, ``target_part``, ``target_part_name`` (e.g.
``artix7``), ``target_speed``, and ``target_package`` are set from
the vivado-style part in the output group's ``target:`` block.

Synthesis Flow
--------------

For iCE40 targets, the typical flow is:

.. code-block:: text

   VHDL/Verilog → GHDL (for VHDL) → Yosys → JSON netlist → nextpnr → icepack → bitstream

The Yosys backend handles the synthesis step, producing a JSON netlist that can be consumed by nextpnr for place-and-route.

Requirements
------------

- Yosys installed and in PATH
- For VHDL: GHDL with Yosys plugin support
- For iCE40: nextpnr-ice40 and icepack for complete flow

See Also
--------

- Yosys documentation: https://yosyshq.net/yosys/
- GHDL Yosys plugin: https://github.com/ghdl/ghdl-yosys-plugin
- :doc:`nextpnr` - Place-and-route backend
- :doc:`icepack` - Bitstream packer for iCE40
- :doc:`openxc7` - Series-7 bitstream backend
- :doc:`ghdl` - VHDL analyzer
