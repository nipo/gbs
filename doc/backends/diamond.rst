Lattice Diamond Backend
=======================

The Lattice Diamond backend provides FPGA synthesis for Lattice devices
using the Diamond tool suite, driven through its ``diamondc`` Tcl console.

Overview
--------

The backend currently supports the ECP5 family (LFE5U, LFE5UM, LFE5UM5G
and the LAE5U/LAE5UM automotive variants). Synthesis may use either the
Lattice Synthesis Engine (LSE, the default) or Synplify Pro as shipped
with Diamond.

For an open-source ECP5 flow, see the yosys/nextpnr/ecppack backends;
both flows consume the same ``ecp5-lpf`` constraints and produce the
same ``ecp5-bitstream`` output type. Because of this overlap, an output
group targeting Diamond must state it:

.. code-block:: yaml

   require_backends:
     - gbs.builtin.diamond

Configuration
-------------

Target device is specified at the output group level. ``part`` is the
full Diamond part number, as displayed in the Diamond device selector
(and stored in the ``device=`` attribute of ``.ldf`` project files):

.. code-block:: yaml

   output:
     - name: pnr
       topcell: top
       require_backends:
         - gbs.builtin.diamond
       target:
         part: LFE5U-25F-6BG256C
       outputs:
         - type: ecp5-bitstream
           path: blink.bit

Backend configuration in project file:

.. code-block:: yaml

   backend_config:
     gbs.builtin.diamond:
       tool: diamond:3.14
       synthesis: lse
       vhdl_standard: "1993"
       strategy:
         par_stop_zero: "True"

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

``target.part``
    **Required**. Full Diamond part number:
    ``{device}-{speed}{package code}{grade}``

    Examples:

    - ``LFE5U-25F-6BG256C`` - ECP5 25K, speed 6, caBGA256, commercial
    - ``LFE5UM5G-85F-8BG756I`` - ECP5UM5G 85K, speed 8, caBGA756, industrial

``tool``
    Tool identifier for Diamond lookup.

    Default: ``"diamond"``

``synthesis``
    Synthesis engine, ``lse`` or ``synplify``.

    Default: ``"lse"``

``vhdl_standard``
    ``"1993"`` or ``"2008"``. Selects the corresponding VHDL standard
    strategy setting of the active synthesis engine.

    Default: ``"1993"``

``strategy``
    Dictionary of Diamond strategy values applied verbatim with
    ``prj_strgy set_value``. Option names are the ones listed by
    ``prj_strgy list_option`` in diamondc.

Tool Configuration
------------------

Configure Diamond in ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: diamond
       variant: "3.14"
       config:
         path: /opt/Lattice/Diamond/3.14
         env:
           LM_LICENSE_FILE: ~/local/etc/lattice.dat

The ``path`` should point to the Diamond installation directory
containing ``bin/lin64/diamondc``.

Input File Types
----------------

``vhdl``
    VHDL source files

``verilog``
    Verilog source files

``ecp5-lpf``
    Logic Preference Files (pin assignments, IO types, timing
    preferences, SYSCONFIG). All LPF sources are aggregated into a
    single preference file, since a Diamond project has exactly one
    active LPF.

Output Types
------------

``ecp5-bitstream``
    BIT file for FPGA configuration. SYSCONFIG preferences in the LPF
    (e.g. ``COMPRESS_CONFIG``, ``MASTER_SPI_PORT``) control its layout.

``diamond-synthesis-report``
    Synthesis report (log and map resource usage).

``diamond-pnr-report``
    Place-and-route report (PAR log, pads, timing).

Filter Variables
----------------

The Diamond backend contributes these filter variables:

``target-usage``
    Set to ``"synthesis"``

``vendor``
    Set to ``"lattice"``

``hwdep``
    Set to ``"lattice-ecp5"`` (for hardware-dependent code selection)

``target_part``
    Full part number (e.g., ``"LFE5U-25F-6BG256C"``)

``vhdl-version``
    Configured VHDL standard

Build Process
-------------

The backend creates a Diamond project (``project.ldf``, implementation
``impl``) inside the output group build directory, then runs the
Diamond milestones, each gated by file modification times:

1. **Synthesis + Translate**: HDL to NGD (LSE emits the NGD directly;
   with Synplify, Translate converts the EDIF netlist)
2. **Map**: technology mapping against the aggregated LPF
3. **PAR**: place and route, including trace timing analysis
4. **Export/Bitgen**: bitstream generation

All milestones run in a single persistent ``diamondc`` session. The
saved project is reopened from disk when a later milestone runs in a
fresh session.

Example Project
---------------

See ``example/diamond/ecp5/blink/``:

.. code-block:: yaml

   name: blink

   root:
     name: top
     deps:
       - nsl_hwdep.clock
     sources:
       - file_type: vhdl
         files:
           - blink.vhd
       - file_type: ecp5-lpf
         files:
           - icepi-zero.lpf

   output:
     - name: pnr
       topcell: top
       require_backends:
         - gbs.builtin.diamond
       target:
         part: LFE5U-25F-6BG256C
       outputs:
         - type: ecp5-bitstream
           path: blink.bit

Troubleshooting
---------------

**diamondc not found**
    Ensure ``path`` in the tool configuration points to the Diamond
    installation directory (the one containing ``bin/lin64``).

**License errors**
    Set ``LM_LICENSE_FILE`` through the tool configuration ``env``
    section.

**Part not recognized**
    ``target.part`` must be the full part number, not the bare device
    name: ``LFE5U-25F-6BG256C``, not ``LFE5U-25F``. When the part does
    not parse as a Diamond ECP5 part number, the backend silently
    declines to contribute and planning fails with no candidate passes.

**Preference warnings about missing ports**
    LPF preferences referring to ports absent from the design are
    reported and disabled by Diamond; this is expected with shared
    board constraint files.
