yavs Backend
============

The yavs backend analyzes, elaborates and runs VHDL testbenches with the
yavs simulator.

Overview
--------

Each library is analyzed into its own directory with ``yavs analyze``,
mapping the libraries it depends on with ``--map``. The top level is then
checked with ``yavs elab``, so elaboration errors fail the build, and a
``simulator.sh`` wrapper running ``yavs run`` on the analyzed libraries is
written. The run pass executes that wrapper.

yavs supports VHDL-1993, VHDL-2008 and VHDL-2019.

VHPIDIRECT
----------

Sources of type ``vhpidirect-c`` are compiled into shared libraries
with the C compiler, honouring the source's ``include_dirs``. Each
library is given to both ``yavs elab`` and ``yavs run`` as
``--load=LIB:args=fat-pointer``, the argument layout GHDL uses, so the
same C sources serve both simulators.

Supported Outputs
-----------------

- ``simulator`` / ``yavs-simulator``: wrapper script running the testbench
- ``simulation-log``: messages of the run
- ``simulation-success``: marker created when the run succeeds
- ``waveform-fst``: FST waveform of the whole design

Filter Variables
----------------

``purpose = "simulation"``, ``vhdl_frontend = "yavs"``,
``simulation_engine = "yavs"`` and ``vhdl_std``.

Configuration
-------------

Tool Configuration
~~~~~~~~~~~~~~~~~~

In ``.gbs.yaml``:

.. code-block:: yaml

   tools:
     - name: yavs
       config:
         executable: ~/yavs/target/release/yavs
         analyze_args: [--relaxed]    # extra yavs analyze arguments
         run_args: [--exit-severity=error]  # extra yavs run arguments

Backend Configuration
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   backend_config:
     gbs.builtin.yavs:
       vhdl_standard: "2008"         # 1993 (default), 2008 or 2019

``success_regex`` and ``max_simulation_time`` (passed as ``--stop-time``)
come from the output group's ``simulation`` block, see
:doc:`../project_file`.

Example Project
---------------

.. code-block:: yaml

   name: my_simulation

   root:
     name: top
     deps:
       - library.partition
     sources:
       - file_type: vhdl
         files:
           - src/tb_top.vhd

   output:
     - name: simulation
       topcell: tb_top
       outputs:
         - type: simulation-log
           path: simulation.log

See Also
--------

- :doc:`ghdl` - Alternative VHDL simulator
- :doc:`nvc` - Alternative VHDL simulator
