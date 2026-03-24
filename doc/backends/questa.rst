Questa Backend
==============

The Questa backend provides VHDL and Verilog simulation using Siemens QuestaSim and Intel ModelSim.

Overview
--------

QuestaSim (formerly ModelSim) is an industry-standard HDL simulator supporting VHDL and Verilog. The GBS backend generates TCL batch scripts for compilation and simulation, along with shell script wrappers for easy execution.

Supported Inputs
----------------

- ``vhdl``: VHDL source files
- ``verilog``: Verilog source files

Supported Outputs
-----------------

- ``questa-simulator``: Shell script wrapper for running the simulation
- ``questa-batch-script``: TCL batch script for compilation and simulation

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: questa
       config:
         executable: vsim  # Path to vsim binary

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In project file:

.. code-block:: yaml

   backend_config:
     questa:
       vhdl_standard: "2008"  # VHDL standard: 1993, 2008, 2019
       tool: questa           # Tool identifier for lookup

Example Project
---------------

.. code-block:: yaml

   name: my_simulation
   root_library_name: work

   output_groups:
     - name: simulation
       topcell: tb_top
       outputs:
         - type: questa-simulator
           path: build/run_sim.sh
         - type: questa-batch-script
           path: build/batch.tcl

   root_partition_template:
     dependencies:
       - library.partition

Simulation Workflow
-------------------

The Questa backend generates two files:

1. **TCL Batch Script** (``questa-batch-script``):
   - Creates libraries
   - Compiles VHDL/Verilog sources in dependency order
   - Configures simulation parameters
   - Runs the simulation

2. **Shell Script Wrapper** (``questa-simulator``):
   - Sets up environment
   - Invokes ``vsim`` with the batch script
   - Provides a simple executable interface

To run the simulation:

.. code-block:: bash

   # After building with GBS
   ./build/run_sim.sh

   # Or run the batch script directly
   vsim -c -do build/batch.tcl

Filter Variables
----------------

The Questa backend contributes the following filter variables for conditional source selection:

- ``target-usage``: Set to ``simulation``
- ``compiler``: Set to ``questa``
- ``vhdl-version``: VHDL standard from configuration

VHDL Standards
--------------

Supported VHDL standards:

- ``1993``: VHDL-1993 (IEEE Std 1076-1993)
- ``2008``: VHDL-2008 (IEEE Std 1076-2008)
- ``2019``: VHDL-2019 (IEEE Std 1076-2019)

Specify the standard in backend configuration:

.. code-block:: yaml

   backend_config:
     questa:
       vhdl_standard: "2008"

Requirements
------------

- QuestaSim or ModelSim installed
- ``vsim`` command in PATH or configured in tools
- Valid license for commercial versions
- VHDL/Verilog source files

See Also
--------

- QuestaSim documentation: https://www.intel.com/content/www/us/en/software/programmable/quartus-prime/model-sim.html
- :doc:`ghdl` - Open-source VHDL simulator
- :doc:`nvc` - Alternative VHDL simulator
