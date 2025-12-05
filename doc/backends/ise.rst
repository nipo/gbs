Xilinx ISE Backend
==================

The Xilinx ISE backend provides FPGA synthesis for legacy Xilinx devices
using the ISE Design Suite (version 14.7, 2014).

Overview
--------

Xilinx ISE supports older Xilinx FPGA families:

- Spartan-3, Spartan-3E, Spartan-3A
- Spartan-6
- Virtex-4, Virtex-5, Virtex-6

For newer devices (7-series and later), use Vivado instead.

Configuration
-------------

Backend configuration in project file:

.. code-block:: yaml

   backend_config:
     gbs.builtin.ise:
       target:
         part: xc6slx9-2tqg144
       output_dir: ise-build
       tool: ise:14.7

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

``target.part``
    **Required**. Target device in format: ``{device}{speed}{package}``

    Examples:

    - ``xc6slx9-2tqg144`` - Spartan-6 LX9, -2 speed, TQG144 package
    - ``xc3s500e-4fg320`` - Spartan-3E 500K, -4 speed, FG320 package

``output_dir``
    Directory for build artifacts.

    Default: ``"ise-build"``

``tool``
    Tool identifier for ISE lookup.

    Default: ``"ise"``

``output_base_name``
    Base name for output files. If not specified, derived from project name.

Tool Configuration
------------------

Configure Xilinx ISE in ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: ise
       variant: "14.7"
       config:
         path: /opt/Xilinx/14.7

The ``path`` should point to the ISE installation directory containing
``ISE_DS/`` subdirectory.

Input File Types
----------------

``vhdl``
    VHDL source files

``verilog``
    Verilog source files

``xilinx-ucf``
    User Constraint File (pin assignments, timing constraints)

Output Types
------------

``ise-bitstream``
    BIT file for FPGA configuration

``ise-timing-report``
    Timing analysis report from TRCE

``ise-netlist``
    Synthesized netlist (NGC format)

Filter Variables
----------------

The ISE backend contributes these filter variables:

``target-usage``
    Set to ``"synthesis"``

``vendor``
    Set to ``"xilinx"``

``hwdep``
    Set to ``"xilinx"`` (for hardware-dependent code selection)

``target_part``
    Full part string (e.g., ``"xc6slx9-2tqg144"``)

``part_name``
    Device name without speed/package (e.g., ``"xc6slx9"``)

``part_speed``
    Speed grade (e.g., ``"-2"``)

``part_package``
    Package type (e.g., ``"tqg144"``)

Use these for conditional source selection:

.. code-block:: yaml

   groups:
     - name: xilinx_specific
       conditions:
         - expression: vendor = "xilinx"
           sources:
             - file_type: vhdl
               files:
                 - xilinx_primitives.vhd
           deps:
             - nsl_hwdep.xilinx_clock

Build Process
-------------

The ISE build process follows the classic FPGA flow:

1. **XST** (Xilinx Synthesis Technology): HDL to netlist
2. **NGDBUILD**: Netlist to NGD (Native Generic Database)
3. **MAP**: Technology mapping to device resources
4. **PAR**: Place and route
5. **TRCE**: Timing analysis
6. **BITGEN**: Bitstream generation

Each step is executed as a separate task with proper dependency tracking.

Constraint Files (UCF)
----------------------

UCF files specify pin assignments and timing constraints:

.. code-block:: text

   # Pin assignments
   NET "clk"     LOC = "P55" | IOSTANDARD = LVCMOS33;
   NET "led<0>"  LOC = "P134" | IOSTANDARD = LVCMOS33;
   NET "led<1>"  LOC = "P133" | IOSTANDARD = LVCMOS33;

   # Timing constraints
   NET "clk" TNM_NET = clk;
   TIMESPEC TS_clk = PERIOD "clk" 20 ns HIGH 50%;

   # Placement hints
   INST "core/fifo" AREA_GROUP = "AG_fifo";

Example Project
---------------

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

Build:

.. code-block:: bash

   gbs project build

Program (using iMPACT or xc3sprog):

.. code-block:: bash

   xc3sprog -c jtaghs2 blink.bit

ISE Tool Chain
--------------

XST (Synthesis)
~~~~~~~~~~~~~~~

Synthesizes HDL to Xilinx netlist (NGC). Options controlled by
project configuration.

NGDBUILD
~~~~~~~~

Merges netlist with constraints to create NGD file. Resolves
IP cores and generates mapping input.

MAP
~~~

Maps logical design to physical device resources (CLBs, IOBs,
BRAMs, DSPs).

PAR (Place and Route)
~~~~~~~~~~~~~~~~~~~~~

Places components and routes interconnects. This is typically
the longest step.

TRCE (Timing Analysis)
~~~~~~~~~~~~~~~~~~~~~~

Analyzes timing paths against constraints. Reports violations
and timing margins.

BITGEN
~~~~~~

Generates final bitstream for FPGA configuration.

Output Files
------------

The ISE backend produces these files in the output directory:

=============== ==========================================
File            Description
=============== ==========================================
``*.ngc``       Synthesized netlist
``*.ngd``       Native Generic Database
``*.ncd``       Native Circuit Description (placed/routed)
``*.pcf``       Physical Constraints File (from PAR)
``*.twr``       Timing report
``*.bit``       Configuration bitstream
``*.bin``       Binary bitstream (for flash)
=============== ==========================================

Troubleshooting
---------------

**ISE tools not found**
    Ensure ISE is installed and ``path`` points to correct directory.
    Verify ISE license is configured.

**Part not recognized**
    Check part string format. Use ISE part selector for valid strings.

**Timing violations**
    Review ``.twr`` file for failing paths. Consider relaxing constraints
    or optimizing critical paths.

**Routing failures**
    Device may be too full. Check utilization in MAP report. Consider
    larger device or design optimization.

**UCF errors**
    Verify net names match top-level port names. Use ``<n>`` syntax
    for bus signals (not ``[n]``).

Legacy Notes
------------

Xilinx ISE reached end-of-life in 2014. For new designs:

- Use Vivado for 7-series and newer devices
- ISE 14.7 remains the only option for Spartan-6 and older

ISE runs on older Linux distributions. On modern systems, you may need
compatibility libraries or a container/VM.
