Vivado Bus Definition Backend
=============================

The Vivado bus definition backend generates IP-XACT 1685-2009 bus
interface definitions from YAML descriptions, targeting the subset of
IP-XACT that Vivado's IP integrator consumes.

Overview
--------

Plugin name: ``gbs.builtin.vivado-bus``

Each YAML description produces a busDefinition (``<name>.xml``) and an
abstractionDefinition (``<name>_rtl.xml``).  Definitions can be bundled
into a repository archive or directory, suitable as a Vivado
``ip_repo_paths`` entry, or fed directly into the Vivado IP packaging
backend.  Everything runs in pure Python; no Vivado installation is
needed to generate bus definitions.

The YAML file name must match the ``name`` declared inside it, so that
output file names are known before the file is read.

Supported Inputs
----------------

- ``vivado-bus-yaml``: YAML bus descriptions
- ``vivado-bus-definition``: Generated (or hand-written) IP-XACT XML files

Supported Outputs
-----------------

- ``vivado-bus-definition``: The XML file pair for each described bus
- ``vivado-bus-zip``: All bus definitions bundled as a zip archive
- ``vivado-bus-dir``: All bus definitions collected into a directory

The :doc:`vivado_ip` backend accepts both ``vivado-bus-definition`` and
``vivado-bus-zip`` as inputs, so an IP project can either list the YAML
descriptions as sources (the transpile pass is planned in
automatically) or reference a prebuilt archive.

YAML Format
-----------

.. code-block:: yaml

   library: interface           # required, IP-XACT library
   name: framed                 # required, must match the file name
   vendor: nsl                  # default "nsl"
   version: "1.0"               # default "1.0"
   description: NSL Framed bus  # optional
   display_name: Framed bus     # optional, Xilinx vendor extension

   direct_connection: false     # defaults shown
   addressable: false
   max_masters: 1
   max_slaves: 1

   roles:                       # optional, see below
     broadcast:
       base: m2s
       slave:
         presence: optional

   ports:                       # required, order preserved
     req_valid:
       role: m2s                # required
       description: Request path valid
     req_data:
       role: m2s
       width: 8                 # optional, emitted on both sides
       qualifier: data          # optional: clock, data, reset, address
     req_ready:
       role: s2m
     oe:
       role: m2s
       default: 0               # optional value for unconnected inputs
     dio_o:
       role: m2s
       tristate:                # optional Xilinx tristate mapping
         role: out              # tristate, in or out
         group: dio

Roles
~~~~~

A role gives, for the master and slave sides of the bus, the port
presence and direction.  Two roles are built in:

- ``m2s``: driven by the master, required on both sides
- ``s2m``: driven by the slave, required on both sides

Additional roles are declared under ``roles:``.  Each side takes
``presence`` (``required``, ``optional`` or ``illegal``) and an
optional ``direction`` (``in`` or ``out``; omitted, no direction
element is emitted).  A side that is not declared is left out of the
port entirely.  ``base:`` derives a role from another one, overriding
only the given attributes.

Project Example
---------------

.. code-block:: yaml

   name: bus_package

   root:
     name: top
     sources:
       - file_type: vivado-bus-yaml
         files:
           - bus/handshake.yaml

   output:
     - name: bus_definitions
       topcell: handshake
       outputs:
         - type: vivado-bus-zip
           path: example_buses.zip

See ``example/vivado/bus_package/`` for the complete example.

Stand-alone Conversion
----------------------

The generator is also exposed as a CLI converter, usable without a
project:

.. code-block:: bash

   gbs convert vivado-bus --output-dir interfaces/ framed.yaml spi.yaml

   # Verify generated files are up to date (exit code 1 when stale)
   gbs convert vivado-bus --check --output-dir interfaces/ *.yaml
