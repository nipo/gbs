Repository System
=================

Repositories are the foundation of GBS source management. They organize
HDL code into libraries and partitions with dependency tracking and
conditional source selection.

Data Model Hierarchy
--------------------

.. code-block:: text

   Repository
   └── Library
       └── Partition
           └── ConditionalGroup
               └── FilterCondition
                   ├── sources: [SourceFile, ...]
                   ├── deps: ["lib.partition", ...]
                   └── groups: [ConditionalGroup, ...]

**Repository**
    Collection of libraries from a directory tree. Has a name, root path,
    and dictionary of libraries.

**Library**
    Symbol scoping unit (like VHDL libraries). Contains partitions that
    group related source files.

**Partition**
    Minimal dependency unit. A partition can depend on other partitions,
    and contains conditional groups that select sources.

**ConditionalGroup**
    Container for mutually exclusive conditions. First matching condition
    wins (switch/case semantics).

**FilterCondition**
    One branch of a conditional group. Specifies an expression, sources,
    dependencies, and nested groups.

**SourceFile**
    A single source file with path, file type, and optional variant.

Repository Definition
---------------------

Repositories are defined by YAML files in the repository tree. The exact
format depends on the repository loader plugin.

Standard GBS Format
~~~~~~~~~~~~~~~~~~~

The standard loader expects this structure::

   my_repo/
   ├── library1/
   │   ├── partition1.gbs.yaml
   │   └── partition2.gbs.yaml
   └── library2/
       └── core.gbs.yaml

Each partition YAML file defines a partition:

.. code-block:: yaml

   # library1/partition1.gbs.yaml
   name: partition1

   deps:
     - library2.core        # Depends on library2.core partition

   sources:
     - file_type: vhdl
       files:
         - types.vhd
         - utils.vhd

Libraries
---------

Libraries provide symbol name scoping, similar to VHDL libraries. All
partitions within a library share the same library name in the compiled
output.

Library names are derived from directory names in the repository.

Partitions
----------

Partitions are the fundamental unit of dependency management. Key properties:

- A partition belongs to exactly one library
- Partitions can depend on partitions from any library
- Source files are always associated with a partition
- Dependencies are evaluated at build time using filter variables

Simple Partition Example
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   # nsl_data/text.gbs.yaml
   name: text

   deps:
     - nsl_data.bytestream
     - nsl_data.crc

   sources:
     - file_type: vhdl
       files:
         - text_pkg.vhd
         - text_encoder.vhd
         - text_decoder.vhd

Conditional Source Selection
----------------------------

Partitions use conditional groups to select different sources or
dependencies based on filter variables.

Filter Expression Syntax
~~~~~~~~~~~~~~~~~~~~~~~~

Filter expressions use this syntax:

.. code-block:: text

   variable = "value"        # Exact match (string)
   variable = 1              # Exact match (integer)
   variable                  # Variable is truthy
   NOT variable              # Variable is falsy
   default                   # Always matches (catch-all)

Examples:

.. code-block:: yaml

   # Select by vendor
   expression: vendor = "xilinx"

   # Select by target usage
   expression: target-usage = "simulation"

   # Default fallback
   expression: default

Conditional Dependencies
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: clock

   # Base dependencies always included
   deps:
     - nsl_data.bytestream

   # Conditional groups
   groups:
     - name: target_specific
       conditions:
         - expression: vendor = "gowin"
           deps:
             - nsl_hwdep.gowin_clock

         - expression: vendor = "xilinx"
           deps:
             - nsl_hwdep.xilinx_clock

         - expression: target-usage = "simulation"
           deps:
             - nsl_hwdep.generic_clock

         - expression: default
           deps: []

Conditional Sources
~~~~~~~~~~~~~~~~~~~

.. code-block:: yaml

   name: fifo

   deps:
     - nsl_data.bytestream

   groups:
     - name: implementation
       conditions:
         - expression: target-usage = "simulation"
           sources:
             - file_type: vhdl
               files:
                 - fifo_sim.vhd

         - expression: vendor = "xilinx"
           sources:
             - file_type: vhdl
               files:
                 - fifo_xilinx.vhd

         - expression: default
           sources:
             - file_type: vhdl
               files:
                 - fifo_generic.vhd

Nested Conditional Groups
~~~~~~~~~~~~~~~~~~~~~~~~~

Groups can be nested for complex selection logic:

.. code-block:: yaml

   groups:
     - name: vendor_specific
       conditions:
         - expression: vendor = "xilinx"
           sources:
             - file_type: vhdl
               files:
                 - xilinx_common.vhd
           groups:
             - name: xilinx_family
               conditions:
                 - expression: family = "spartan6"
                   sources:
                     - file_type: vhdl
                       files:
                         - spartan6_impl.vhd

                 - expression: family = "artix7"
                   sources:
                     - file_type: vhdl
                       files:
                         - artix7_impl.vhd

Filter Variables
----------------

Filter variables control conditional selection. They come from two sources:

1. **OutputGroup Configuration**: User-specified in project file

   .. code-block:: yaml

      output:
        - name: synthesis
          filter_vars:
            vendor: xilinx
            family: spartan6

2. **Passes**: Contributed during build planning

   For example, the GHDL simulation pass adds:

   .. code-block:: python

      def filter_vars(self):
          return {
              "target-usage": "simulation",
              "compiler": "ghdl",
          }

Variables from both sources are merged, with OutputGroup taking precedence.

Dependency Resolution
---------------------

The dependency resolver performs topological sorting of partitions:

1. Start from root partition (project's top cell)
2. Recursively collect dependencies using filter variables
3. Order partitions so dependencies come before dependents
4. Return SourceFileSet with ordered libraries, partitions, and files

Resolution Algorithm
~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   function resolve(root_partition, filter_vars):
       visited = {}
       order = []

       function visit(lib, partition):
           if (lib, partition) in visited:
               return
           visited[(lib, partition)] = True

           # Evaluate conditional groups with filter_vars
           deps = evaluate_deps(partition, filter_vars)
           sources = evaluate_sources(partition, filter_vars)

           # Visit dependencies first
           for dep in deps:
               visit(dep.library, dep.partition)

           # Add this partition
           order.append((lib, partition, sources))

       visit(root_partition.library, root_partition)
       return order

The result is used to populate a BuildFileSet for execution.

File Types
----------

Source files have a ``file_type`` field indicating their format:

Common file types:

- ``vhdl`` - VHDL source files
- ``verilog`` - Verilog source files
- ``systemverilog`` - SystemVerilog source files
- ``gowin-cst`` - Gowin constraint files
- ``xilinx-ucf`` - Xilinx UCF constraint files

File types can have variants (e.g., VHDL-2008):

.. code-block:: yaml

   sources:
     - file_type: vhdl
       variant: "2008"
       files:
         - modern_code.vhd

Custom Repository Loaders
-------------------------

GBS supports custom repository loaders through the plugin system. Loaders
implement:

.. code-block:: python

   def load(path: Path) -> Repository:
       """Load repository from path"""
       ...

See :doc:`plugins` for details on creating custom loaders.
