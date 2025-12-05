Getting Started
===============

This guide walks you through installing GBS and building your first project.

Installation
------------

GBS requires Python 3.10 or later. Install it in development mode:

.. code-block:: bash

   # Clone the repository
   git clone https://github.com/nipo/gbs.git
   cd gbs

   # Install with development dependencies
   pip install -e ".[dev]"

   # Verify installation
   gbs --help

Basic Concepts
--------------

Before building your first project, understand these key concepts:

**Repository**
    A collection of HDL libraries organized in a directory tree. Repositories
    define what source files exist and how they depend on each other.

**Library**
    A logical grouping of related HDL code providing symbol scoping.

**Partition**
    A subset of a library's source files with dependencies on other partitions.
    Partitions enable fine-grained dependency management.

**Project**
    A build configuration specifying what to build (top cell, outputs)
    and how to build it (backend configuration, filter variables).

Your First Project
------------------

Create a simple VHDL project with a testbench that prints "Hello World".

1. Create the directory structure:

.. code-block:: bash

   mkdir -p hello_world
   cd hello_world

2. Create ``top.vhd``:

.. code-block:: vhdl

   library ieee;
   use ieee.std_logic_1164.all;

   entity top is
   end entity;

   architecture sim of top is
   begin
       process
       begin
           report "Hello World!";
           wait;
       end process;
   end architecture;

3. Create ``project.gbs.yaml``:

.. code-block:: yaml

   name: hello_world

   root:
     name: top
     sources:
       - file_type: vhdl
         files:
           - top.vhd

   output:
     - name: simulation
       topcell: top
       filter_vars: {}
       backend_config:
         gbs.builtin.ghdl:
           vhdl_standard: "93c"
       outputs:
         - type: ghdl-simulator
           path: build/top

4. Build and run:

.. code-block:: bash

   gbs project build project.gbs.yaml

   # Run the simulation
   ./build/top

   # Output: Hello World!

Project Structure
-----------------

A typical GBS project has this structure::

   my_project/
   ├── project.gbs.yaml      # Project configuration
   ├── .gbs.yaml             # Optional tree-level config (tools, profiles)
   ├── src/
   │   ├── top.vhd           # Top-level design
   │   └── components/       # Submodules
   ├── testbench/
   │   └── tb_top.vhd        # Testbenches
   └── constraints/
       └── board.ucf         # FPGA constraints

Using External Libraries
------------------------

GBS supports external library repositories. Reference them in your tree
configuration:

1. Create ``.gbs.yaml`` in your project root:

.. code-block:: yaml

   repositories:
     - path: /path/to/external/lib
       loader: gbs.plugin.nsl.tree

2. Reference library partitions in your project:

.. code-block:: yaml

   root:
     name: top
     deps:
       - nsl_data.text         # External library partition
       - nsl_simulation.assertions
     sources:
       - file_type: vhdl
         files:
           - top.vhd

Configuration Hierarchy
-----------------------

GBS loads configuration from multiple levels (later overrides earlier):

1. **Global**: ``~/.config/gbs.yaml`` - User-wide settings
2. **Tree**: ``.gbs.yaml`` in parent directories - Shared team/project settings
3. **Project**: ``*.gbs.yaml`` - Project-specific settings

This allows sharing tool paths and profiles across projects while
customizing individual builds.

Next Steps
----------

- :doc:`project_file` - Detailed project YAML documentation
- :doc:`cli` - CLI command reference
- :doc:`design/index` - Architecture deep dive
- :doc:`backends/ghdl` - GHDL backend configuration
- :doc:`backends/gowin` - Gowin backend configuration
- :doc:`backends/ise` - Xilinx ISE backend configuration
