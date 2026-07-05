Backends
========

GBS supports multiple FPGA and ASIC toolchains through a pluggable backend system.

Available Backends
------------------

Simulation
~~~~~~~~~~

.. toctree::
   :maxdepth: 1

   ghdl
   nvc
   questa

FPGA Synthesis
~~~~~~~~~~~~~~

.. toctree::
   :maxdepth: 1

   diamond
   gowin
   ise
   quartus
   vivado
   yosys
   nextpnr

IP Packaging
~~~~~~~~~~~~

.. toctree::
   :maxdepth: 1

   vivado_ip

Utilities
~~~~~~~~~

.. toctree::
   :maxdepth: 1

   icepack
   openxc7
   compress
   output_copy

Backend Selection
-----------------

Backends are automatically selected based on the output types requested in your
project file. The build planner chooses appropriate backends to convert from
source files (VHDL, Verilog, etc.) to the desired outputs (bitstreams, netlists, etc.).

Configuration
-------------

Backends can be configured in your ``.gbs.yaml`` or project file:

.. code-block:: yaml

   tools:
     - name: ghdl
       variant: llvm
       config:
         executable: /usr/local/bin/ghdl

     - name: vivado
       config:
         executable: /opt/Xilinx/Vivado/2023.1/bin/vivado

Backend-Specific Configuration
-------------------------------

Each backend may support additional configuration options in the project file's
``backend_config`` section. See individual backend documentation for details.

Example:

.. code-block:: yaml

   backend_config:
     ghdl:
       vhdl_std: "2008"
       optimization: "-O3"

     vivado:
       strategy: "Performance_Explore"
