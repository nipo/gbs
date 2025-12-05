GHDL Backend
============

The GHDL backend provides VHDL simulation using the `GHDL <https://ghdl.github.io/ghdl/>`_
open-source VHDL simulator.

Overview
--------

GHDL supports multiple code generation backends:

- **mcode**: Fast compilation, interpreted execution
- **gcc**: GCC-based native code generation
- **llvm**: LLVM-based native code generation
- **jit**: LLVM JIT for fast compile-run cycles

GBS automatically detects the GHDL backend type and adjusts its
compilation strategy accordingly.

Configuration
-------------

Backend configuration in project file:

.. code-block:: yaml

   backend_config:
     gbs.builtin.ghdl:
       vhdl_standard: "2008"
       output_dir: build
       ghdl_tool: ghdl:llvm

Configuration Options
~~~~~~~~~~~~~~~~~~~~~

``vhdl_standard``
    VHDL standard version. Options:

    - ``"87"`` - VHDL-1987
    - ``"93"`` or ``"93c"`` - VHDL-1993 (with common extensions)
    - ``"00"`` or ``"02"`` - VHDL-2000/2002
    - ``"08"`` or ``"2008"`` - VHDL-2008
    - ``"19"`` or ``"2019"`` - VHDL-2019

    Default: ``"93c"``

``ghdl_tool``
    Tool identifier for GHDL lookup in ``name:variant`` format.

    Default: ``"ghdl"``

Build artifacts are placed in ``gbs-build/<output_group_name>/``.
Use the ``outputs`` section to copy final files to desired locations.

Tool Configuration
------------------

Configure GHDL in ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: ghdl
       variant: system
       config:
         executable: ghdl

     - name: ghdl
       variant: llvm
       config:
         executable: /opt/ghdl-llvm/bin/ghdl

     - name: ghdl
       variant: jit
       config:
         executable: /usr/local/bin/ghdl

Output Types
------------

``ghdl-simulator``
    Simulator executable. Can be run directly to execute simulation.

``simulator``
    Generic simulator type (GHDL will match this as well).

Filter Variables
----------------

The GHDL backend contributes these filter variables for source selection:

``target-usage``
    Set to ``"simulation"``

``compiler``
    Set to ``"ghdl"``

``ghdl-backend``
    GHDL code generator type: ``"mcode"``, ``"gcc"``, ``"llvm"``, or ``"jit"``

``vhdl-version``
    VHDL standard as four-digit year: ``"1987"``, ``"1993"``, ``"2008"``, etc.

Use these in conditional source selection:

.. code-block:: yaml

   # In partition definition
   groups:
     - name: simulation_specific
       conditions:
         - expression: target-usage = "simulation"
           sources:
             - file_type: vhdl
               files:
                 - testbench.vhd
                 - sim_utils.vhd

Build Process
-------------

GHDL compilation follows these steps:

1. **Import** (``ghdl -i``): Import VHDL sources into library
2. **Analyze**: Parse and check VHDL code
3. **Elaborate** (``ghdl -m`` or ``ghdl -c -e``): Build design hierarchy
4. **Link**: Generate executable

For **mcode** backend:
    Uses ``ghdl -m`` for elaboration, produces interpreted executable

For **gcc/llvm** backends:
    Uses ``ghdl -c -e`` for compiled elaboration, produces native executable

Library Handling
----------------

GHDL processes libraries in dependency order:

1. Each library gets its own work directory: ``gbs-build/<output_group>/library_name/``
2. Library configuration is stored in ``.cf`` files
3. Inter-library dependencies are resolved using ``-P`` flags

Example Project
---------------

.. code-block:: yaml

   name: uart_test

   root:
     name: testbench
     deps:
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
           vhdl_standard: "2008"
           ghdl_tool: ghdl:llvm
       outputs:
         - type: ghdl-simulator
           path: tb_uart

This produces:

- Build artifacts in ``gbs-build/simulation/``
- Simulator executable copied to ``tb_uart``

Build and run:

.. code-block:: bash

   gbs project build
   ./tb_uart

Troubleshooting
---------------

**GHDL not found**
    Ensure GHDL is in PATH or configure explicit path in ``.gbs.yaml``

**Backend detection failed**
    Run ``ghdl --version`` to verify GHDL installation and check output
    contains "code generator" line

**Library dependency errors**
    Check partition dependencies are correctly specified. GBS resolves
    libraries in topological order.

**VHDL standard mismatch**
    Ensure ``vhdl_standard`` matches your source code requirements.
    Use ``"2008"`` for modern VHDL features.
