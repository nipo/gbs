# GBS: Gateware Build System

A build system for gateware projects targeting FPGAs.

## Overview

GBS provides a flexible, extensible build infrastructure for hardware
description language (HDL) projects. It supports:

- **Configuration System**: User- and tree-level configs describing tools, toolchains, and repositories
- **Multiple repositories and libraries**: Organize code into logical units
- **Dependency management**: Topological sorting ensures correct build order
- **Conditional source filtering**: Target-specific code selection
- **Pluggable backend architecture**: Support for different toolchains, can add support for custom backends as plugins
- **Tool management**: Multiple installations with variant support
- **Asyncio-based execution**: Parallel builds when possible

## Installation

```bash
pip install .
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

### Basic Build

Commands auto-discover a single `*.gbs.yaml` in the current directory:

```bash
# Build every output group declared in the project
gbs project build

# Build only the named output group(s)
gbs project build simulation

# Clean build artifacts
gbs project clean

# Show project configuration and planned build graph
gbs project show
```

To point at a specific project file when several coexist, use
`-f`: `gbs project -f other.gbs.yaml build`. To run from elsewhere
in the tree, use `-C`: `gbs -C path/to/project project build`.

### Configuring Tools

Create `~/.config/gbs.yaml` and register the tools you have
installed. Each entry pins one binary (or install root, for
IDE-style tools) under a name that backends resolve at plan time:

```yaml
tools:
  - name: ghdl
    variant: system
    config:
      executable: /usr/local/bin/ghdl
  - name: yosys
    variant: system
    config:
      executable: /opt/oss-cad-suite/bin/yosys
      ghdl_plugin: /opt/oss-cad-suite/share/yosys/plugins/ghdl.so
```

The `toolchains:` key can bulk-register groups of related tools —
apio distributions of oss-cad-suite and openxc7 have a provider
built in. See `doc/design/configuration.rst` for the full config
reference, and `example/dot_config_gbs.yaml` for a fuller sample.

### Writing a Project

A minimal project declares its top-level partition (`root:`),
its dependencies, and one or more output groups:

```yaml
name: hello

root:
  name: top
  deps:
    - nsl_data.text
    - nsl_simulation.assertions
  sources:
    - file_type: vhdl
      files:
        - top.vhd

output:
  - name: simulation
    topcell: top
    outputs:
      - type: simulator
        path: build/top
```

Terminal output types are the canonical shared names —
`bitstream`, `simulator`, `synthesis-report`, `pnr-report`. The
planner picks whichever installed backend can produce them, so a
single `type: bitstream` project can build with Vivado, openxc7,
Gowin IDE, ISE, Quartus or Diamond depending on what's on the
machine.

Working projects for every builtin backend live under `example/`:
`example/ghdl/hello/` for a minimal simulation, `example/openxc7/`
and `example/vivado/` for Xilinx synthesis flows,
`example/gowin/`, `example/diamond/`, `example/ise/`,
`example/altera/`, `example/yosys/` for other backends.

## Documentation

See the `doc/` directory for detailed documentation:

- [Overview](doc/overview.rst) — Project goals and architecture
- [Getting started](doc/getting_started.rst) — Walkthrough for a new project
- [Command-line reference](doc/cli.rst) — Every subcommand and its options
- [Project file reference](doc/project_file.rst) — `project.gbs.yaml` schema
- [Configuration system](doc/design/configuration.rst) — `~/.config/gbs.yaml` and `.gbs.yaml`
- [Filter variables](doc/design/filter_vars.rst) — Canonical vocabulary for source selection
- [Build system design](doc/design/build_system.rst) — Backends, passes, dispatchers, and the alias registry
- [Repository system](doc/design/repositories.rst) — Libraries, partitions, and conditional groups

## Requirements

- Python 3.13+

## Development Status

YMMV

## License

MIT
