Filter Variables
================

Filter variables are the environment that repository loaders consult to
decide which sources and dependencies apply to a build. They are
produced by backend passes during planning, merged into a single flat
dictionary, and evaluated against filter expressions in repository
definitions.

This page defines the canonical variable set. Every builtin backend
emits variables from this set; every project or repository written for
GBS should filter on these variables.

Naming conventions
------------------

- Variable names use snake_case. No dashes.
- Values are lowercase strings, except where the value is a raw
  identifier from the outside world (e.g. a part number).
- A variable is either set or absent. There is no *empty-string*
  distinction: absent means "no information", and filter expressions
  match absent variables as *not matching*, so a source gated on
  ``vendor = "xilinx"`` is skipped when ``vendor`` is unset.

Absence semantics
-----------------

The technology-stack variables (``vendor``, ``family``, ``die``,
``speed``, ``package``, ``part``) are only set when a hardware target
is defined. This means a plain simulation build with no hardware
target has all of them unset, which is how "compile against generic
behavioural mockups" gets expressed: sources gated on any specific
vendor do not match, and the ``default`` fallback picks up the
mockups.

Variables
---------

Purpose
~~~~~~~

``purpose``
    What the build ultimately produces. Always set.

    Values: ``synthesis`` | ``simulation`` | ``asic``

    ``synthesis`` covers every stage of a hardware build (mapping,
    place-and-route, bitstream generation) — those stages consume
    netlists rather than HDL sources, so no finer distinction is
    needed for source enumeration.

Pipeline stages
~~~~~~~~~~~~~~~

Each pass in the plan owns at most one of these axes. Because filter
variables from all passes are unioned into a single dictionary before
evaluation, splitting the tool axis by stage avoids collisions when a
pipeline mixes tools (e.g. yosys synthesis feeding a vendor PnR).

``vhdl_frontend``
    The VHDL parser/analyzer active in this build.

    Values (examples): ``ghdl_mcode``, ``ghdl_llvm``, ``ghdl_gcc``,
    ``nvc``, ``lse``, ``synplify``, ``vivado``, ``ise``, ``verific``,
    ``questa``, ``xsim``.

    GHDL variants encode the backend flavour directly in the value,
    so a source that needs VHPIDIRECT (unavailable on ``ghdl_mcode``)
    filters as ``vhdl_frontend matches "^ghdl_(llvm|gcc)"``. A source
    that just needs "any GHDL" filters as
    ``vhdl_frontend matches "^ghdl_"``.

``verilog_frontend``
    The Verilog parser active in this build.

    Values (examples): ``yosys``, ``slang``, ``verific``, ``vivado``,
    ``ise``, ``questa``, ``icarus``, ``xsim``.

``synthesis_engine``
    The tool that produces the netlist. Set only when
    ``purpose = "synthesis"``.

    Values (examples): ``vivado``, ``ise``, ``yosys``, ``quartus``,
    ``lse``, ``synplify``, ``gowin_synth``, ``diamond``.

``pnr_engine``
    The place-and-route tool. Set only when the pipeline includes a
    dedicated PnR stage.

    Values (examples): ``vivado``, ``ise``, ``nextpnr``, ``quartus``,
    ``diamond``, ``gowin_pnr``.

``simulation_engine``
    The simulator that will execute the compiled design. Set only
    when ``purpose = "simulation"``. GHDL variants are folded into
    the engine value, mirroring ``vhdl_frontend``.

    Values (examples): ``ghdl_mcode``, ``ghdl_llvm``, ``ghdl_gcc``,
    ``questa``, ``nvc``, ``xsim``, ``icarus``, ``verilator``.

``bitstream_engine``
    The tool that produces the final bitstream. Set only when a
    bitstream is being generated.

    Values (examples): ``vivado``, ``ise``, ``openxc7``, ``icepack``,
    ``ecppack``, ``quartus``, ``gowin_bit``.

Technology stack
~~~~~~~~~~~~~~~~

``vendor``
    Silicon vendor.

    Values: ``xilinx``, ``lattice``, ``altera``, ``gowin``, ``efinix``.

``family``
    Marketing family — the level at which per-family primitive
    libraries and I/O blocks tend to be shared. Choose the value from
    the table below; use ``die matches "..."`` when finer granularity
    is needed.

    +----------------+-----------------------------------------------+
    | Vendor         | Family values                                 |
    +================+===============================================+
    | xilinx         | ``spartan6``, ``virtex6``,                    |
    |                | ``spartan7``, ``artix7``, ``kintex7``,        |
    |                | ``virtex7``, ``zynq7``,                       |
    |                | ``kintexu``, ``virtexu``,                     |
    |                | ``artixusp``, ``kintexusp``, ``virtexusp``,   |
    |                | ``zynqusp``,                                  |
    |                | ``versal``                                    |
    +----------------+-----------------------------------------------+
    | lattice        | ``ice40``, ``ecp5``, ``machxo2``, ``nexus``,  |
    |                | ``certus``                                    |
    +----------------+-----------------------------------------------+
    | altera / intel | ``cyclone10``, ``arria10``, ``stratix10``,    |
    |                | ``agilex3``, ``agilex5``, ``agilex7``,        |
    |                | ``agilex9``                                   |
    +----------------+-----------------------------------------------+
    | gowin          | ``gw1n``, ``gw1nz``, ``gw2a``, ``gw5a``,      |
    |                | ``gw5ast``                                    |
    +----------------+-----------------------------------------------+
    | efinix         | ``trion``, ``titanium``                       |
    +----------------+-----------------------------------------------+

    Notes:

    - Zynq-7 uses an Artix-like or Kintex-like fabric depending on
      the die. ``family = "zynq7"`` is treated as its own bucket. If
      a source needs to distinguish PL fabric flavour, filter on the
      die number (``die matches "^xc7z0(10|15|20)$"`` for the
      Artix-like split, etc.).
    - Cyclone-10 GX/LP and Arria-10 GX/GT/SX collapse under one
      family value; use die-prefix filters if needed.

``die``
    Die-level part identifier with speed grade and package stripped.

    Examples: ``xc7a35t``, ``lfe5u_45f``, ``gw5a25``.

``speed``
    Speed grade, exactly as vendors write it (dash included when
    applicable).

    Examples: ``-1``, ``-2``, ``-6``, ``-8``.

    Kept as a string. Regex-comparable via ``matches``. Numeric
    comparison is not supported because vendors are not consistent
    (Xilinx uses ``-1``…``-3``, Lattice ECP5 uses ``-6``…``-8``,
    Diamond can label ECP5 grades ``LOW``/``STD``/``HIGH``).

``package``
    Package code as vendors write it.

    Examples: ``csg324``, ``cabga256``, ``fpbga484``.

``part``
    The raw part number string as the user provided it in the
    project file. Kept for backends that need to hand the full string
    to a vendor tool. Filtering directly on ``part`` is discouraged
    — filter on ``die`` or ``family`` instead.

Language
~~~~~~~~

``vhdl_std``
    VHDL revision.

    Values: ``1993``, ``2002``, ``2008``, ``2019``.

Emitting variables from backends
--------------------------------

Each pass contributes a subset of the canonical variables by
returning them from ``filter_vars()``. The planner unions every
pass's contribution into a single flat dictionary. A pass must not
emit a variable it does not own — for example, a PnR pass sets
``pnr_engine`` and re-emits the technology-stack variables (which
come from the project's ``target`` block), but must not set
``synthesis_engine``.

Legacy compatibility hook
-------------------------

Plugins may transform the merged variable dictionary before it is
handed to repository loaders. This lets an out-of-tree repository
consumer (for example, one whose Makefiles still expect legacy
variable names) synthesise those names from the canonical set
without any change to GBS itself.

.. code-block:: python

   class BasePlugin:
       def transform_filter_vars(
           self,
           filter_vars: dict[str, Any],
       ) -> dict[str, Any]:
           """Return additional filter variables to merge into the
           build's filter environment.

           Called once per build after per-pass filter_vars have been
           unioned. Every plugin's contribution is merged additively
           on top of the canonical set. Plugins should only *add*
           variables (typically legacy aliases); replacing canonical
           variables is discouraged.
           """
           return {}

The hook is invoked from the planner. Each plugin sees the merged
canonical dictionary and returns extra keys; the planner merges them
in with the canonical set taking precedence when a plugin tries to
overwrite an already-set key.
