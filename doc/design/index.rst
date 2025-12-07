Architecture Overview
=====================

GBS follows a layered architecture with clear separation between:

- **Configuration**: Loading and merging settings from multiple sources
- **Repository**: Data models for libraries, partitions, and source files
- **Planning**: Finding transformation paths from sources to outputs
- **Execution**: AsyncIO-based parallel task execution

.. toctree::
   :maxdepth: 2

   configuration
   repositories
   build_system
   plugins
   suite

Design Principles
-----------------

**Declarative Configuration**
    Projects declare *what* to build, not *how*. The planner finds the
    transformation path automatically.

**Type-Based Planning**
    Build planning works by matching file types. Passes declare input/output
    types; the planner chains them together.

**Pluggable Backends**
    Each toolchain (GHDL, Gowin, ISE) is a plugin contributing passes
    for planning and dispatchers for execution.

**AsyncIO Execution**
    The entire build system is async-native. Tasks are asyncio Futures,
    dependencies resolve naturally through awaits.

High-Level Flow
---------------

.. code-block:: text

   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │   Config    │────▶│  Planning   │────▶│  Execution  │
   │   Loading   │     │             │     │             │
   └─────────────┘     └─────────────┘     └─────────────┘
         │                   │                   │
         ▼                   ▼                   ▼
   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
   │ Global cfg  │     │   Passes    │     │   Tasks     │
   │ Tree cfg    │     │   Backends  │     │  Resources  │
   │ Project cfg │     │  Dispatchers│     │  Parallel   │
   └─────────────┘     └─────────────┘     └─────────────┘

1. **Configuration Loading**: Parse YAML from global, tree, and project files
2. **Repository Loading**: Enumerate libraries and partitions from repositories
3. **Build Planning**: Find pass chain from source types to desired output types
4. **Source Resolution**: Apply filter variables to resolve partition dependencies
5. **Task Graph Creation**: Dispatchers create tasks with input/output resources
6. **Parallel Execution**: AsyncIO executes tasks respecting dependencies

Component Hierarchy
-------------------

The build system follows a hierarchical structure where each level
spawns the next:

.. code-block:: text

   Backend
   └── Pass (planning metadata)
       └── Dispatcher (execution engine)
           └── Task (work unit)
               └── Resource (file)

**Backend** (:doc:`build_system`)
    Top-level plugin that contributes passes for build planning.
    Backends represent toolchains like GHDL, Gowin, or Xilinx ISE.

**Pass** (:doc:`build_system`)
    Planning metadata declaring input/output file types and filter variables.
    Passes do NOT execute anything - they're purely for planning.

**Dispatcher** (:doc:`build_system`)
    Execution engine that processes a BuildFileSet and creates Tasks.
    Dispatchers iterate until the fileset stabilizes.

**Task** (:doc:`build_system`)
    Actual work unit that awaits inputs, executes a tool, and produces outputs.
    Tasks are asyncio Futures with dependency tracking.

**Resource** (:doc:`build_system`)
    Represents a file or virtual data. Awaiting a Resource waits for it
    to become available (after its producing Task completes).

Key Abstractions
----------------

**Repository Data Model** (:doc:`repositories`)

.. code-block:: text

   Repository → Library → Partition → ConditionalGroup → FilterCondition → SourceFile

Repositories contain libraries, which contain partitions. Partitions
define source files and dependencies through conditional groups that
evaluate based on filter variables.

**Build Planning** (:doc:`build_system`)

The planner works backwards from desired outputs:

1. Query backends for passes that produce the desired output types
2. Check if pass inputs are satisfied by available sources
3. If not, recursively find passes that produce the missing types
4. Return the shortest pass chain (iterative deepening)

**Filter Variables**

Filter variables control source selection. They come from:

1. OutputGroup configuration (user-specified)
2. Passes (e.g., ``target-usage: simulation``)

Combined variables are used to evaluate conditional groups in partitions,
selecting the appropriate sources and dependencies.
