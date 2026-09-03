Vivado IP Packaging Backend
===========================

The Vivado IP packaging backend creates IP-XACT packages from HDL sources using Vivado's ``ipx::package_project`` flow.

Overview
--------

Plugin name: ``gbs.builtin.vivado-ip``

This backend packages HDL designs as reusable Vivado IP components. The packaged IP can be used in Vivado block designs or distributed as a zip archive for integration into other projects.

Supported Inputs
----------------

- ``vhdl``: VHDL source files
- ``verilog``: Verilog source files
- ``vivado-bus-definition``: Custom bus interface XML definitions
- ``vivado-bus-zip``: Archive of custom bus interface XML definitions (see :doc:`vivado_bus`)
- ``vivado-ip-customization-tcl``: Post-packaging TCL scripts for IP customization
- ``xilinx-xdc``: Constraint files to include in the IP package

Supported Outputs
-----------------

- ``vivado-ip-zip``: Packaged IP as a zip archive
- ``vivado-ip-dir``: Packaged IP as a directory

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

The Vivado IP backend uses the same Vivado tool as the synthesis backend.
In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: vivado
       config:
         path: /opt/Xilinx/Vivado/2023.1

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

IP metadata is specified in the backend configuration:

.. code-block:: yaml

   backend_config:
     gbs.builtin.vivado-ip:
       tool: vivado               # Tool identifier for lookup
       vhdl_standard: "2008"
       vendor: com.example
       library: ip
       name: my_ip_core
       version: "1.0"
       taxonomy: /UserIP
       display_name: My IP Core
       description: A custom IP core

Output Group Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~

Target device is specified in the output group:

.. code-block:: yaml

   output_groups:
     - name: package
       topcell: my_ip_top
       target:
         part: xc7a35tcsg324-1  # Xilinx part number
       outputs:
         - type: vivado-ip-zip
           path: build/my_ip_core_1.0.zip

Example Project
---------------

.. code-block:: yaml

   name: my_ip_core
   root_library_name: work

   output_groups:
     - name: package
       topcell: ip_top
       target:
         part: xc7a35tcsg324-1
       outputs:
         - type: vivado-ip-zip
           path: my_ip_core_1.0.zip
         - type: vivado-ip-dir
           path: ip_repo/my_ip_core_1.0

   backend_config:
     gbs.builtin.vivado-ip:
       vendor: com.example
       library: ip
       name: my_ip_core
       version: "1.0"
       taxonomy: /UserIP
       display_name: My IP Core
       description: A reusable IP core

   root_partition_template:
     dependencies:
       - library.partition

Build:

.. code-block:: bash

   gbs project build

The packaged IP zip can then be added to a Vivado project's IP repository
or used as a ``vivado-ip-zip`` input in another GBS project (which triggers
project mode in the Vivado synthesis backend).

Filter Variables
----------------

The Vivado IP packaging backend contributes the following filter variables:

- ``target-usage``: Set to ``synthesis``
- ``vendor``: Set to ``xilinx``
- ``hwdep``: Set to ``xilinx``
- ``vhdl-version``: VHDL standard from configuration

Requirements
------------

- Vivado Design Suite installed
- Valid Vivado license
- HDL source files for the IP

See Also
--------

- :doc:`vivado` - Vivado synthesis backend (consumes ``vivado-ip-zip``)
- Vivado IP packaging documentation: https://docs.amd.com/r/en-US/ug1118-vivado-creating-packaging-custom-ip
