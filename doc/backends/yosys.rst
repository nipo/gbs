Yosys Backend
=============

The Yosys backend provides open-source FPGA synthesis using Yosys.

Overview
--------

Yosys is an open-source RTL synthesis framework supporting Verilog and VHDL (via GHDL plugin). GBS uses Yosys for synthesizing designs to various FPGA targets, with built-in support for Lattice iCE40 devices.

Supported Inputs
----------------

- ``vhdl``: VHDL source files (via GHDL plugin)
- ``verilog``: Verilog source files
- ``ghdl-cf``: GHDL compiled library files

Supported Outputs
-----------------

- ``ice40-netlist-json``: JSON netlist for Lattice iCE40 FPGAs

FPGA Targets
------------

Currently Supported:
~~~~~~~~~~~~~~~~~~~~

**Lattice iCE40**
  - Pass: ``yosys-ice40``
  - Synthesis command: ``synth_ice40``
  - Output: JSON netlist for use with nextpnr-ice40

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
       yosys_tool: yosys      # Tool identifier for lookup
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
- ``hwdep``: Device-specific (e.g., ``lattice-ice40`` for iCE40 pass)

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
- :doc:`ghdl` - VHDL analyzer
