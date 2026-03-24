Overview
========

What is GBS?
------------

GBS (Gateware Build System) is a modern, Python-based build system designed
specifically for FPGA and ASIC gateware projects. It provides:

- **Dependency Resolution**: Automatic tracking and resolution of HDL file
  dependencies across libraries and partitions
- **Multi-Backend Support**: Pluggable backend system supporting GHDL, Gowin,
  Xilinx ISE, Xilinx Vivado, Intel Quartus, Yosys/nextpnr, and custom toolchains
- **Source Filtering**: Conditional source selection based on target platform,
  simulation vs. synthesis, and custom filter variables
- **Async Build Execution**: AsyncIO-based task system for parallel compilation
- **Plugin Architecture**: Extensible design allowing custom backends,
  repository loaders, and dispatchers

Project Goals
-------------

GBS aims to solve common pain points in gateware development:

1. **Unified Build Process**: One tool to handle simulation, synthesis, and
   implementation across different FPGA vendors and simulators.

2. **Library Management**: First-class support for organizing HDL code into
   reusable libraries with proper dependency tracking.

3. **Conditional Compilation**: Select different source files or dependencies
   based on target platform, simulation mode, or custom conditions.

4. **Reproducible Builds**: Declarative project configuration ensuring
   consistent builds across different machines and environments.

5. **Extensibility**: Plugin system allowing integration with any EDA tool
   or custom workflow.

Key Concepts
------------

Repository
    A collection of HDL libraries organized in a directory tree. Repositories
    define what source files exist and how they depend on each other.

Library
    A logical grouping of related HDL code (similar to VHDL libraries or
    Verilog packages). Libraries provide symbol scoping.

Partition
    A subset of a library's source files with its own dependencies. Partitions
    allow fine-grained dependency management within a library.

Project
    A build configuration that specifies what to build (top cell, outputs)
    and how to build it (backend configuration, filter variables).

Pass
    Planning metadata describing a file type transformation (e.g., VHDL to
    simulator executable). Passes are used by the planner to find build paths.

Dispatcher
    Execution engine that creates and manages build tasks. Each backend
    provides dispatchers for its specific tools.

Backend
    A toolchain integration (e.g., GHDL, Gowin, ISE) that contributes passes
    for build planning and dispatchers for execution.

Architecture Overview
---------------------

The build process follows these stages:

1. **Configuration Loading**: Load global, tree-level, and project configuration
2. **Repository Loading**: Parse repository definitions and enumerate libraries
3. **Build Planning**: Find transformation path from sources to desired outputs
4. **Source Resolution**: Enumerate source files using filter variables from plan
5. **Task Graph Creation**: Dispatchers create async tasks for compilation steps
6. **Parallel Execution**: AsyncIO executes tasks respecting dependencies

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

Supported Tools
---------------

Built-in backends:

- **GHDL**: VHDL simulation with mcode, GCC, LLVM, or JIT backends
- **Gowin**: Gowin FPGA synthesis and implementation
- **Xilinx ISE**: Legacy Xilinx synthesis for Spartan-6 and older devices
- **Intel Quartus**: Intel/Altera FPGA synthesis and implementation (Standard and Pro editions)
- **Xilinx Vivado**: Xilinx/AMD synthesis for 7-series and UltraScale devices
- **Yosys + nextpnr**: Open-source synthesis and place-and-route (iCE40, ECP5)

Additional tools can be integrated via the plugin system.
