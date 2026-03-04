# GBS: Gateware Build System

A build system for gateware projects targeting FPGAs.

## Overview

GBS provides a flexible, extensible build infrastructure for hardware
description language (HDL) projects. It supports:

- **Configuration System**: User and tree-level configs with profiles for reusable setups
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

```bash
# Build a project
gbs project build project.gbs.yaml

# Clean build artifacts
gbs project clean

# Show project configuration
gbs project show project.gbs.yaml
```

### Using Configuration

Create `~/.config/gbs.yaml`:

```yaml
tools:
  - name: ghdl
    variant: system
    config:
      executable: ghdl

profiles:
  simulation:
    filter_vars:
      sim: 1
    backends:
      - backend: gbs.backend:GHDLBackend
        config:
          output_dir: build
          vhdl_std: "08"
```

Then reference the profile in your project:

```yaml
name: my_project
topcell: top
output_format: filelist
profile: simulation

root_library:
  name: mylib
  partitions:
    - name: rtl
      sources:
        - language: vhdl
          files: [src/top.vhd]
```

See [`doc/examples/`](doc/examples/) for more configuration examples.

## Documentation

See the `doc/` directory for detailed documentation:

- [GBS Overview](doc/overview.rst) - Project goals and architecture
- [Configuration System](doc/design/configuration.rst) - Complete configuration guide

## Requirements

- Python 3.13+

## Development Status

YMMV

## License

MIT
