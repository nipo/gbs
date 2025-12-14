NVC Backend
===========

The NVC backend provides VHDL simulation using the NVC VHDL compiler and simulator.

Overview
--------

NVC is an open-source VHDL compiler and simulator supporting VHDL-1993, VHDL-2000,
VHDL-2008, and VHDL-2019. It provides fast simulation with good IEEE library support.

Supported Outputs
-----------------

- ``nvc-executable``: Compiled simulation executable

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: nvc
       config:
         executable: nvc  # Path to nvc binary

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In project file:

.. code-block:: yaml

   backend_config:
     nvc:
       vhdl_std: "2008"  # VHDL standard: 1993, 2000, 2008, 2019
       optimization: "-O2"  # Optimization level

Example Project
---------------

.. code-block:: yaml

   name: my_simulation
   root_library_name: work

   output_groups:
     - name: simulation
       topcell: tb_top
       outputs:
         - type: nvc-executable
           path: build/simulator

   root_partition_template:
     dependencies:
       - library.partition

Requirements
------------

- NVC installed and in PATH
- VHDL source files

See Also
--------

- NVC documentation: https://www.nickg.me.uk/nvc/
- :doc:`ghdl` - Alternative VHDL simulator
