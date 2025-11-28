# GBS: Gateware Build System

A build system for gateware projects targeting FPGAs and ASICs.

## Overview

GBS provides a flexible, extensible build infrastructure for hardware description language (HDL) projects. It supports:

- **Configuration System**: User and tree-level configs with profiles for reusable setups
- **Multiple repositories and libraries**: Organize code into logical units
- **Dependency management**: Topological sorting ensures correct build order
- **Conditional source filtering**: Target-specific code selection
- **Pluggable backend architecture**: Support for different toolchains (GHDL, Vivado, etc.)
- **Tool management**: Multiple installations with variant support
- **Asyncio-based execution**: Parallel builds for faster compilation

## Installation

```bash
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

## Quick Start

### Basic Build

```bash
# Build a project
gbs build project.gbs.yaml

# Clean build artifacts
gbs clean

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

- [GBS Overview](doc/gbs_overview.md) - Project goals and architecture
- [Configuration System](doc/plan/configuration_system.md) - Complete configuration guide
- [Configuration Examples](doc/examples/) - Example configs for common scenarios
- [Implementation Plan](doc/plan/implementation_plan.md) - Development roadmap
- [Filter Design](doc/plan/filter_design.md) - Conditional filtering design

### Key Features

**Configuration System**:
- User-wide configs (`~/.config/gbs.yaml`) for tool paths and reusable profiles
- Tree-level configs (`.gbs.yaml`) for project-specific settings
- Profiles for quick setup of common configurations (simulation, synthesis, etc.)
- Tool variants for managing multiple installations (e.g., `ghdl:llvm`, `ghdl:gcc`)
- Automatic repository merging from multiple sources

## Requirements

- Python 3.13+

## Development Status

⚠️ **Alpha** - This project is in early development. API stability is not guaranteed.

## License

MIT
