Gowin Backend
=============

The Gowin backend provides FPGA synthesis for Gowin devices using
Gowin EDA tools.

Overview
--------

Gowin FPGAs are low-cost, low-power devices popular in hobbyist and
commercial applications. The GBS Gowin backend supports:

- VHDL and Verilog synthesis
- Physical and timing constraints
- Bitstream generation (FS and BIN formats)

Configuration
-------------

Backend configuration in project file:

.. code-block:: yaml

   backend_config:
     gbs.builtin.gowin:
       gowin_tool: gowin:V1.9.12
       output_base_name: design

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

``gowin_tool``
    Tool identifier for Gowin EDA lookup in ``name:variant`` format.

    Default: ``"gowin"``

``output_base_name``
    Base name for output files. If not specified, derived from project name.

Build artifacts are placed in ``gbs-build/<output_group_name>/``.
Use the ``outputs`` section to copy final files to desired locations.

Target Configuration
~~~~~~~~~~~~~~~~~~~~

Specify the target device in the project file:

.. code-block:: yaml

   target:
     part: GW5AT-LV60PG484AC1/I0
     use_as_gpio:
       - done
       - jtag

``target.part``
    Full device part number including package and speed grade.

``target.use_as_gpio``
    Optional list of special pins to use as GPIO:

    - ``done`` - Use DONE pin as GPIO
    - ``jtag`` - Use JTAG pins as GPIO

Tool Configuration
------------------

Configure Gowin EDA in ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: gowin
       variant: V1.9.12
       config:
         # Path to directory containing IDE/ and Programmer/
         path: /opt/Gowin/V1.9.12

The ``path`` should point to the Gowin EDA installation directory
that contains ``IDE/`` and ``Programmer/`` subdirectories.

Input File Types
----------------

``vhdl``
    VHDL source files

``verilog``
    Verilog source files

``gowin-cst``
    Physical constraint files (pin assignments, IO standards)

``gowin-sdc``
    Timing constraint files (SDC format)

Output Types
------------

``gowin-fs``
    SRAM bitstream file (``.fs``). Used for direct FPGA programming.

``gowin-bin``
    Binary bitstream for external flash programming.

``gowin-netlist``
    Synthesized netlist (intermediate format).

Filter Variables
----------------

The Gowin backend contributes these filter variables:

``target-usage``
    Set to ``"synthesis"``

``vendor``
    Set to ``"gowin"``

Use these for conditional source selection:

.. code-block:: yaml

   groups:
     - name: vendor_specific
       conditions:
         - expression: vendor = "gowin"
           sources:
             - file_type: vhdl
               files:
                 - gowin_pll.vhd
           deps:
             - nsl_hwdep.gowin_clock

Build Process
-------------

The Gowin build process:

1. **Synthesis**: Compile HDL to netlist using GowinSynthesis
2. **Place & Route**: Map and route design using gw_sh
3. **Bitstream**: Generate FS/BIN files

GBS uses ``gw_sh`` (Gowin Shell) for command-line synthesis.

Constraint Files
----------------

Physical Constraints (CST)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Pin assignments and IO configuration:

.. code-block:: text

   // Pin assignments
   IO_LOC "led[0]" A3;
   IO_LOC "led[1]" B3;

   // IO standards
   IO_PORT "led[0]" IO_TYPE=LVCMOS33;
   IO_PORT "clk" IO_TYPE=LVCMOS33 PULL_MODE=UP;

Timing Constraints (SDC)
~~~~~~~~~~~~~~~~~~~~~~~~

Clock definitions and timing requirements:

.. code-block:: tcl

   create_clock -name clk -period 20 [get_ports clk]
   set_false_path -from [get_ports rst_n]

Example Project
---------------

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
           - clock_pkg.vhd
           - boundary.vhd
       - file_type: gowin-cst
         files:
           - pinout.cst

   output:
     - name: synthesis
       topcell: boundary
       filter_vars:
         vendor: gowin
       backend_config:
         gbs.builtin.gowin:
           gowin_tool: gowin:V1.9.12
       outputs:
         - type: gowin-fs
           path: blink.fs

This produces:

- Build artifacts in ``gbs-build/synthesis/``
- Bitstream copied to ``blink.fs``

Build:

.. code-block:: bash

   gbs project build

Program (using Gowin Programmer):

.. code-block:: bash

   programmer_cli --device GW5AT-60 --fsFile blink.fs

Supported Devices
-----------------

The Gowin backend supports all Gowin FPGA families:

- **GW1N**: Low-power, small footprint
- **GW1NR**: With embedded BSRAM
- **GW1NS**: Ultra-low power
- **GW2A**: Mid-range with DSP
- **GW5A/GW5AT**: High-performance with transceivers

Device characteristics are automatically read from the Gowin EDA
device database.

Troubleshooting
---------------

**Gowin tools not found**
    Verify the ``path`` in tool configuration points to the correct
    Gowin EDA installation directory.

**Synthesis errors**
    Check HDL syntax and ensure all referenced libraries are available.

**Routing failures**
    Device may be too small for design. Check utilization report.

**Constraint errors**
    Verify pin names match top-level port names exactly.
