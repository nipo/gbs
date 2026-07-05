openxc7 Backend
===============

The openxc7 backend converts nextpnr-xilinx FASM output into a Xilinx
Series-7 ``.bit`` bitstream by chaining ``fasm2frames`` (FASM to
binary frame deltas) and ``xc7frames2bit`` (frames to Xilinx
bitstream). Both tools ship in the openxc7 apio package.

Overview
--------

This backend is the final stage of the open-source Series-7 flow:

.. code-block:: text

   VHDL/Verilog → yosys (synth_xilinx) → JSON netlist
                → nextpnr-xilinx (--chipdb + --xdc) → FASM
                → fasm2frames  (--db-root + --part) → frames (.frm)
                → xc7frames2bit (--part_file part.yaml) → bitstream (.bit)

It only handles the last two steps; the JSON→FASM step lives in the
:doc:`nextpnr` backend's ``xilinx`` target.

Supported Inputs
----------------

- ``nextpnr-fasm``: FASM file produced by ``nextpnr-xilinx``

Supported Outputs
-----------------

- ``xilinx-bitstream``: Xilinx ``.bit`` bitstream ready to flash

Configuration
-------------

The backend uses two tools; both are populated automatically by the
apio provider when openxc7 is installed under ``~/.apio/packages``:

.. code-block:: yaml

   tools:
     - name: fasm2frames
       config:
         executable: ~/.apio/packages/openxc7/bin/fasm2frames
         prjxray_db_root: ~/.apio/packages/openxc7/share/nextpnr/external/prjxray-db
     - name: xc7frames2bit
       config:
         executable: ~/.apio/packages/openxc7/bin/xc7frames2bit
         prjxray_db_root: ~/.apio/packages/openxc7/share/nextpnr/external/prjxray-db

The dispatcher joins ``prjxray_db_root`` with the target part's
family (parsed from the part name, e.g. ``artix7``) and the
``<chipdb>-<speed>`` subdirectory to reach both the family-level
``db-root`` (needed by ``fasm2frames``) and the per-part
``part.yaml`` (needed by ``xc7frames2bit``). Note that
``xc7frames2bit`` reads only the YAML flavor of the prjxray part
metadata; ``part.json`` sits next to it but is not what this tool
consumes.

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

In the project file, add ``gbs.builtin.openxc7`` to the output
group's ``backend_config`` and request an ``xilinx-bitstream``:

.. code-block:: yaml

   output:
     - name: bitstream
       topcell: boundary
       target:
         part: xc7a35t-1cpg236   # vivado-style: name-speedpackage
       backend_config:
         gbs.builtin.yosys: {}
         gbs.builtin.nextpnr: {}
         gbs.builtin.openxc7: {}
       outputs:
         - type: xilinx-bitstream
           path: blink.bit

Filter Variables
----------------

- ``target-usage=bitstream``
- ``vendor=xilinx``, ``hwdep=xilinx``
- ``target_part``, ``target_part_name``, ``target_speed``,
  ``target_package`` parsed from the part name (same shape as the
  yosys and nextpnr Xilinx passes and the vivado backend).

Part Coverage
-------------

The openxc7 apio package ships a limited set of pre-built chipdbs and
prjxray part directories. As of writing, the ``.bin`` in
``chipdb/`` is generated only for a handful of the most common
Series-7 parts (e.g. ``xc7a35tcpg236`` — the Digilent Basys 3);
additional parts have to be assembled with ``bbasm`` from the
metadata under ``share/nextpnr/external/nextpnr-xilinx-meta/`` and
``share/nextpnr/external/prjxray-db/``.

Use ``gbs openxc7 chipdb build <part>`` to generate one on demand:

.. code-block:: bash

   gbs openxc7 chipdb build xc7a35t-1cpg236   # Basys 3
   gbs openxc7 chipdb build xc7a35t-1csg324   # Arty A7-35T

The command wraps ``bbaexport.py`` + ``bbasm`` and drops the ``.bin``
into ``<install>/chipdb/`` where nextpnr-xilinx picks it up
automatically on the next build. Expect one to a few minutes of CPU
per part; the intermediate ``.bba`` text file is ~250 MB and is
removed unless ``--keep-bba`` is passed. See :doc:`../cli` for the
full option list.

Example
-------

See :file:`example/openxc7/artix7/blink/` in the source tree for a
complete Basys 3 blink project driving the yosys → nextpnr-xilinx →
fasm2frames → xc7frames2bit pipeline end-to-end.

Requirements
------------

- ``fasm2frames`` and ``xc7frames2bit`` from openxc7
- ``nextpnr-xilinx`` (openxc7 fork) for the preceding PnR stage
- ``yosys`` with ``synth_xilinx`` for synthesis
- A chipdb ``.bin`` file matching the target part, and a
  ``prjxray-db/<family>/<part>/part.yaml`` for that part

Installing openxc7 through apio and enabling the ``apio`` toolchain
in ``.gbs.yaml`` fills all four tool configs automatically.

See Also
--------

- openxc7 project: https://github.com/openXC7
- nextpnr-xilinx: https://github.com/openXC7/nextpnr-xilinx
- Project X-Ray: https://github.com/f4pga/prjxray
- :doc:`yosys` - Synthesis for ``synth_xilinx``
- :doc:`nextpnr` - Place-and-route (``xilinx`` target)
